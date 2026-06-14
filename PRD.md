# Local Coding Harness — PRD

A from-scratch agentic coding harness running entirely on **Ollama**, built to learn
how these systems work and to have a private, local coding agent.

**Status legend:** ✅ done · 🚧 in progress · ⬜ not started

---

## 1. Goals

- Build a working agentic coding loop on local + cloud Ollama models.
- Understand every layer ourselves — no black-box framework (LangChain, etc.).
- Split roles between a strong **orchestrator/debugger** and a lightweight **coder**.
- Stay dependency-free where reasonable (stdlib HTTP client, no SDK).

## 2. Non-goals (for now)

- No GUI / TUI / VSCode extension — plain CLI only.
- No multi-agent message-passing — one loop, coder is a tool.
- No sandboxing beyond a workspace dir + command confirmation (revisit later).
- Not optimizing for speed/cost yet — correctness and understanding first.

## 3. Models

| Role | Model | Where it runs | Notes |
|---|---|---|---|
| Orchestrator / debugger | `gemma4:31b-cloud` | Ollama cloud (no local RAM) | Owns the loop, plans, reviews, debugs. Carries full history. |
| Coder | `qwen2.5-coder:latest` (7B) | Local, M2 Pro 16GB | Fresh focused prompt each call. Mind Ollama's 4K `num_ctx` default. |

## 4. Architecture

The harness is a **loop** around an LLM:

1. Send the model the conversation history + a list of tools it may use.
2. The model replies with text (done) or a request to call a tool.
3. Our code runs the tool, captures the result, appends it to history.
4. Repeat until the model stops asking for tools.

```
CLI  ──task──▶  AGENT LOOP  ──history+tools──▶  MODELS (llm.py)
                    │                                ▲
                    └──tool call──▶  TOOLS  ─────────┘
                       (delegate_to_coder fires a one-shot chat to the coder)
```

**Two-model split:** the orchestrator runs the loop and owns all tools. The coder is
exposed to it as just another tool, `delegate_to_coder(task)`, which fires a *separate
one-shot* `chat()` to the coder. The coder never sees the conversation history — only a
tight, self-contained task string. This is what keeps the small model's context small.

**Memory:** the growing `messages` list is the memory. Every tool call + result is
appended to it. That accumulation is what makes the system agentic vs. one-shot.

## 5. Files

| File | Responsibility | Status |
|---|---|---|
| `llm.py` | Talk to a model over Ollama's HTTP API (stdlib only) | ✅ |
| `tools.py` | Tool functions + their JSON schemas | ⬜ |
| `agent.py` | The loop: holds history, dispatches tool calls, owns models | ⬜ |
| `main.py` | CLI that reads input and runs the agent | ⬜ |

---

## 6. Milestones

> We edit these as we go — check items off, add detail, reorder as we learn.

### M0 — Talk to a model ✅
- [x] Stdlib Ollama HTTP client (`llm.py`)
- [x] Verified `chat()` and `generate()` return real replies

### M1 — First tool, end-to-end ✅
- [x] `tools.py` with one tool: `read_file`
- [x] Tool JSON schema the model understands
- [x] Prove the model *requests* the tool (`tool_calls` appears in the reply)
- [x] Manually run the requested tool and feed the result back
- **Lesson:** `qwen2.5-coder` advertises `tools` but emits calls as plain-text JSON
  (Ollama can't parse them). `gemma4:31b-cloud` emits native `tool_calls` cleanly.
  → Orchestrator must be the cloud model; coder is only ever called one-shot via
  `generate()`, so its quirk doesn't matter. `gemma4:12b` has no `tools` capability.

### M2 — The agent loop 🚧
- [ ] `agent.py` with the read→tool→append→repeat loop
- [ ] Tool dispatch (map tool name → Python function)
- [ ] Stop condition (model replies with no tool calls)
- [ ] Add core tools: `write_file`, `edit_file`, `list_dir`, `grep`, `run_bash`

### M3 — Two-model split ⬜
- [ ] `delegate_to_coder` tool fires a one-shot `chat()` to the coder
- [ ] Orchestrator reviews/places/debugs coder output
- [ ] Tune `num_ctx` for the coder

### M4 — CLI ⬜
- [ ] `main.py` REPL: type a task, watch the agent work, see results
- [ ] Show tool calls + results as they happen
- [ ] Command confirmation for `run_bash` (safety)

### M5 — Polish (later) ⬜
- [ ] Workspace dir scoping (don't let it wander the filesystem)
- [ ] History trimming / summarization when it grows
- [ ] Config file for models + options
