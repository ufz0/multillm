"""Multi-agent playground: N agents with inboxes, talking via a send_message tool.
Streams every agent's output live into a terminal UI.

Setup:  pip install openai python-dotenv textual
.env:   API_KEY=..., API_URL=... (optional: MODEL)
Run:    python agents.py                      (then type into the input box)
        python agents.py -n 4 "your task"     (4 agents, first task goes to agent1)
Keys:   Enter = send to agent1, "@agent2 text" = send to agent2, Ctrl+Q = quit
Tools:  send_message(to, text), run_command(command)  (60s timeout)
Log:    every message is appended to logs/session_DD_MM_YYYY_HH_MM.txt
llama-server must run with --jinja for tool calls.
"""
import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time

from dotenv import load_dotenv
from openai import OpenAI
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, Input, RichLog, Static

load_dotenv()
if not os.environ.get("API_URL"):
    sys.exit("API_URL is not set - add it to .env (base URL of an OpenAI-compatible endpoint)")
client = OpenAI(
    base_url=os.environ["API_URL"],
    api_key=os.environ["API_KEY"],
)
MODEL = os.getenv("MODEL")  # if unset, the first model of the endpoint is used

N_AGENTS = 3          # keep <= number of llama.cpp slots (4)
MAX_MESSAGES = 100     # agent-to-agent messages per human message before sending is refused
MAX_STEPS = 8         # model calls per wake-up
CTX_SLOT = 57600      # 230400 / 4 slots
CTX_LIMIT = 45000     # trim an agent's history above this (approx. tokens)
MAX_PANEL_CHARS = 6000
CMD_TIMEOUT = 60      # hard timeout per run_command call (seconds)
MAX_CMD_OUT = 8000    # cap on command output kept for the model
LOG_DIR = "logs"      # plaintext session logs live here

COLORS = ["cyan", "magenta", "yellow", "blue"]

SYSTEM = """You are {me}. Other agents: {others}. The user is called "human".
The human only ever sees messages you send via send_message(to="human"). Your plain text goes nowhere and is invisible to the human and to other agents - treat it as scratch space, and route anything they should read through send_message.
Use the run_command tool to check actual state instead of assuming; prefer read-only commands.
Only send a message if you have something to add; do not reply just to say thanks or acknowledge.
When the task is done, one agent sends the final result to "human"."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "Send a message to another agent or to 'human'. This is the only way anything reaches the human.",
            "parameters": {
                "type": "object",
                "properties": {"to": {"type": "string"}, "text": {"type": "string"}},
                "required": ["to", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command on the local machine and receive its combined output. Use to check real-world state instead of guessing.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "Shell command line to execute"}},
                "required": ["command"],
            },
        },
    },
]

names = []
inboxes = {}
pending = 0           # messages delivered but not yet fully processed
sent = 0              # agent-to-agent messages since the last human message
lock = threading.Lock()
log_lock = threading.Lock()
session_log = None        # file handle for this run's session log, set by start()
events = queue.Queue()  # agent threads -> UI
threads = []            # agent worker threads, set by start(); other frontends (web.py) reuse all of the above


def setup(n):
    global names, inboxes
    names = [f"agent{i}" for i in range(1, n + 1)]
    inboxes = {k: queue.Queue() for k in names}


def emit(kind, agent, *data):
    events.put((kind, agent, data))


def new_session_log():
    global session_log
    os.makedirs(LOG_DIR, exist_ok=True)
    t = time.localtime()
    path = os.path.join(
        LOG_DIR,
        f"session_{t.tm_mday:02d}_{t.tm_mon:02d}_{t.tm_year:04d}"
        f"_{t.tm_hour:02d}_{t.tm_min:02d}.txt",
    )
    session_log = open(path, "a", encoding="utf-8")
    return path


def log_line(sender, to, text):
    if session_log is None:
        return
    with log_lock:
        session_log.write(f"[{time.strftime('%H:%M:%S')}] {sender} -> {to}: {text}\n")
        session_log.flush()


def announce(sender, to, text):
    """Record one communication in the session log, then broadcast it to the UI."""
    log_line(sender, to, text)
    emit("msg", None, sender, to, text)


def color_of(who):
    return COLORS[names.index(who) % len(COLORS)] if who in names else "green"


# ---------- engine ----------

def deliver(sender, to, text):
    global pending, sent
    if to == "human":
        announce(sender, to, text)
        return "delivered to human"
    if to == sender or to not in inboxes:
        return f"error: unknown recipient '{to}'. Valid: {', '.join(n for n in names if n != sender)}, human"
    with lock:
        if sent >= MAX_MESSAGES:
            return "error: message limit reached, stop sending messages"
        sent += 1
        pending += 1
    announce(sender, to, text)
    inboxes[to].put(f"[from {sender}] {text}")
    return "sent"


def human_send(to, text):
    global pending, sent
    with lock:
        sent = 0
        pending += 1
    announce("human", to, text)
    inboxes[to].put(f"[from human] {text}")


def execute_command(sender, command):
    if not command.strip():
        msg = "error: missing 'command'"
        emit("call", sender, f"x {msg}")
        return msg
    emit("call", sender, f"$ {command}")
    try:
        p = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=CMD_TIMEOUT)
    except subprocess.TimeoutExpired:
        msg = f"error: timed out after {CMD_TIMEOUT}s"
        emit("call", sender, f"x {msg}")
        return msg
    out = (p.stdout or "") + (f"\n[stderr]\n{p.stderr}" if p.stderr.strip() else "")
    if len(out) > MAX_CMD_OUT:
        out = out[:MAX_CMD_OUT] + "\n... [truncated]"
    if p.returncode != 0:
        emit("call", sender, f"x exit code {p.returncode}")
        return f"{out}\nexit code {p.returncode}".strip()
    preview = out[:1000]
    if preview.strip():
        emit("callout", sender, preview + ("..." if len(out) > 1000 else ""))
    return out.strip() or "(no output)"


def run_tool(sender, call):
    try:
        args = json.loads(call["arguments"] or "{}")
        if call["name"] == "send_message":
            to, text = str(args["to"]), str(args["text"])
            result = deliver(sender, to, text)
            emit("call", sender, f"-> {to}: {text}" if not result.startswith("error") else f"x {result}")
            return result
        if call["name"] == "run_command":
            return execute_command(sender, str(args.get("command", "")))
        return f"error: unknown tool {call['name']}"
    except Exception as e:
        emit("call", sender, f"x bad tool call: {e}")
        return f"error: {e}"


def est_tokens(messages):
    return len(json.dumps(messages)) // 3


def trim(messages):
    # drop oldest turns, always restarting at a user message so tool results are never orphaned
    while est_tokens(messages) > CTX_LIMIT and len(messages) > 2:
        messages.pop(1)
        while len(messages) > 1 and messages[1]["role"] != "user":
            messages.pop(1)


def stream_completion(name, messages):
    stream = client.chat.completions.create(
        model=MODEL, messages=messages, tools=TOOLS, temperature=0.7,
        stream=True, stream_options={"include_usage": True},
    )
    content = ""
    calls = {}  # index -> {"id", "name", "arguments"}
    for chunk in stream:
        if getattr(chunk, "usage", None):
            emit("ctx", name, chunk.usage.prompt_tokens)
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            emit("think", name, reasoning)
        if delta.content:
            content += delta.content
            emit("text", name, delta.content)
        for tc in delta.tool_calls or []:
            c = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
            if tc.id:
                c["id"] = tc.id
            if tc.function:
                if tc.function.name:
                    c["name"] += tc.function.name
                if tc.function.arguments:
                    c["arguments"] += tc.function.arguments
            emit("status", name, "writing tool call")
    result = [calls[i] for i in sorted(calls)]
    for i, c in enumerate(result):
        c["id"] = c["id"] or f"call_{int(time.time() * 1000)}_{i}"
    return content, result


def agent(name):
    global pending
    others = ", ".join(n for n in names if n != name)
    messages = [{"role": "system", "content": SYSTEM.format(me=name, others=others)}]
    while True:
        incoming = inboxes[name].get()
        if incoming is None:      # stop sentinel from shutdown()
            return
        messages.append({"role": "user", "content": incoming})
        emit("wake", name, incoming)
        try:
            for _ in range(MAX_STEPS):
                trim(messages)
                emit("status", name, "thinking")
                content, calls = stream_completion(name, messages)
                entry = {"role": "assistant", "content": content}
                if calls:
                    entry["tool_calls"] = [
                        {"id": c["id"], "type": "function",
                         "function": {"name": c["name"], "arguments": c["arguments"]}}
                        for c in calls
                    ]
                messages.append(entry)
                if not calls:
                    break
                for c in calls:
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": run_tool(name, c)})
            emit("status", name, "idle")
        except Exception as e:
            emit("error", name, str(e))
        finally:
            with lock:
                pending -= 1


# ---------- UI ----------

class AgentsApp(App):
    TITLE = "agents"
    CSS = """
    #agents { height: 1fr; }
    .panel { width: 1fr; border: round $primary; padding: 0 1; }
    #log { height: 10; border: round $secondary; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    """
    BINDINGS = [("ctrl+q", "quit", "Quit")]

    def __init__(self, first_task=None):
        super().__init__()
        self.first_task = first_task
        self.bufs = {n: [] for n in names}    # per agent: list of [style, text]
        self.sizes = {n: 0 for n in names}
        self.state = {n: "idle" for n in names}
        self.ctx = {n: 0 for n in names}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="agents"):
            for n in names:
                with VerticalScroll(id=n, classes="panel"):
                    yield Static(id=f"{n}-text")
        yield RichLog(id="log", wrap=True, markup=False, highlight=False)
        yield Static(id="status")
        yield Input(placeholder="Message agent1, or '@agent2 text'  (Ctrl+Q quits)")
        yield Footer()

    def on_mount(self):
        for n in names:
            self.retitle(n)
        self.set_interval(0.05, self.drain)
        self.query_one(Input).focus()
        if self.first_task:
            human_send("agent1", self.first_task)

    def retitle(self, n):
        panel = self.query_one(f"#{n}")
        panel.border_title = f"[{color_of(n)}]{n}[/] - {self.state[n]}"
        panel.border_subtitle = f"ctx {self.ctx[n] / 1000:.1f}k / {CTX_SLOT / 1000:.1f}k" if self.ctx[n] else ""

    def add(self, n, style, text):
        segs = self.bufs[n]
        if segs and segs[-1][0] == style:
            segs[-1][1] += text
        else:
            segs.append([style, text])
        self.sizes[n] += len(text)
        while self.sizes[n] > MAX_PANEL_CHARS and len(segs) > 1:
            self.sizes[n] -= len(segs.pop(0)[1])
        if self.sizes[n] > MAX_PANEL_CHARS:
            segs[0][1] = segs[0][1][-MAX_PANEL_CHARS:]
            self.sizes[n] = len(segs[0][1])

    def redraw(self, n):
        self.query_one(f"#{n}-text", Static).update(Text.assemble(*[(t, s) for s, t in self.bufs[n]]))
        self.call_after_refresh(self.query_one(f"#{n}").scroll_end, animate=False)
        self.retitle(n)

    def log_msg(self, sender, to, text):
        line = Text.assemble(
            (time.strftime("%H:%M:%S "), "dim"),
            (sender, f"bold {color_of(sender)}"), " -> ",
            (to, f"bold {color_of(to)}"), ": ",
            (text, "bold" if to == "human" else ""),
        )
        self.query_one("#log", RichLog).write(line)

    def drain(self):
        dirty = set()
        while True:
            try:
                kind, agent_name, data = events.get_nowait()
            except queue.Empty:
                break
            if kind == "msg":
                self.log_msg(*data)
                continue
            if kind == "wake":
                self.add(agent_name, "bold yellow", f"\n> {data[0]}\n")
            elif kind == "think":
                self.add(agent_name, "dim italic", data[0])
            elif kind == "text":
                self.add(agent_name, "", data[0])
            elif kind == "call":
                self.add(agent_name, "bold cyan", f"\n{data[0]}\n")
            elif kind == "callout":
                self.add(agent_name, "dim", data[0])
            elif kind == "status":
                self.state[agent_name] = data[0]
            elif kind == "ctx":
                self.ctx[agent_name] = data[0]
            elif kind == "error":
                self.add(agent_name, "bold red", f"\n!! {data[0]}\n")
                self.state[agent_name] = "error"
            dirty.add(agent_name)
        for n in dirty:
            self.redraw(n)
        with lock:
            busy, count = pending, sent
        self.query_one("#status", Static).update(
            f"{'working' if busy else 'idle'} | {busy} message(s) in flight | "
            f"agent messages {count}/{MAX_MESSAGES}"
        )

    def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        to = "agent1"
        if text.startswith("@"):
            head, _, rest = text.partition(" ")
            if head[1:] in inboxes and rest.strip():
                to, text = head[1:], rest.strip()
        human_send(to, text)


def start(n):
    """Create the inbox set, open a session log and spawn one worker thread per agent.

    Shared by the terminal UI (main) and any other frontend, e.g. web.py.
    """
    global MODEL
    if not MODEL:
        try:
            MODEL = client.models.list().data[0].id
        except Exception as e:
            sys.exit(f"could not get model list from {client.base_url}: {e}\nset MODEL in .env")
    setup(n)
    print(f"session log: {new_session_log()}")
    for nm in names:
        t = threading.Thread(target=agent, args=(nm,), daemon=True)
        t.start()
        threads.append(t)


def shutdown():
    """Send each agent a stop sentinel and wait for the workers to finish."""
    for nm in names:
        inboxes[nm].put(None)
    for t in threads:
        t.join(timeout=CMD_TIMEOUT + 5)
    threads.clear()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=N_AGENTS, help="number of agents")
    parser.add_argument("task", nargs="*", help="optional first task for agent1")
    args = parser.parse_args()
    start(args.n)
    AgentsApp(" ".join(args.task) or None).run()


if __name__ == "__main__":
    main()