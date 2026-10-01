"""Web UI for the agent playground: Flask + server-sent events, no auth.

Reuses the same engine as agents.py (inboxes, tools, pacing guards,
session logs) - this file only adds an HTTP front end: a page, a few
JSON endpoints and an SSE stream of every agent event.

Run:   venv/bin/python web.py                 -> http://127.0.0.1:4321
       venv/bin/python web.py -n 4 "task"     (4 agents, first task goes to agent1)
Env:   WEB_HOST, WEB_PORT (defaults 127.0.0.1 : 4321)
Notes: no auth by design; keep it on localhost or a trusted network.
       The dev server runs threaded so many SSE connections can coexist.
"""
import argparse
import collections
import json
import os
import queue
import sys
import threading

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_from_directory

load_dotenv()
if not os.environ.get("API_URL"):
    sys.exit("API_URL is not set - add it to .env (base URL of an OpenAI-compatible endpoint)")

import agents as core  # noqa: E402  (engine lives here; importing also creates the OpenAI client)

REPLAY_LIMIT = 2000    # how many past events a (re)connecting client receives

app = Flask(__name__)

# ---------- event fan-out ----------

subs = []              # one queue.Queue per connected SSE client
sub_lock = threading.Lock()
replay = collections.deque(maxlen=REPLAY_LIMIT)


def sse(ev):
    return f"data: {json.dumps(ev)}\n\n"


def publish(kind, agent, *data):
    """Put one event on the replay buffer and into every subscriber queue."""
    ev = [kind, agent, list(data)]
    with sub_lock:
        replay.append(ev)
        for q in subs:
            q.put_nowait(ev)


def pump():
    """Drain the engine's event queue and re-broadcast it."""
    while True:
        kind, agent, data = core.events.get()
        publish(kind, agent, *data)


# ---------- routes ----------

@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/health")
def health():
    with core.lock:
        inflight, sent = core.pending, core.sent
    return jsonify(
        ok=True,
        agents=core.names,
        model=core.MODEL,
        in_flight=inflight,
        sent=sent,
        ctx_slot=core.CTX_SLOT,
        max_messages=core.MAX_MESSAGES,
    )


@app.post("/messages")
def messages():
    b = request.get_json(silent=True) or {}
    to, text = str(b.get("to", "")), str(b.get("text", "")).strip()
    if to == "human":
        return jsonify(error="you are human - pick one of the agents"), 400
    if to not in core.inboxes:
        return jsonify(error=f"unknown recipient '{to}'. Valid: {', '.join(core.names)}"), 400
    if not text:
        return jsonify(error="message is empty"), 400
    core.human_send(to, text)
    return jsonify(ok=True)


_reset_lock = threading.Lock()


@app.post("/reset")
def reset():
    if not _reset_lock.acquire(blocking=False):
        return jsonify(error="already resetting"), 409
    try:
        n = len(core.names)
        core.shutdown()                 # wait for workers to drain their current step
        publish("reset", None)          # clients wipe their panels before new events arrive
        core.start(n)
        return jsonify(ok=True, agents=core.names)
    finally:
        _reset_lock.release()


@app.get("/events")
def events():
    def gen():
        q = queue.Queue()
        with sub_lock:
            seed = list(replay)         # replay first, then live; keeps reconnects lossless-ish
            subs.append(q)
        try:
            yield sse(["hello", None, [core.names]])
            for ev in seed:
                yield sse(ev)
            while True:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    yield ": heartbeat\n\n"   # keep proxies and clients honest
                    continue
                yield sse(ev)
        finally:
            with sub_lock:
                subs.remove(q)

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- main ----------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", type=int, default=core.N_AGENTS, help="number of agents")
    parser.add_argument("task", nargs="*", help="optional first task for agent1")
    args = parser.parse_args()
    host = os.environ.get("WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("WEB_PORT", "4321"))
    core.start(args.n)
    threading.Thread(target=pump, daemon=True).start()
    if args.task:
        core.human_send("agent1", " ".join(args.task))
    print(f"web ui: http://{host}:{port}")
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
