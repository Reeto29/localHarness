"""The agent loop.

Same dance as the M1 hand-test, but automated:
  ask the model -> if it wants tools, run them and append results -> ask again
  -> stop when it replies with no tool calls.
"""

import re
import time

from llm import chat, OllamaUnreachable
from tools import TOOL_SCHEMAS, TOOL_FUNCS, CODER_TOKENS, clip, run_bash

ORCHESTRATOR = "gemma4:31b-cloud"  # glm-5.2:cloud needs a paid Ollama subscription

# Explicit context window. Without this Ollama uses a small default and, once
# history outgrows it, silently drops the oldest tokens -- system prompt first.
NUM_CTX = 32768

SYSTEM_PROMPT = (
    "You are a coding orchestrator working in the current directory. You plan and "
    "delegate; you do NOT write code yourself. "
    "To produce code, call delegate_to_coder with: a fully self-contained task "
    "(signatures, names, constraints), the target_file path, and a verify_command — a "
    "shell command that exits 0 only when the code is correct (e.g. "
    "`python3 -c \"from mod import f; assert f(...) == ...\"`). The coder writes the file "
    "and self-verifies against your command, retrying on failure, and returns only a "
    "summary. You will not see the code, so put the correctness checks in verify_command. "
    "Inspect files with the other tools as needed; do not guess file contents. "
    "When the task is done, reply in plain text."
)

# A safety cap so a confused model can't loop forever.
MAX_STEPS = 20

# --- text-action mode ----------------------------------------------------------
# For local models that fumble tool-call JSON (Ollama 500s on malformed calls):
# no tools are sent at all. The model replies with ONE fenced lh_bash block, we
# regex it out and run it. No JSON to malform, so that failure class is gone.
# The unique fence tag keeps prose code blocks from false-matching.

FENCE_RE = re.compile(r"```lh_bash\s*\n(.*?)```", re.DOTALL)

# Sentinel command that means "I'm finished" (checked before execution).
TASK_COMPLETE = "task_complete"

# Prefix marking a command result in history, so stubbing can elide stale ones.
OBS_PREFIX = "[command output]\n"

# gpt-oss quirk: with a LONG system prompt and no tools it stalls in its
# thinking channel and returns empty content. So the system prompt stays
# tiny and the protocol rules ride in the first user message instead
# (mini-swe-agent uses the same layout).
TEXT_SYSTEM_PROMPT = (
    "Reply with one shell command in a ```lh_bash fenced block. Nothing else "
    f"runs. When finished, send {TASK_COMPLETE} in the block."
)

TEXT_TASK_RULES = (
    "Rules: each command runs in a FRESH shell in the working directory (cd and "
    "variables do not persist). Create or overwrite files with a heredoc "
    "(cat > f.py <<'EOF' ... EOF). Verify your work by running it before sending "
    f"{TASK_COMPLETE}. Exactly one lh_bash block per reply."
)

# Give up after this many failed chat() calls in a row (e.g. the model keeps
# emitting tool-call JSON that Ollama's parser rejects with an HTTP 500).
MAX_LLM_ERRORS = 3

# Tool results attached to the last N assistant turns are sent verbatim;
# older ones go out as one-line stubs (see _stub_stale_tool_results).
KEEP_RECENT_TOOL_RESULTS = 2

# Tool results shorter than this are never stubbed — the stub wouldn't save anything.
STUB_THRESHOLD_CHARS = 200


def _stub_stale_tool_results(messages):
    """Return a copy of messages for the API where tool results older than the
    last KEEP_RECENT_TOOL_RESULTS assistant turns are replaced with one-line
    stubs. Whatever the model concluded from those results is already in its
    later messages, so resending the raw output every step re-bills dead
    weight — that's what made history cost grow quadratically. The local
    `messages` list keeps the full content; only the outgoing copy is stubbed.
    """
    cutoff = 0
    seen = 0
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "assistant":
            seen += 1
            if seen == KEEP_RECENT_TOOL_RESULTS:
                cutoff = i
                break
    out = []
    for i, m in enumerate(messages):
        content = m.get("content") or ""
        is_tool = m.get("role") == "tool"
        is_obs = m.get("role") == "user" and content.startswith(OBS_PREFIX)
        if i < cutoff and (is_tool or is_obs) and len(content) > STUB_THRESHOLD_CHARS:
            label = m.get("tool_name", "tool") if is_tool else "command output"
            m = dict(m, content=(f"[{label} elided ({len(content)} chars); "
                                 "re-run if needed]"))
        out.append(m)
    return out


def needs_confirm(name, args):
    """Should this tool call ask the user first? run_bash always, and
    delegate_to_coder when it carries a verify_command (the harness runs
    that command without further gating)."""
    if name == "run_bash":
        return True
    return name == "delegate_to_coder" and bool(args.get("verify_command"))


def run(task, model=ORCHESTRATOR, verbose=True, confirm=None,
        wall_budget_secs=None, token_budget=None,
        system_prompt=None, tool_schemas=None, text_actions=False):
    """Run one task to completion. Returns (final_text_answer, metrics).

    confirm: optional callable(name, args) -> bool. Called before running a
             gated tool (see needs_confirm). Return False to skip it (the
             model is told it was denied). If confirm is None, everything runs.
    wall_budget_secs, token_budget: optional soft limits. When exceeded, the
             loop stops cleanly with stopped_reason='budget' instead of
             grinding on until MAX_STEPS.
    system_prompt, tool_schemas: override the defaults (used by bench configs,
             e.g. a no-delegation setup where the model writes code itself).
    text_actions: use the fenced-block protocol instead of tool calls — no
             tools are sent, so malformed-JSON 500s are structurally impossible.
    """
    if system_prompt is None:
        system_prompt = TEXT_SYSTEM_PROMPT if text_actions else SYSTEM_PROMPT
    if tool_schemas is None:
        tool_schemas = None if text_actions else TOOL_SCHEMAS

    start = time.monotonic()
    first_user = f"Task: {task}\n\n{TEXT_TASK_RULES}" if text_actions else task
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": first_user},
    ]

    # Metrics for the eval harness (and curiosity).
    metrics = {
        "steps": 0,
        "tokens": {"prompt": 0, "eval": 0, "coder_prompt": 0, "coder_eval": 0},
        "prompt_per_step": [],  # growth curve; flat while history grows = truncation
        "tools": {},          # name -> {"ok": n, "err": n}
        "llm_errors": 0,
        "format_errors": 0,   # text mode: replies without exactly one action block
        "stopped_reason": None,
    }
    coder_start = dict(CODER_TOKENS)  # snapshot; we report this run's delta

    def finish(reason, answer):
        metrics["stopped_reason"] = reason
        metrics["tokens"]["coder_prompt"] = CODER_TOKENS["prompt"] - coder_start["prompt"]
        metrics["tokens"]["coder_eval"] = CODER_TOKENS["eval"] - coder_start["eval"]
        return answer, metrics

    def record_tool(name, ok):
        t = metrics["tools"].setdefault(name, {"ok": 0, "err": 0})
        t["ok" if ok else "err"] += 1

    errors_in_a_row = 0
    format_errors_in_a_row = 0
    for step in range(MAX_STEPS):
        # Soft budgets: a clean stop with metrics beats a 15-minute grind.
        if wall_budget_secs and time.monotonic() - start > wall_budget_secs:
            return finish("budget", "[stopped: wall-clock budget exceeded]")
        spent = (metrics["tokens"]["prompt"] + metrics["tokens"]["eval"]
                 + (CODER_TOKENS["prompt"] - coder_start["prompt"])
                 + (CODER_TOKENS["eval"] - coder_start["eval"]))
        if token_budget and spent > token_budget:
            return finish("budget", "[stopped: token budget exceeded]")

        metrics["steps"] += 1
        try:
            body = chat(model, _stub_stale_tool_results(messages), tools=tool_schemas,
                        options={"temperature": 0, "num_ctx": NUM_CTX})
        except OllamaUnreachable:
            raise  # server is down; corrective re-prompts can't help
        except RuntimeError as e:
            # One malformed tool call must not kill the run: Ollama answers
            # HTTP 500 when a model emits invalid tool-call JSON (gpt-oss
            # does this with heredocs). Tell the model and let it re-issue.
            metrics["llm_errors"] += 1
            errors_in_a_row += 1
            if verbose:
                print(f"  [llm error {errors_in_a_row}/{MAX_LLM_ERRORS}] {str(e)[:150]}")
            if errors_in_a_row >= MAX_LLM_ERRORS:
                return finish("llm_error", "[stopped: "
                              f"{MAX_LLM_ERRORS} model errors in a row. Last: {str(e)[:300]}]")
            if text_actions:
                hint = ("Your last reply caused a server error: "
                        f"{str(e)[:200]}. Re-send your action as one "
                        "```lh_bash fenced block.")
            else:
                hint = (f"Your last reply caused a server error: {str(e)[:200]}. "
                        "This usually means a malformed tool call. Re-issue it as "
                        "valid JSON. Never put heredocs or multi-line scripts inside "
                        "a run_bash command; use write_file to create the file, "
                        "then run it.")
            messages.append({"role": "user", "content": hint})
            continue
        errors_in_a_row = 0

        metrics["tokens"]["prompt"] += body.get("prompt_eval_count", 0) or 0
        metrics["tokens"]["eval"] += body.get("eval_count", 0) or 0
        metrics["prompt_per_step"].append(body.get("prompt_eval_count", 0) or 0)

        reply = body["message"]
        messages.append(reply)  # record what the model said (text or tool calls)

        if text_actions:
            content = reply.get("content") or ""
            actions = [a.strip() for a in FENCE_RE.findall(content)]
            if len(actions) != 1:
                # Format slip — reflect it back and let the model retry.
                metrics["format_errors"] += 1
                format_errors_in_a_row += 1
                if verbose:
                    print(f"  [format error {format_errors_in_a_row}/{MAX_LLM_ERRORS}]")
                if format_errors_in_a_row >= MAX_LLM_ERRORS:
                    return finish("format_error",
                                  "[stopped: no valid action block after "
                                  f"{MAX_LLM_ERRORS} tries]")
                messages.append({"role": "user", "content": (
                    "Your reply must contain EXACTLY ONE ```lh_bash code block "
                    f"(found {len(actions)}). Re-send just the action."
                )})
                continue
            format_errors_in_a_row = 0

            command = actions[0]
            if command == TASK_COMPLETE:
                # The model's prose around the sentinel is its final answer.
                return finish("done", FENCE_RE.sub("", content).strip())
            if verbose:
                print(f"  [cmd] {command}")
            if confirm is not None and not confirm("run_bash", {"command": command}):
                result = "DENIED: the user declined to run this command."
            else:
                result = run_bash(command)
            record_tool("run_bash", not str(result).startswith(("ERROR", "DENIED")))
            messages.append({"role": "user",
                             "content": OBS_PREFIX + clip(str(result))})
            continue

        tool_calls = reply.get("tool_calls") or []
        if not tool_calls:
            # No tools requested -> the model is done.
            return finish("done", reply.get("content", ""))

        # Run each requested tool and feed the result back.
        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            if verbose:
                print(f"  [tool] {name}({args})")

            func = TOOL_FUNCS.get(name)
            if func is None:
                result = f"ERROR: unknown tool {name}"
            elif confirm is not None and needs_confirm(name, args) and not confirm(name, args):
                result = "DENIED: the user declined to run this command."
            else:
                try:
                    result = func(**args)
                except Exception as e:
                    result = f"ERROR running {name}: {e}"

            # A tool "failed" if it returned an error/denied/failed marker.
            ok = not str(result).startswith(("ERROR", "DENIED", "FAILED"))
            record_tool(name, ok)

            # Single choke point: no tool result enters history unbounded,
            # whatever the tool. (clip keeps head+tail, marks what it cut.)
            tool_msg = {"role": "tool", "tool_name": name, "content": clip(str(result))}
            # Echo the call id back so the model can match results to calls
            # (matters when it makes several tool calls in one turn).
            if call.get("id"):
                tool_msg["tool_call_id"] = call["id"]
            messages.append(tool_msg)

    return finish("max_steps", "[stopped: hit MAX_STEPS without a final answer]")
