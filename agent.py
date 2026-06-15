"""The agent loop.

Same dance as the M1 hand-test, but automated:
  ask the model -> if it wants tools, run them and append results -> ask again
  -> stop when it replies with no tool calls.
"""

from llm import chat
from tools import TOOL_SCHEMAS, TOOL_FUNCS

ORCHESTRATOR = "gemma4:31b-cloud"

SYSTEM_PROMPT = (
    "You are a coding orchestrator working in the current directory. You plan, "
    "inspect files, place code, run things, and debug. "
    "Do NOT write substantial code yourself. To produce code, call delegate_to_coder "
    "with a fully self-contained task (signatures, names, constraints), then review the "
    "result and use write_file/edit_file to place it. "
    "Use the tools to inspect files before answering; do not guess file contents. "
    "When the task is done, reply in plain text."
)

# A safety cap so a confused model can't loop forever.
MAX_STEPS = 20

# Tools that should ask before running.
NEEDS_CONFIRM = {"run_bash"}


def run(task, model=ORCHESTRATOR, verbose=True, confirm=None):
    """Run one task to completion. Returns (final_text_answer, metrics).

    confirm: optional callable(name, args) -> bool. Called before running a tool
             in NEEDS_CONFIRM. Return False to skip it (the model is told it was
             denied). If confirm is None, everything runs.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    # Metrics for the eval harness (and curiosity).
    metrics = {
        "steps": 0,
        "tokens": {"prompt": 0, "eval": 0},
        "tools": {},          # name -> {"ok": n, "err": n}
        "stopped_reason": None,
    }

    def record_tool(name, ok):
        t = metrics["tools"].setdefault(name, {"ok": 0, "err": 0})
        t["ok" if ok else "err"] += 1

    for step in range(MAX_STEPS):
        metrics["steps"] += 1
        body = chat(model, messages, tools=TOOL_SCHEMAS, options={"temperature": 0})
        metrics["tokens"]["prompt"] += body.get("prompt_eval_count", 0) or 0
        metrics["tokens"]["eval"] += body.get("eval_count", 0) or 0

        reply = body["message"]
        messages.append(reply)  # record what the model said (text or tool calls)

        tool_calls = reply.get("tool_calls") or []
        if not tool_calls:
            # No tools requested -> the model is done.
            metrics["stopped_reason"] = "done"
            return reply.get("content", ""), metrics

        # Run each requested tool and feed the result back.
        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            if verbose:
                print(f"  [tool] {name}({args})")

            func = TOOL_FUNCS.get(name)
            if func is None:
                result = f"ERROR: unknown tool {name}"
            elif name in NEEDS_CONFIRM and confirm is not None and not confirm(name, args):
                result = "DENIED: the user declined to run this command."
            else:
                try:
                    result = func(**args)
                except Exception as e:
                    result = f"ERROR running {name}: {e}"

            # A tool "failed" if it returned an error/denied marker.
            ok = not str(result).startswith(("ERROR", "DENIED"))
            record_tool(name, ok)

            tool_msg = {"role": "tool", "tool_name": name, "content": str(result)}
            # Echo the call id back so the model can match results to calls
            # (matters when it makes several tool calls in one turn).
            if call.get("id"):
                tool_msg["tool_call_id"] = call["id"]
            messages.append(tool_msg)

    metrics["stopped_reason"] = "max_steps"
    return "[stopped: hit MAX_STEPS without a final answer]", metrics
