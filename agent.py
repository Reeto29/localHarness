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
    """Run one task to completion. Returns the model's final text answer.

    confirm: optional callable(name, args) -> bool. Called before running a tool
             in NEEDS_CONFIRM. Return False to skip it (the model is told it was
             denied). If confirm is None, everything runs.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    for step in range(MAX_STEPS):
        reply = chat(model, messages, tools=TOOL_SCHEMAS, options={"temperature": 0})
        messages.append(reply)  # record what the model said (text or tool calls)

        tool_calls = reply.get("tool_calls") or []
        if not tool_calls:
            # No tools requested -> the model is done.
            return reply.get("content", "")

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

            tool_msg = {"role": "tool", "tool_name": name, "content": str(result)}
            # Echo the call id back so the model can match results to calls
            # (matters when it makes several tool calls in one turn).
            if call.get("id"):
                tool_msg["tool_call_id"] = call["id"]
            messages.append(tool_msg)

    return "[stopped: hit MAX_STEPS without a final answer]"
