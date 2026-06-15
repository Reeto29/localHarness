# localHarness

A coding agent I'm building from scratch on top of [Ollama](https://ollama.com), mostly to
understand how these things actually work. No framework, almost all stdlib.

There are two models doing two jobs:

- `gemma4:31b-cloud` runs the loop and does the thinking: planning, reviewing, debugging.
- `qwen2.5-coder` writes the actual code. It gets called one shot at a time with a tight prompt.

The reasoning behind the split, plus the milestone plan, is in [PRD.md](PRD.md).

## Status

v0 works end to end. The cloud model orchestrates, calls tools, and delegates code-writing
to the local coder, then places and runs the result. Try it:

```
python3 main.py
```

Type a task, watch it work, and approve any shell commands it wants to run.

## How it works

The harness is a loop around a model. It sends the conversation plus a list of tools, the
model either answers or asks to call a tool, the loop runs the tool and feeds the result
back, and it repeats until the model answers with no tool call. The growing message list is
the memory.

The coder is exposed to the orchestrator as one more tool (`delegate_to_coder`). It gets a
fresh, self-contained prompt each call and never sees the conversation, which is what keeps
the small model's context small. Full reasoning and the milestone history are in [PRD.md](PRD.md).

## Requirements

- Ollama running locally (`ollama serve`)
- Python 3, nothing to pip install
- `ollama pull qwen2.5-coder`, plus access to a cloud model for the orchestrator

## Layout

| File | What it does |
|---|---|
| `llm.py` | Tiny Ollama HTTP client |
| `tools.py` | The tools the agent can call, and their schemas |
| `agent.py` | The agent loop: dispatch tools, stop when done, gate risky calls |
| `main.py` | The interactive CLI |

## What's next

v0 is the foundation, not the finish line. Before piling on features I want a way to tell
whether a change actually helps, so an eval harness is coming soon: a small set of coding
tasks with tests, run against the agent so each new feature has to earn its place against a
baseline.

After that, the rough priority is context hygiene (read slices, not whole files), a
verify loop (edit, run tests, fix), and diff approval before edits. See [PRD.md](PRD.md).
