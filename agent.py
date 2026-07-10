"""The agent loop.

Same dance as the M1 hand-test, but automated:
  ask the model -> if it wants tools, run them and append results -> ask again
  -> stop when it replies with no tool calls.
"""

import time

from llm import chat, OllamaUnreachable
from tools import TOOL_SCHEMAS, TOOL_FUNCS, CODER_TOKENS

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

# Give up after this many failed chat() calls in a row (e.g. the model keeps
# emitting tool-call JSON that Ollama's parser rejects with an HTTP 500).
MAX_LLM_ERRORS = 3


def needs_confirm(name, args):
    """Should this tool call ask the user first? run_bash always, and
    delegate_to_coder when it carries a verify_command (the harness runs
    that command without further gating)."""
    if name == "run_bash":
        return True
    return name == "delegate_to_coder" and bool(args.get("verify_command"))


def run(task, model=ORCHESTRATOR, verbose=True, confirm=None,
        wall_budget_secs=None, token_budget=None,
        system_prompt=None, tool_schemas=None):
    """Run one task to completion. Returns (final_text_answer, metrics).

    confirm: optional callable(name, args) -> bool. Called before running a
             gated tool (see needs_confirm). Return False to skip it (the
             model is told it was denied). If confirm is None, everything runs.
    wall_budget_secs, token_budget: optional soft limits. When exceeded, the
             loop stops cleanly with stopped_reason='budget' instead of
             grinding on until MAX_STEPS.
    system_prompt, tool_schemas: override the defaults (used by bench configs,
             e.g. a no-delegation setup where the model writes code itself).
    """
    if system_prompt is None:
        system_prompt = SYSTEM_PROMPT
    if tool_schemas is None:
        tool_schemas = TOOL_SCHEMAS

    start = time.monotonic()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    # Metrics for the eval harness (and curiosity).
    metrics = {
        "steps": 0,
        "tokens": {"prompt": 0, "eval": 0, "coder_prompt": 0, "coder_eval": 0},
        "tools": {},          # name -> {"ok": n, "err": n}
        "llm_errors": 0,
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
            body = chat(model, messages, tools=tool_schemas,
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
            messages.append({"role": "user", "content": (
                f"Your last reply caused a server error: {str(e)[:200]}. "
                "This usually means a malformed tool call. Re-issue it as valid JSON. "
                "Never put heredocs or multi-line scripts inside a run_bash command; "
                "use write_file to create the file, then run it."
            )})
            continue
        errors_in_a_row = 0

        metrics["tokens"]["prompt"] += body.get("prompt_eval_count", 0) or 0
        metrics["tokens"]["eval"] += body.get("eval_count", 0) or 0

        reply = body["message"]
        messages.append(reply)  # record what the model said (text or tool calls)

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

            tool_msg = {"role": "tool", "tool_name": name, "content": str(result)}
            # Echo the call id back so the model can match results to calls
            # (matters when it makes several tool calls in one turn).
            if call.get("id"):
                tool_msg["tool_call_id"] = call["id"]
            messages.append(tool_msg)

    return finish("max_steps", "[stopped: hit MAX_STEPS without a final answer]")
