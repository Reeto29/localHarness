"""The agent loop.

Same dance as the M1 hand-test, but automated:
  ask the model -> if it wants tools, run them and append results -> ask again
  -> stop when it replies with no tool calls.
"""

from llm import chat
from tools import TOOL_SCHEMAS, TOOL_FUNCS

ORCHESTRATOR = "gemma4:31b-cloud"

SYSTEM_PROMPT = (
    "You are a coding agent working in the current directory. "
    "Use the provided tools to inspect files before answering. "
    "Do not guess file contents. When the task is done, reply in plain text."
)

# A safety cap so a confused model can't loop forever.
MAX_STEPS = 20


def run(task, model=ORCHESTRATOR, verbose=True):
    """Run one task to completion. Returns the model's final text answer."""
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
            else:
                try:
                    result = func(**args)
                except Exception as e:
                    result = f"ERROR running {name}: {e}"

            messages.append({"role": "tool", "tool_name": name, "content": str(result)})

    return "[stopped: hit MAX_STEPS without a final answer]"
