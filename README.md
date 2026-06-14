# localHarness

A from-scratch agentic coding harness running on [Ollama](https://ollama.com), built to
learn how these systems work end-to-end — no framework, mostly stdlib.

- **Orchestrator / debugger:** `gemma4:31b-cloud` (runs the loop, plans, reviews, debugs)
- **Coder:** `qwen2.5-coder` (writes focused code, called one-shot)

See [PRD.md](PRD.md) for goals, architecture, and the milestone roadmap.

## Status

Early. M0 (talk to a model) and M1 (first tool, end-to-end) done. Building the agent loop next.

## Requirements

- [Ollama](https://ollama.com) running locally (`ollama serve`)
- Python 3 (stdlib only — no pip installs)
- Models: `ollama pull qwen2.5-coder`, plus access to a cloud orchestrator model

## Layout

| File | Responsibility |
|---|---|
| `llm.py` | Minimal Ollama HTTP client (stdlib) |
| `tools.py` | Tool functions + their JSON schemas |
| `agent.py` | The agent loop (planned) |
| `main.py` | CLI (planned) |
