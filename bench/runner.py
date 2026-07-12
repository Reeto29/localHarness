"""Eval runner. One agent run per task in a fresh temp workspace, graded by
the task's test.sh.

Usage:
    python3 bench/runner.py                              # all tasks, split config
    python3 bench/runner.py fizzbuzz                     # one task
    python3 bench/runner.py --config single-gptoss       # another model setup

Each task runs in a CHILD process (bench/_run_one.py) with the workspace as
cwd, so a hung agent gets killed by a hard wall-clock timeout instead of
hanging the bench. Soft budgets inside agent.run() stop a runaway task cleanly
first; the hard kill is the backstop.

Writes one row per task to bench/scores.csv AS EACH TASK FINISHES (a crash
mid-run loses nothing), plus a per-run scorecard JSON to bench/results/
(gitignored). The old aggregate-per-run rows were archived to scores_v0.csv
when the schema changed.
"""

import argparse
import csv
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
TASKS_DIR = os.path.join(BENCH, "tasks")
RESULTS_DIR = os.path.join(BENCH, "results")
SCORES_CSV = os.path.join(BENCH, "scores.csv")
RUN_ONE = os.path.join(BENCH, "_run_one.py")

# Soft budgets stop the agent cleanly (metrics survive); the hard timeout
# kills the child if even that fails. No more 15-minute grinds.
SOFT_WALL_SECS = 300
HARD_WALL_SECS = 450
TOKEN_BUDGET = 100_000

# Model setups the bench can run. None means the library default.
# direct=True: the orchestrator writes code itself, no delegate_to_coder
# (this replaces the old _ablation_no_qwen.py / _exp_single_local.py scripts).
CONFIGS = {
    "split":         {"orchestrator": None,          "coder": None, "direct": False},
    "gemma-direct":  {"orchestrator": None,          "coder": None, "direct": True},
    "single-gptoss": {"orchestrator": "gpt-oss:20b", "coder": None, "direct": True},
    # coder bake-off: same split, different local coder
    "split-opus9b":  {"orchestrator": None,
                      "coder": "aravhawk/qwen3.5-opus-4.6:9b", "direct": False},
    "split-ds9b":    {"orchestrator": None,
                      "coder": "pdurugyan/qwen3.5-9b-deepseek-v4-flash-Q4_K_M-v_2",
                      "direct": False},
    # The opus distill running everything itself, no orchestrator.
    "single-opus9b": {"orchestrator": "aravhawk/qwen3.5-opus-4.6:9b",
                      "coder": None, "direct": True},
    # Specialist coders vs the generalist champion. Both non-thinking:
    # the dense code-gen classic, and the fast-MoE code model (2.4B active).
    "split-qwen14b":    {"orchestrator": None, "coder": "qwen2.5-coder:14b",
                         "direct": False},
    "split-dscoder16b": {"orchestrator": None, "coder": "deepseek-coder-v2:16b",
                         "direct": False},
}

CSV_FIELDS = ["timestamp", "commit", "config", "task", "passed", "agent_status",
              "steps", "prompt_tokens", "output_tokens", "coder_prompt_tokens",
              "coder_output_tokens", "wall_secs", "error"]


def discover_tasks():
    return sorted(
        d for d in os.listdir(TASKS_DIR)
        if os.path.isdir(os.path.join(TASKS_DIR, d))
    )


def run_task(task_id, config_id):
    """Run one task in a child process, grade it, return a scores.csv row."""
    tdir = os.path.join(TASKS_DIR, task_id)
    prompt = open(os.path.join(tdir, "prompt.txt")).read().strip()
    test_sh = os.path.join(tdir, "test.sh")
    starter = os.path.join(tdir, "starter")

    work = tempfile.mkdtemp(prefix=f"bench_{task_id}_")
    if os.path.isdir(starter):
        shutil.copytree(starter, work, dirs_exist_ok=True)

    out_fd, out_path = tempfile.mkstemp(suffix=".json")
    os.close(out_fd)
    spec = {
        "prompt": prompt,
        "config": CONFIGS[config_id],
        "out": out_path,
        "wall_budget_secs": SOFT_WALL_SECS,
        "token_budget": TOKEN_BUDGET,
    }

    row = {"task": task_id, "passed": 0, "agent_status": None, "steps": 0,
           "prompt_tokens": 0, "output_tokens": 0, "coder_prompt_tokens": 0,
           "coder_output_tokens": 0, "wall_secs": 0.0, "error": None}

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, RUN_ONE, json.dumps(spec)],
            cwd=work, capture_output=True, text=True, timeout=HARD_WALL_SECS,
        )
        if proc.returncode != 0:
            row["agent_status"] = "error"
            row["error"] = (proc.stderr or "").strip()[-300:] or f"exit {proc.returncode}"
    except subprocess.TimeoutExpired:
        row["agent_status"] = "timeout"  # hard kill; the soft budget failed us
    row["wall_secs"] = round(time.monotonic() - t0, 1)

    # Pull metrics if the child got far enough to write them.
    try:
        with open(out_path) as f:
            result = json.load(f)
        m = result.get("metrics") or {}
        tok = m.get("tokens", {})
        row["agent_status"] = row["agent_status"] or m.get("stopped_reason")
        row["steps"] = m.get("steps", 0)
        row["prompt_tokens"] = tok.get("prompt", 0)
        row["output_tokens"] = tok.get("eval", 0)
        row["coder_prompt_tokens"] = tok.get("coder_prompt", 0)
        row["coder_output_tokens"] = tok.get("coder_eval", 0)
        # Not a CSV column (DictWriter ignores it); lands in the results JSON
        # so we can see whether the prompt-growth curve actually flattened.
        row["prompt_per_step"] = m.get("prompt_per_step", [])
        if result.get("error") and not row["error"]:
            row["agent_status"] = "error"
            row["error"] = result["error"][:300]
    except (OSError, ValueError):
        pass  # child died before writing; status was set above
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)

    # Grade whatever the agent left behind -- even after a timeout, partial
    # work that passes is worth knowing about (passed and agent_status are
    # separate columns for exactly this reason).
    try:
        graded = subprocess.run(["bash", test_sh], cwd=work, capture_output=True,
                                text=True, timeout=60)
        row["passed"] = int(graded.returncode == 0)
    except subprocess.TimeoutExpired:
        row["error"] = row["error"] or "test.sh timed out"

    shutil.rmtree(work, ignore_errors=True)
    return row


def git_commit():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=ROOT, capture_output=True, text=True)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def append_row(row):
    new_file = not os.path.exists(SCORES_CSV)
    with open(SCORES_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerow(row)


def main():
    ap = argparse.ArgumentParser(description="Run the eval bench.")
    ap.add_argument("tasks", nargs="*", help="task ids to run (default: all)")
    ap.add_argument("--config", default="split", choices=sorted(CONFIGS),
                    help="which model setup to run (default: split)")
    args = ap.parse_args()

    which = args.tasks or discover_tasks()
    os.makedirs(RESULTS_DIR, exist_ok=True)
    commit = git_commit()

    rows = []
    for task_id in which:
        print(f"running {task_id} [{args.config}] ...", flush=True)
        row = run_task(task_id, args.config)
        row["timestamp"] = datetime.datetime.now().isoformat(timespec="seconds")
        row["commit"] = commit
        row["config"] = args.config
        append_row(row)  # flush per task: a crash later loses nothing
        rows.append(row)

        mark = "PASS" if row["passed"] else "FAIL"
        extra = f" ({row['error']})" if row["error"] else ""
        print(f"  {mark} [{row['agent_status']}]  {row['steps']} steps, "
              f"{row['prompt_tokens']}+{row['output_tokens']} tok, "
              f"{row['wall_secs']}s{extra}")

    passed = sum(r["passed"] for r in rows)
    print(f"\n=== {args.config}: {passed}/{len(rows)} passed ===")

    stamp = datetime.datetime.now().isoformat(timespec="seconds").replace(":", "-")
    with open(os.path.join(RESULTS_DIR, f"{stamp}.json"), "w") as f:
        json.dump({"config": args.config, "commit": commit, "rows": rows}, f, indent=2)

    # Regenerate the progress board; a board bug must never fail a bench run.
    try:
        import board
        board.main()
    except Exception as e:
        print(f"(board regeneration failed: {e})")


if __name__ == "__main__":
    main()
