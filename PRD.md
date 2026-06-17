# Local Coding Harness — PRD

A coding agent built from scratch on Ollama. I'm doing it this way to actually understand
how agentic harnesses work, and to end up with a private coding agent I run locally.

**Status legend:** ✅ done · 🚧 in progress · ⬜ not started

---

## 1. Goals

- Get a working coding agent running on a mix of local and cloud Ollama models.
- Understand every layer myself, instead of leaning on something like LangChain.
- Use a strong model to orchestrate and debug, and a small one to write code.
- Avoid dependencies where I reasonably can. The HTTP client is stdlib, no SDK.

## 2. Not doing yet

- No GUI, TUI, or VSCode extension. Plain CLI.
- No two agents talking to each other. One loop, and the coder is just a tool it calls.
- No real sandboxing beyond a working directory and asking before running commands.
- Not worrying about speed or cost yet. I want it correct and legible first.

## 3. Models

| Role | Model | Where it runs | Notes |
|---|---|---|---|
| Orchestrator / debugger | `gemma4:31b-cloud` | Ollama cloud, no local RAM | Runs the loop and holds the full history. |
| Coder | `qwen2.5-coder` (7B) | Local, M2 Pro 16GB | Fresh focused prompt each call. Watch Ollama's 4K `num_ctx` default. |

## 4. How it works

The harness is a loop around a model:

1. Send the model the conversation so far, plus the list of tools it's allowed to use.
2. It replies with either an answer or a request to call a tool.
3. If it asked for a tool, run it, and add the result to the conversation.
4. Go back to step 1. Stop once it answers without asking for a tool.

```
CLI  ──task──▶  AGENT LOOP  ──history+tools──▶  MODELS (llm.py)
                    │                                ▲
                    └──tool call──▶  TOOLS  ─────────┘
                       (delegate_to_coder fires a one-shot chat to the coder)
```

Why two models. The orchestrator runs the loop and owns every tool. The coder is exposed
to it as one more tool, `delegate_to_coder(task)`, which fires a separate one-shot call to
the small model. The coder never sees the running conversation, only a short self-contained
task. That's the whole reason it can stay small: the big model does the thinking and hands
it a tight spec.

Where the memory lives: in the growing `messages` list. Every tool call and its result get
appended to it, and that running history is what the model sees on the next turn. Without it
you'd just have a series of one-shot prompts.

## 5. Files

| File | What it does | Status |
|---|---|---|
| `llm.py` | Tiny Ollama HTTP client | ✅ |
| `tools.py` | Tool functions and their schemas | ⬜ |
| `agent.py` | The loop: history, tool dispatch, picking models | ⬜ |
| `main.py` | CLI that takes a task and runs the agent | ⬜ |

---

## 6. Milestones

> I edit these as I go: check things off, add notes, reorder when I learn something.

### M0 — Talk to a model ✅
- [x] Stdlib Ollama HTTP client (`llm.py`)
- [x] Confirmed `chat()` and `generate()` return real replies

### M1 — First tool, end to end ✅
- [x] `tools.py` with one tool, `read_file`
- [x] A tool schema the model understands
- [x] Confirmed the model actually asks for the tool (`tool_calls` shows up)
- [x] Ran the tool by hand and fed the result back
- **What I learned:** `qwen2.5-coder` claims `tools` support but writes the call as plain
  JSON text, which Ollama won't parse. `gemma4:31b-cloud` returns proper `tool_calls`. So
  the orchestrator has to be the cloud model. The coder only ever gets called one shot via
  `generate()`, so its quirk doesn't matter. `gemma4:12b` has no `tools` support at all.

### M2 — The agent loop ✅
- [x] `agent.py` with the read → run tool → append → repeat loop
- [x] Dispatch: map a tool name to its Python function
- [x] Stop when the model replies with no tool calls
- [x] MAX_STEPS safety cap + try/except around tool calls
- [x] Added the rest: `write_file`, `edit_file`, `list_dir`, `grep`, `run_bash`
- **Note:** `run_bash` runs unguarded for now. The "ask before running" confirmation
  lands in M3 with the CLI, since it needs the interactive layer.

### M3 — CLI ✅
- [x] `main.py`: type a task, watch it work, see the result
- [x] Print tool calls and results as they happen
- [x] Ask before running anything via `run_bash` (confirm callback in `agent.run`)
- [x] Deny path: model is told the command was declined, keeps going

### M4 — Two-model split ✅
- [x] `delegate_to_coder` fires a one-shot call to the coder (`generate()`)
- [x] Orchestrator system prompt tells it to delegate code, then place/review it
- [x] `num_ctx` set to 8192 for the coder
- [x] Verified end to end: orchestrator specced FizzBuzz → coder wrote it →
  orchestrator placed and ran it

**v0 done.** The harness plans with the cloud model and writes code with the local one.

### M5 — Later
- [ ] Keep it inside a working directory so it can't wander the filesystem
- [ ] Trim or summarize history when it gets long
- [ ] Config file for models and options
- [ ] Sandboxing: revisit `run_bash` using `shell=True`. Fine for local/trusted use
  (the confirm prompt is the guard), but a real sandbox is needed before untrusted use.

---

## 7. After v0 — toward a genuinely good harness

The plan stays as built: keep the two-model split (cloud orchestrator + local coder). The
question now is what to add next, and how to know it actually helped. So evals come before
the feature pile.

### M6 — Eval baseline (do this first)
Establish a number so every later feature has to earn its place against a baseline.
- [x] Surface token/timing counts from `llm.py` (`chat()` now returns the full body;
  `prompt_eval_count` / `eval_count` are available).
- [x] `agent.run()` returns `(answer, metrics)` — steps, per-tool ok/err, token counts,
  stopped_reason. CLI prints a steps+tokens line per task.
- [x] `bench/` (stdlib only): `runner.py` drives `agent.run()` over 7 handmade Python
  tasks, each with a `test.sh` that exits 0 on pass.
- [x] Each task runs in a temp workspace the runner chdirs into, so auto-confirm is safe.
  (Not airtight: model could escape with an absolute path or `../`. Fine for handmade tasks.)
- [x] Commit `bench/scores.csv` (one row per run); gitignore `bench/results/`.
- [x] Baseline recorded: **7/7 passed, 36 steps, 56.8k tokens.**

**Key finding:** pass-rate is saturated — gemma one-shots every toy task, including the
multi-bug debug and duplicate-line edit. So **efficiency (tokens, steps) is the primary
metric** for now; pass-rate is just a regression guard. This fits the near-term work:
context hygiene is measured in tokens, not pass-rate. Moving pass-rate again needs
real-world-hard tasks (polyglot / SWE-bench), deferred until the harness is competitive.

### M7 — Tier 1 features (measured against M6)
Rough order reflects dependency + leverage.
- [ ] **Context hygiene** — read file slices not whole files; truncate tool output
  (keep head+tail). Do before the verify loop, since verification floods context with logs.
- [ ] **Verify loop** — edit → run tests → read failure → fix. Biggest quality multiplier.
- [ ] **Diff approval before edits** — reuse the `confirm` callback; `edit_file` returns a
  unified diff for the gate.

### M8 — Tier 2 (later)
- [ ] ripgrep-based search (regex, fast, respects `.gitignore`)
- [ ] project memory file injected on start
- [ ] session persistence, then todo/plan mode

### Open questions
- Relax stdlib-only for ripgrep / a repo-map (tree-sitter), or ship stdlib-lite versions
  (e.g. `ast` outline for Python + grep elsewhere)?
- `delegate_to_subagent` (fresh cloud sub-loop for context isolation) — build only when
  single tasks routinely blow the context budget. Not speculative.
