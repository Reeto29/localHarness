# localHarness

A coding agent I'm building from scratch on top of [Ollama](https://ollama.com), mostly to
understand how these things actually work. No framework, almost all stdlib.

There are two models doing two jobs:

- `gemma4:31b-cloud` runs the loop and does the thinking: planning, reviewing, debugging.
- `qwen2.5-coder` writes the actual code. It gets called one shot at a time with a tight prompt.

The reasoning behind the split, plus the milestone plan, is in [PRD.md](PRD.md).

## Status

v0 works. The cloud model orchestrates and the local coder writes the code. Run it with
`python3 main.py`, type a task, and approve any shell commands it wants to run.

## Requirements

- Ollama running locally (`ollama serve`)
- Python 3, nothing to pip install
- `ollama pull qwen2.5-coder`, plus access to a cloud model for the orchestrator

## Layout

| File | What it does |
|---|---|
| `llm.py` | Tiny Ollama HTTP client |
| `tools.py` | The tools the agent can call, and their schemas |
| `agent.py` | The loop (not written yet) |
| `main.py` | The CLI (not written yet) |
