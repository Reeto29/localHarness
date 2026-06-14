"""Tools the agent can call.

Each tool is two things:
  1. a plain Python function that does the work, and
  2. a JSON schema (OpenAI-style) that tells the model the tool exists.

TOOL_SCHEMAS  -> the list we pass to chat(tools=...)
TOOL_FUNCS    -> name -> function, so the loop can dispatch a tool call
"""


def read_file(path):
    """Return the contents of a text file, or an error string."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"ERROR: file not found: {path}"
    except Exception as e:
        return f"ERROR: could not read {path}: {e}"


# --- schemas the model sees ---------------------------------------------------

read_file_schema = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read and return the full contents of a text file at the given path.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read, relative to the working directory.",
                },
            },
            "required": ["path"],
        },
    },
}


# --- registries the loop uses -------------------------------------------------

TOOL_SCHEMAS = [read_file_schema]

TOOL_FUNCS = {
    "read_file": read_file,
}