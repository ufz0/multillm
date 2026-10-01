# AGENTS.md

Two frontends over one engine: `agents.py` is the terminal UI (Textual), `web.py` a web UI (Flask + SSE, no auth, vanilla JS in `static/`). N LLM agents with inboxes, talking via a `send_message` tool, streamed live. No package, no test suite, no CI, no lint/typecheck config.

## Setup

- Working venv lives at `./venv` (Python 3.14, deps pinned in `requirements.txt`). Install fresh with `pip install -r requirements.txt` only if missing.
- `.env` is required: `API_KEY`, `API_URL` (any OpenAI-compatible endpoint). `MODEL` optional — falls back to the first entry of `/models`. App exits immediately if `API_URL` is unset.
- Backend: llama.cpp `llama-server` **must** run with `--jinja`, or tool calls break.

## Git workflow

- Never commit directly to `main`. All work lands on `dev`; push `dev` to origin as work progresses.
- When a feature is complete, open a PR from `dev` into `main` (use `gh pr create`).
- Feature branches may be used for isolated work, but they merge into `dev`, not `main`.

## Running / verification

- `venv/bin/python agents.py` → then type into the input box
- `venv/bin/python agents.py -n 4 "task"` → n agents, first task goes to agent1
- `venv/bin/python web.py` → http://127.0.0.1:4321 (override with `WEB_HOST`/`WEB_PORT`); same `-n`/task args
- Input box: plain text → agent1; `@agentN text` → that agent; Ctrl+Q quits
- Web endpoints: `GET /events` (SSE stream of every agent event, replays history on connect), `POST /messages` {to, text}, `GET /health`, `POST /reset` (restarts the session)
- `run.sh` is just a canned demo prompt, not part of any build/test pipeline
- There is no test runner. Verify by running the app against a live backend and watching the panels/log; transcripts land in `logs/session_*.txt` (gitignored)

## Gotchas

- Agents have a `run_command` tool that executes **arbitrary shell commands on the host machine** (`shell=True`, 60s timeout, output capped at 8k chars). Expect real side effects when the app runs.
- `web.py` reuses `agents.py`'s engine in-process (`core.start/shutdown`, shared inboxes and counters): don't run both frontends from one interpreter, and remember `/reset` shuts down and respawns the agent threads.
- The Flask dev server runs threaded for SSE; there's no auth, so keep the bind address local/trusted.
- Top-of-file constants encode the deployment: `N_AGENTS = 3` must stay ≤ the llama.cpp slot count (4); `CTX_SLOT = 230400 / 4` assumes a 230k context split across those slots. Adjust both together if the backend changes.
- Pacing guards are intentional: `MAX_MESSAGES` caps agent-to-agent chatter per human message, `MAX_STEPS` caps model calls per wake-up, `trim()` keeps history under `CTX_LIMIT` (estimation is `len(json)//3`, deliberately rough).
