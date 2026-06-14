"""Tools the agent can call.

Each tool is two things:
  1. a plain Python function that does the work, and
  2. a JSON schema (OpenAI-style) that tells the model the tool exists.

TOOL_SCHEMAS  -> the list we pass to chat(tools=...)
TOOL_FUNCS    -> name -> function, so the loop can dispatch a tool call
"""

import os
import subprocess


# --- the functions ------------------------------------------------------------

def read_file(path):
    """Return the contents of a text file, or an error string."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"ERROR: file not found: {path}"
    except Exception as e:
        return f"ERROR: could not read {path}: {e}"


def write_file(path, content):
    """Write content to a file, creating parent dirs. Overwrites if it exists."""
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"OK: wrote {len(content)} chars to {path}"
    except Exception as e:
        return f"ERROR: could not write {path}: {e}"


def edit_file(path, old, new):
    """Replace the first exact occurrence of `old` with `new` in a file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        count = text.count(old)
        if count == 0:
            return f"ERROR: `old` text not found in {path}"
        if count > 1:
            return f"ERROR: `old` text appears {count} times in {path}; make it unique"
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace(old, new, 1))
        return f"OK: edited {path}"
    except FileNotFoundError:
        return f"ERROR: file not found: {path}"
    except Exception as e:
        return f"ERROR: could not edit {path}: {e}"


def list_dir(path="."):
    """List entries in a directory, marking subdirectories with a trailing /."""
    try:
        entries = sorted(os.listdir(path))
        if not entries:
            return f"(empty) {path}"
        lines = []
        for name in entries:
            full = os.path.join(path, name)
            lines.append(name + "/" if os.path.isdir(full) else name)
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: could not list {path}: {e}"


def grep(pattern, path="."):
    """Search for a literal string in files under path. Returns file:line: matches."""
    hits = []
    try:
        for root, dirs, files in os.walk(path):
            # skip noise
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".venv")]
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        for i, line in enumerate(f, 1):
                            if pattern in line:
                                hits.append(f"{fpath}:{i}: {line.rstrip()}")
                except (UnicodeDecodeError, OSError):
                    continue  # skip binary / unreadable files
        return "\n".join(hits) if hits else f"(no matches for {pattern!r})"
    except Exception as e:
        return f"ERROR: grep failed: {e}"


def run_bash(command):
    """Run a shell command, return combined stdout+stderr (truncated)."""
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=120,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        out = out.strip() or "(no output)"
        if len(out) > 6000:
            out = out[:6000] + "\n...[truncated]"
        return f"exit {proc.returncode}\n{out}"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 120s"
    except Exception as e:
        return f"ERROR: could not run command: {e}"


# --- schemas the model sees ---------------------------------------------------

def _fn(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


TOOL_SCHEMAS = [
    _fn("read_file", "Read and return the full contents of a text file.",
        {"path": {"type": "string", "description": "Path to the file, relative to the working dir."}},
        ["path"]),

    _fn("write_file", "Write (or overwrite) a file with the given content. Creates parent dirs.",
        {"path": {"type": "string", "description": "Path to write to."},
         "content": {"type": "string", "description": "Full file contents to write."}},
        ["path", "content"]),

    _fn("edit_file", "Replace the first exact occurrence of `old` with `new` in a file. `old` must be unique.",
        {"path": {"type": "string", "description": "Path to the file to edit."},
         "old": {"type": "string", "description": "Exact text to find (must appear exactly once)."},
         "new": {"type": "string", "description": "Replacement text."}},
        ["path", "old", "new"]),

    _fn("list_dir", "List the files and subdirectories in a directory.",
        {"path": {"type": "string", "description": "Directory to list. Defaults to current dir."}},
        []),

    _fn("grep", "Search for a literal string across files under a path. Returns file:line matches.",
        {"pattern": {"type": "string", "description": "Literal string to search for."},
         "path": {"type": "string", "description": "Directory to search. Defaults to current dir."}},
        ["pattern"]),

    _fn("run_bash", "Run a shell command in the working directory and return its output.",
        {"command": {"type": "string", "description": "The shell command to run."}},
        ["command"]),
]


# --- registry the loop uses ---------------------------------------------------

TOOL_FUNCS = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "list_dir": list_dir,
    "grep": grep,
    "run_bash": run_bash,
}
