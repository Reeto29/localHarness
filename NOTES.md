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

### 9. Phase 1: context hygiene

One clip() choke point in the loop (head+tail, so traceback tails survive), read_file
line slices, grep capped at 50 hits, and stale-result stubbing: tool outputs older than
the last 2 assistant turns go to the API as one-line stubs while local history stays
complete. Also started logging prompt tokens per step so growth is visible. The review
pass caught that the coder's verify-failure feedback was still head-only truncated
(check[:800]) — the error line at the end of the traceback was being cut in exactly the
path where retries need it.

**Takeaway:** ran the full 8-task bench and expr_eval PASSED for the first time: 3 steps,
~12.4k tokens total (5.5k orch + 6.9k coder), 107s. The only prior attempt was 0/1 at
141k tokens, hard-killed after 15+ minutes. Prompt-per-step curve on it: 1173 → 1447 →
1953, gentle and linear instead of quadratic. Whole suite: 8/8, 42.8k tokens including
coder, 204s wall. Caveat I shouldn't forget: this isn't a clean A/B for context hygiene
alone — the coder swap to gpt-oss and the delegate verify loop landed since the 141k run,
so several variables moved. n=1 too. But the direction is unambiguous and now every
future change can be measured properly.

### 10. gpt-oss:20b solo, no orchestrator (second try)

Same experiment that died at step 0 last time, rerun with phase 0+1 in place.

**Takeaway:** 7/8 passed, ~216s wall, fully local. expr_eval passed solo (9 steps, 31.8k
tokens, 67.5s — costlier in tokens than the split's 12.4k but faster in wall clock and
zero cloud). The 500 parse errors still happen: fix_suite hit 3 in a row and the loop
gave up, but the code already written passed the tests, so the run scored PASS with
agent_status=llm_error. That's the phase 0 design paying off — recover, then grade
whatever's left. Only multi_file failed. So the split still wins on my metrics (8/8,
fewer tokens), but no-orchestrator went from catastrophically broken to one task short.
The text-action protocol (phase 2) should close the tool-call gap.

### 11. Coder bake-off: two 9B reasoning distills vs gpt-oss

Swapped the coder seat in the split for aravhawk/qwen3.5-opus-4.6:9b, then
pdurugyan/qwen3.5-9b-deepseek-v4-flash. Same harness, same limits, same orchestrator.

**Takeaway:** both lose, and the CSV shows exactly why. Not context bloat — orchestrator
tokens were identical to the champion run and coder prompts were tiny (433 tokens in).
The distills just think too much. The opus distill is a situational overthinker: terse
and correct on easy tasks (7/8, 53.9k tokens, but 625s wall), then 14.8k output tokens
of chain-of-thought on expr_eval until the budget killed it. The DeepSeek distill is a
pathological one: 15-16k output tokens on EVERYTHING, including a string replacement —
it overran its own 8k context mid-monologue and forgot the start of its own reasoning.
Killed the run at 5/8 tasks (2 passes). Lesson: "reasoning-distilled" means trained to
always emit long CoT, which is the exact opposite of the coder seat's job description.
gpt-oss:20b keeps the seat: MoE (3.6B active) makes it fast, and it just writes the code.

### 12. Coder bake-off round 2: the non-thinking specialists

qwen2.5-coder:14b (dense, the v0 coder's big brother) and deepseek-coder-v2:16b
(MoE, 2.4B active — the champion's shape) in the coder seat.

**Takeaway:** the champion survives everything. qwen14b went 8/8 — the only other
config ever to sweep — and on the easy 7 it's as fast as gpt-oss or faster. But its
expr_eval first drafts were weak, so the orchestrator ground through 8 steps of
retry (22k orch tokens on one task), landing at 69.7k total vs the champ's 42.8k.
dscoder16b showed a brand-new failure mode: high-velocity thrash. MoE speed plus
weak drafts meant 18 steps on expr_eval in 238s, ~111k tokens on one task, first
run ever killed by the token budget instead of the wall clock. Fast and wrong is
just expensive, quickly. Full ladder after five coder-seat candidates: gpt-oss
42.8k > qwen14b 69.7k (both 8/8) > everything else. The seat's job description,
confirmed twice over: non-thinking, fast, and good enough on the first draft that
the orchestrator doesn't have to loop.

---

## Things I keep thinking about

- Does the split even earn its keep? First real data point (method 10): split 8/8 at
  42.8k tokens vs solo gpt-oss 7/8 at ~60k. Split wins today, but the solo run's losses
  were tool-call formatting, not reasoning — the text-action protocol could flip this.
  Needs 3 runs each before believing it either way.

- JSON tool calling might be the wrong interface for local models entirely. The
  mini-swe-agent trick: don't pass tools at all. Model replies with one fenced bash block,
  harness parses it with a regex, runs it, feeds output back. Heredocs become harmless
  because the command never gets JSON-escaped. A 20B model has seen millions of bash
  blocks in training and almost no Ollama tool-call JSON. Asking the model nicely to
  avoid heredocs is a stopgap; this feels like the actual fix.

- ~~Context is the budget and I'm not managing it~~ phase 1 shipped the choke point,
  slices, grep cap, and stale-result stubbing. Real compaction (summarize-and-restart)
  stays on the shelf until a task actually needs it.

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
2. ~~Context hygiene~~ done, see method 9. expr_eval went from 141k-token failure to a
   12.4k-token pass.
3. Text-action mode (the mini-swe-agent protocol), then the real experiment: split vs
   single-gptoss vs single-gemma, 3 runs each, on the bench.
4. Orchestrator-level verify loop plus an interface contract (PLAN.md with exact shared
   signatures) for multi-file tasks.
5. Rebuild the middle of the bench with seeded-bug tasks.
