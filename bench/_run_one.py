"""Child process for the bench runner: run ONE task and write metrics JSON.

runner.py invokes this in a subprocess with the task workspace as cwd, so a
hung agent can be killed from outside with a hard timeout. (The old in-process
runner couldn't interrupt a blocked HTTP read to Ollama.)

argv[1] is a JSON spec:
    {"prompt": str, "config": {...}, "out": path,
     "wall_budget_secs": int, "token_budget": int}
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import agent   # noqa: E402
import tools   # noqa: E402

# System prompt for configs where the orchestrator writes code itself
# (no delegate_to_coder). Used by the 'direct' configs in runner.CONFIGS.
DIRECT_PROMPT = (
    "You are a coding agent working in the current directory. You plan, inspect "
    "files, WRITE CODE YOURSELF with write_file/edit_file, run things, and debug. "
    "Use the tools to inspect files before answering; do not guess file contents. "
    "When the task is done, reply in plain text."
)


def config_kwargs(cfg):
    """Translate a bench config into agent.run() keyword arguments.
    Missing keys mean library defaults."""
    kw = {"model": cfg.get("orchestrator") or agent.ORCHESTRATOR}
    if cfg.get("coder"):
        tools.CODER_MODEL = cfg["coder"]  # the one global knob left
    if cfg.get("text"):
        # Fenced-block protocol: no tools at all, agent.run supplies its own prompt.
        kw["text_actions"] = True
    elif cfg.get("direct"):
        # No delegation: drop the coder tool, tell the model to write code itself.
        kw["system_prompt"] = DIRECT_PROMPT
        kw["tool_schemas"] = [s for s in agent.TOOL_SCHEMAS
                              if s["function"]["name"] != "delegate_to_coder"]
    return kw


def main():
    spec = json.loads(sys.argv[1])
    kw = config_kwargs(spec.get("config") or {})

    result = {"answer": None, "metrics": None, "error": None}
    try:
        answer, metrics = agent.run(
            spec["prompt"], verbose=False,
            confirm=None,  # auto-approve: the workspace is a throwaway temp dir
            wall_budget_secs=spec.get("wall_budget_secs"),
            token_budget=spec.get("token_budget"),
            **kw,
        )
        result["answer"] = answer
        result["metrics"] = metrics
    except Exception as e:
        result["error"] = str(e)

    with open(spec["out"], "w") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main()
