# Notes

Running log of what I've tried on this harness and what I learned from each attempt.
Newest stuff at the bottom. I update this as I go.

---

## Methods tried

### 1. Two-model split: cloud orchestrator + local coder (v0)

The original idea. gemma4:31b-cloud runs the loop and plans, qwen2.5-coder (7B, local)
writes code via a `delegate_to_coder` tool. The coder gets a fresh one-shot prompt each
time so its context stays tiny.

**Takeaway:** worked end to end on toy tasks. But the split has a hidden tax: to delegate,
the orchestrator has to write a fully self-contained spec plus a verify command that only
exits 0 when the code is right. For anything non-trivial, writing that spec is about as
hard as writing the code. So the big model does the thinking twice and I pay latency for
the small model anyway.

### 2. Eval bench before features (M6)

Built bench/ with 7 handmade tasks, each with a test.sh. Runner drops the agent into a
temp dir, runs it, checks the test. Baseline: 7/7, 36 steps, 56.8k tokens.

**Takeaway:** best decision so far. Pass rate saturated immediately (gemma one-shots every
toy task), so tokens and steps became the real metric. Having a number to point at also
kills a lot of second-guessing about whether a change helped.

### 3. Verify-and-retry inside the coder

Gave `delegate_to_coder` a target_file and verify_command. The harness writes the code,
runs the check, feeds errors back to the coder, up to 3 tries. Coder returns only a
summary so the orchestrator's context stays small.

**Takeaway:** the loop itself is right (every serious harness does edit → test → fix).
But keeping the orchestrator code-blind backfired on hard tasks: when verification fails
3 times, all it can do is re-delegate roughly the same spec at temperature 0, which
produces roughly the same broken code. Thrash.

### 4. Swapping the coder for a reasoning model

qwen2.5-coder couldn't handle the harder stuff, so I tried a Qwen3.5 9B reasoning distill.
Had to add think-tag stripping because it dumps `<think>` blocks before the code.

**Takeaway:** a reasoning coder undermines the original argument for the split. The whole
point was a small fast worker. If the worker needs 30-55s to think, I've built a slow
two-model system instead of a fast one.

### 5. A genuinely hard task: expr_eval

Two-file infix expression evaluator (tokenizer + parser, precedence, unary minus, error
cases). Added because the original 7 tasks stopped discriminating.

**Takeaway:** first run: 0/1, 20 steps, 141k tokens. For comparison, all 7 easy tasks
together cost 25-57k. Went back and looked at where the tokens went: 135k prompt vs 6.2k
output. The loop resends the entire history every step with zero trimming, so cost grows
quadratically. Most of those 141k tokens were the harness re-reading its own transcript.

### 6. New hardware, new coder: gpt-oss:20b local (M4, 24GB)

Upgraded machines, so a 13GB model fits now. Pulled gpt-oss:20b and made it the coder.

**Takeaway:** as a coder it's good. Clean one-shot gcd with verification passing on
attempt 1, about 9s. No think-tag leakage. The reasoning-strip helpers handle it fine.

### 7. Single model doing everything (gpt-oss as orchestrator too)

If the split is questionable and I have RAM for a 20B, why not one local model for the
whole loop. Tried it on expr_eval.

**Takeaway:** died at step 0. Ollama returned HTTP 500 "error parsing tool call" when the
model emitted a run_bash with a heredoc inside. Turns out this is a documented Ollama +
gpt-oss bug family (issues #12064, #11800, #12884): the harmony format doesn't constrain
tool-call JSON, so multi-line args come out invalid and the parser rejects the whole turn.
Also learned my loop has no try/except around the chat call, so one bad tool call kills
the entire run. 19 minutes, 0 tokens recorded. Painful but clarifying.

### 8. Phase 0: make runs survivable and measurable

Fixed the plumbing before adding features. chat() errors now feed back to the model
instead of killing the run (with a 3-strikes cap, and unreachable-server errors abort
immediately since retrying those is pointless). agent.run takes soft wall/token budgets.
The runner executes each task in a child process with a hard timeout as backstop.
scores.csv went from one aggregate row per run to one row per (task, config, run) with
wall-clock and a config label; the old rows live in scores_v0.csv. Coder tokens finally
get counted. CONFIGS dict + --config flag replaced both throwaway experiment scripts.
Ran a strict code review over the diff and it caught real stuff: I was parsing my own
run_bash output ("exit 0...") as strings to detect verify success instead of returning a
returncode; the bench config mutated agent globals instead of passing arguments; the
token budget ignored coder tokens; the coder retry prompt could overflow its own 8192
context and cut off the task text.

**Takeaway:** smoke test after: split does fizzbuzz in 16.2s (2 steps, 2133+276 orch,
223+289 coder), gemma-direct does it in 3.0s. First honest per-task numbers, and the
first hint of what the coder round-trip actually costs on easy tasks. Also: the review
was worth it purely for the exit-0 string parsing catch. Measure twice.

---

## Things I keep thinking about

- Does the split even earn its keep? Anthropic's guidance is to only add orchestration
  complexity when it demonstrably beats the simple loop. mini-swe-agent gets 65%+ on
  SWE-bench with one model, one bash tool, and no function calling at all. I haven't
  actually proven the split beats a single model on my own bench. That experiment is
  overdue.

- JSON tool calling might be the wrong interface for local models entirely. The
  mini-swe-agent trick: don't pass tools at all. Model replies with one fenced bash block,
  harness parses it with a regex, runs it, feeds output back. Heredocs become harmless
  because the command never gets JSON-escaped. A 20B model has seen millions of bash
  blocks in training and almost no Ollama tool-call JSON. Asking the model nicely to
  avoid heredocs is a stopgap; this feels like the actual fix.

- Context is the budget and I'm not managing it. Every tool result enters history
  verbatim and stays forever. read_file has no size cap (run_bash does, 6k chars, but it
  cuts the tail, which is where the actual error in a traceback lives). Fix order from the
  research: truncate everything at one choke point in the loop, then stub out stale tool
  results, then think about real compaction.

- ~~My metrics have been lying to me~~ fixed in phase 0: coder tokens counted, wall-clock
  recorded, hung tasks killable. A/B comparisons are trustworthy now.

- Task design matters as much as harness design. expr_eval is greenfield synthesis with
  16 asserts in one script, which is the hardest possible shape for a small model and
  gives zero partial credit. The SWE-bench shape is the opposite: working starter code,
  seeded bugs, failing tests that pass after the fix. Should build the medium tier that
  way instead of writing bigger greenfield tasks.

- Watch num_ctx. The orchestrator path never sets it, so Ollama truncates old tokens
  first when history grows, and the system prompt is the first thing to go. Some of what
  looked like the model getting dumber late in a run was probably this.

---

## Next up

Working plan (phases, biggest wins first):

1. ~~Make runs survivable~~ done, see method 8.
2. Context hygiene: one truncation choke point, read_file slices, grep cap, stale-result
   stubbing.
3. Text-action mode (the mini-swe-agent protocol), then the real experiment: split vs
   single-gptoss vs single-gemma, 3 runs each, on the bench.
4. Orchestrator-level verify loop plus an interface contract (PLAN.md with exact shared
   signatures) for multi-file tasks.
5. Rebuild the middle of the bench with seeded-bug tasks.
