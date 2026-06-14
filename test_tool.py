from llm import chat
from tools import TOOL_SCHEMAS, TOOL_FUNCS

ORCH = "gemma4:31b-cloud"

# History starts with a system prompt + the user's task.
messages = [
    {"role": "system", "content": "You are a coding agent. Use tools when you need info."},
    {"role": "user", "content": "Read PRD.md and tell me in one sentence what milestone M1 is."},
]

# --- turn 1: model asks for a tool ---
reply = chat(ORCH, messages, tools=TOOL_SCHEMAS, options={"temperature": 0})
messages.append(reply)  # append the assistant's tool-call message to history
print("turn 1 tool_calls:", reply.get("tool_calls"))

# --- run each requested tool, append result as a 'tool' message ---
for call in reply.get("tool_calls", []):
    name = call["function"]["name"]
    args = call["function"]["arguments"]
    result = TOOL_FUNCS[name](**args)
    messages.append({"role": "tool", "tool_name": name, "content": result})
    print(f"ran {name}({args}) -> {len(result)} chars")

# --- turn 2: model now has the file contents, gives final answer ---
final = chat(ORCH, messages, tools=TOOL_SCHEMAS, options={"temperature": 0})
print("\n=== final answer ===")
print(final.get("content"))
