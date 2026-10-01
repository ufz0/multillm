# AGENTS.md

Single-file Textual app (`agents.py`): N LLM agents with inboxes, talking via a `send_message` tool, streamed live into a terminal UI. No package, no test suite, no CI, no lint/typecheck config.

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
- Input box: plain text → agent1; `@agentN text` → that agent; Ctrl+Q quits
- `run.sh` is just a canned demo prompt, not part of any build/test pipeline
- There is no test runner. Verify by running the app against a live backend and watching the panels/log; transcripts land in `logs/session_*.txt` (gitignored)

## Gotchas

- Agents have a `run_command` tool that executes **arbitrary shell commands on the host machine** (`shell=True`, 60s timeout, output capped at 8k chars). Expect real side effects when the app runs.
- Top-of-file constants encode the deployment: `N_AGENTS = 3` must stay ≤ the llama.cpp slot count (4); `CTX_SLOT = 230400 / 4` assumes a 230k context split across those slots. Adjust both together if the backend changes.
- Pacing guards are intentional: `MAX_MESSAGES` caps agent-to-agent chatter per human message, `MAX_STEPS` caps model calls per wake-up, `trim()` keeps history under `CTX_LIMIT` (estimation is `len(json)//3`, deliberately rough).
