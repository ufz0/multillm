# multillm

Multi-agent playground: N LLM agents with inboxes, talking to each other through a `send_message` tool, while every agent's output streams live into a terminal UI.

Built around any OpenAI-compatible endpoint, primarily a local llama.cpp `llama-server` (must run with `--jinja` for tool calls).

## Setup

```sh
python -m venv venv
pip install -r requirements.txt
```

Create a `.env` next to `agents.py`:

```
API_KEY=sk-anything
API_URL=http://127.0.0.1:8080/v1
MODEL=qwen3.8:27b   # optional; defaults to first model on the endpoint
```

## Run

```sh
venv/bin/python agents.py                        # 3 agents, type into the input box
venv/bin/python agents.py -n 4 "your task"       # 4 agents, task goes to agent1
```

Input box: plain text goes to `agent1`, `@agent2 text` targets agent2, Ctrl+Q quits. Agents can also run shell commands locally via the `run_command` tool (60s timeout) and check real state instead of guessing. Every message lands in `logs/session_*.txt`.

## Notes

- Pacing guards keep things sane: per-human-message caps on agent chatter (`MAX_MESSAGES`), model calls per wake-up (`MAX_STEPS`), and history trimming under a context budget (`CTX_LIMIT`).
- `N_AGENTS` and `CTX_SLOT` assume 4 llama.cpp slots with a 230k context split across them; adjust both if your backend differs.

## AI usage disclaimer

A local instance of qwen3.8:27b was used to develop this project to test out its capabilities. It was mainly used for automating annoying tasks.
