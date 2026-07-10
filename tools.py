"""Tools the agent can call.

Each tool is two things:
  1. a plain Python function that does the work, and
  2. a JSON schema (OpenAI-style) that tells the model the tool exists.

TOOL_SCHEMAS  -> the list we pass to chat(tools=...)
TOOL_FUNCS    -> name -> function, so the loop can dispatch a tool call
"""

import os
import re
import subprocess

from llm import generate

# The local model that actually writes code. Runs on the M4 (24GB), ~13GB resident.
CODER_MODEL = "gpt-oss:20b"

# Running tally of the coder's token usage. delegate_to_coder adds to it;
# agent.run() snapshots it so metrics include the coder's cost (it used to be
# invisible, which quietly biased every split-vs-single comparison).
CODER_TOKENS = {"prompt": 0, "eval": 0}

CODER_SYSTEM = (
    "You are a focused coding model. You are given one small, self-contained task. "
    "Return only the code that satisfies it, with no explanation, no markdown fences, "
    "and no commentary. If a docstring or comment helps, put it in the code itself."
)


# --- the functions ------------------------------------------------------------

def read_file(path, start_line=None, end_line=None):
    """Return the contents of a text file, or an error string.

    start_line/end_line (1-indexed, inclusive) read just a slice, with a
    header saying where the slice sits — so the model can pull the region
    it needs instead of paying for the whole file every time."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        if start_line is None and end_line is None:
            return text
        lines = text.splitlines()
        total = len(lines)
        s = max(1, int(start_line or 1))
        e = min(total, int(end_line or total))
        if s > total:
            return f"ERROR: start_line {s} is past the end of {path} ({total} lines)"
        body = "\n".join(lines[s - 1:e])
        return f"[lines {s}-{e} of {total} in {path}]\n{body}"
    except FileNotFoundError:
        return f"ERROR: file not found: {path}"
    except UnicodeDecodeError:
        return f"ERROR: {path} looks like a binary file, not text."
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


GREP_MAX_HITS = 50


def grep(pattern, path="."):
    """Search for a literal string in files under path. Returns file:line: matches
    (capped at GREP_MAX_HITS so one broad pattern can't flood the context)."""
    hits = []
    count = 0
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
                                count += 1
                                if count <= GREP_MAX_HITS:
                                    hits.append(f"{fpath}:{i}: {line.rstrip()}")
                except (UnicodeDecodeError, OSError):
                    continue  # skip binary / unreadable files
        if count > GREP_MAX_HITS:
            hits.append(f"...[{count - GREP_MAX_HITS} more matches omitted; "
                        "narrow your pattern]")
        return "\n".join(hits) if hits else f"(no matches for {pattern!r})"
    except Exception as e:
        return f"ERROR: grep failed: {e}"


def _strip_reasoning(text):
    """Remove <think>...</think> blocks that reasoning models emit before the answer.

    Handles a closed block, and the case where only the closing tag survives
    (some models omit the opening <think> and dump reasoning, then </think>, then code).
    """
    # full <think>...</think> blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # dangling close tag: keep only what comes after the last </think>
    if "</think>" in text.lower():
        idx = text.lower().rfind("</think>")
        text = text[idx + len("</think>"):]
    return text.strip()


def _strip_fences(text):
    """Strip reasoning blocks, then any ```lang ... ``` markdown fences."""
    t = _strip_reasoning(text)
    if t.startswith("```"):
        lines = t.splitlines()
        lines = lines[1:]                       # drop opening ```lang
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]                  # drop closing ```
        t = "\n".join(lines)
    return t.strip()


CODER_MAX_TRIES = 3


def delegate_to_coder(task, target_file, verify_command=None):
    """Worker-coder sub-loop. The local coder writes target_file; the harness
    verifies and retries. Returns only a SUMMARY (never the code) so the caller's
    context stays small.

    task:           self-contained description of what to write.
    target_file:    path the code is written to.
    verify_command: optional shell command that exits 0 when the code is correct
                    (e.g. a python -c with asserts). On non-zero, the error is fed
                    back to the coder and it retries. If omitted, no verification.
    """
    last_err = ""
    for attempt in range(1, CODER_MAX_TRIES + 1):
        if attempt == 1:
            prompt = task
        else:
            # Cap the embedded file so the retry prompt fits the coder's
            # 8192 num_ctx. Ollama truncates from the FRONT, so an oversized
            # prompt would cut off the task itself and make retries worse.
            current = clip(read_file(target_file))
            prompt = (
                f"{task}\n\nYour previous attempt (in {target_file}):\n"
                f"{current}\n\nIt FAILED verification with:\n{last_err}\n\n"
                f"Return the corrected FULL contents of {target_file}."
            )
        try:
            body = generate(
                CODER_MODEL, prompt, system=CODER_SYSTEM,
                options={"temperature": 0, "num_ctx": 8192},
            )
        except Exception as e:
            return f"ERROR: coder failed: {e}"
        CODER_TOKENS["prompt"] += body.get("prompt_eval_count", 0) or 0
        CODER_TOKENS["eval"] += body.get("eval_count", 0) or 0
        code = _strip_fences(body["message"].get("content", ""))
        if not code:
            return "ERROR: coder returned nothing"

        w = write_file(target_file, code)
        if w.startswith("ERROR"):
            return w
        n = len(code.splitlines())

        if not verify_command:
            return f"wrote {n} lines to {target_file} (no verification requested)"

        try:
            rc, check = _run(verify_command)
        except Exception as e:
            return f"ERROR: could not run verify_command: {e}"
        if rc == 0:
            return f"wrote {n} lines to {target_file}; verification passed on attempt {attempt}"
        last_err = clip(check, 800)  # head+tail: the error line is at the END

    # FAILED prefix so the agent loop counts this as a tool failure in metrics.
    return (f"FAILED: wrote {target_file} but verification failed after "
            f"{CODER_MAX_TRIES} attempts. Last error:\n{last_err}")


def clip(text, limit=6000):
    """Cap text at limit chars, keeping head AND tail. The tail matters:
    in a traceback the actual error is the last line, and head-only
    truncation used to cut exactly that off."""
    if len(text) <= limit:
        return text
    half = limit // 2
    omitted = len(text) - 2 * half
    return f"{text[:half]}\n...[{omitted} chars omitted]...\n{text[-half:]}"


def _run(command):
    """Run a shell command. Returns (returncode, combined stdout+stderr).
    Raises on timeout/OS errors; callers decide how to present those."""
    proc = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=120,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, clip(out.strip() or "(no output)")


def run_bash(command):
    """Run a shell command, return combined stdout+stderr (truncated)."""
    try:
        rc, out = _run(command)
        return f"exit {rc}\n{out}"
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
    _fn("read_file", "Read a text file. Prefer a line slice for big files; "
        "a slice comes back with a 'lines X-Y of N' header.",
        {"path": {"type": "string", "description": "Path to the file, relative to the working dir."},
         "start_line": {"type": "integer", "description": "Optional first line to read (1-indexed)."},
         "end_line": {"type": "integer", "description": "Optional last line to read (inclusive)."}},
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

    _fn("delegate_to_coder",
        "Delegate a coding task to a dedicated coder that writes the file and self-verifies. "
        "It writes the code to target_file and, if you give a verify_command, runs it and "
        "retries on failure. You get back only a short summary, never the code itself. Use "
        "this to write code instead of writing it yourself. The task gets no conversation "
        "history, so it must be fully specified.",
        {"task": {"type": "string", "description": "A complete, self-contained description "
                  "of the code to write, including signatures, names, and constraints."},
         "target_file": {"type": "string", "description": "Path the code should be written to."},
         "verify_command": {"type": "string", "description": "Optional shell command that "
                  "exits 0 when the code is correct, e.g. a python -c with asserts. The coder "
                  "retries using the error if it fails."}},
        ["task", "target_file"]),
]


# --- registry the loop uses ---------------------------------------------------

TOOL_FUNCS = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "list_dir": list_dir,
    "grep": grep,
    "run_bash": run_bash,
    "delegate_to_coder": delegate_to_coder,
}
