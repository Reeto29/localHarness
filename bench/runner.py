"""Eval runner. Drives agent.run() over each task in a fresh temp workspace,
runs its test.sh, and records pass/fail + metrics.

Usage:
    python3 bench/runner.py            # run all tasks
    python3 bench/runner.py fizzbuzz   # run one task by id

Writes a per-run scorecard to bench/results/ (gitignored) and appends a summary
row to bench/scores.csv (committed, so progress is tracked next to the commits).

Safety: each task runs inside a temp dir we chdir into, so the agent's relative-path
tools and run_bash operate there. The model could still escape with an absolute path
or '../', so don't run this on untrusted prompts. Good enough for a handmade dev-set.
"""

import os
import sys
import csv
import json
import shutil
import tempfile
import datetime
import subprocess

# Import the agent from the repo root (one dir up from bench/).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agent import run  # noqa: E402

BENCH = os.path.dirname(os.path.abspath(__file__))
TASKS_DIR = os.path.join(BENCH, "tasks")
RESULTS_DIR = os.path.join(BENCH, "results")
SCORES_CSV = os.path.join(BENCH, "scores.csv")


def auto_yes(name, args):
    """Non-interactive confirm for bench runs. Safe only because each task runs
    in a throwaway temp workspace."""
    return True


def discover_tasks():
    return sorted(
        d for d in os.listdir(TASKS_DIR)
        if os.path.isdir(os.path.join(TASKS_DIR, d))
    )


def run_task(task_id):
    tdir = os.path.join(TASKS_DIR, task_id)
    prompt = open(os.path.join(tdir, "prompt.txt")).read().strip()
    test_sh = os.path.join(tdir, "test.sh")
    starter = os.path.join(tdir, "starter")

    work = tempfile.mkdtemp(prefix=f"bench_{task_id}_")
    if os.path.isdir(starter):
        shutil.copytree(starter, work, dirs_exist_ok=True)

    cwd0 = os.getcwd()
    result = {"task": task_id, "passed": False, "steps": 0,
              "prompt_tokens": 0, "output_tokens": 0, "error": None}
    try:
        os.chdir(work)
        answer, metrics = run(prompt, verbose=False, confirm=auto_yes)
        result["steps"] = metrics["steps"]
        result["prompt_tokens"] = metrics["tokens"]["prompt"]
        result["output_tokens"] = metrics["tokens"]["eval"]
        proc = subprocess.run(["bash", test_sh], capture_output=True,
                              text=True, timeout=60)
        result["passed"] = proc.returncode == 0
    except Exception as e:
        result["error"] = str(e)
    finally:
        os.chdir(cwd0)
        shutil.rmtree(work, ignore_errors=True)
    return result


def git_commit():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                            cwd=ROOT, capture_output=True, text=True)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def append_scores_row(summary):
    new_file = not os.path.exists(SCORES_CSV)
    with open(SCORES_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp", "commit", "tasks", "passed",
                        "pass_rate", "total_steps", "total_tokens"])
        w.writerow([summary["timestamp"], summary["commit"], summary["tasks"],
                    summary["passed"], f"{summary['pass_rate']:.2f}",
                    summary["total_steps"], summary["total_tokens"]])


def main():
    which = sys.argv[1:] or discover_tasks()
    os.makedirs(RESULTS_DIR, exist_ok=True)

    results = []
    for task_id in which:
        print(f"running {task_id} ...", flush=True)
        r = run_task(task_id)
        mark = "PASS" if r["passed"] else "FAIL"
        extra = f" ({r['error']})" if r["error"] else ""
        print(f"  {mark}  {r['steps']} steps, "
              f"{r['prompt_tokens']}+{r['output_tokens']} tokens{extra}")
        results.append(r)

    passed = sum(1 for r in results if r["passed"])
    n = len(results)
    summary = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "commit": git_commit(),
        "tasks": n,
        "passed": passed,
        "pass_rate": passed / n if n else 0.0,
        "total_steps": sum(r["steps"] for r in results),
        "total_tokens": sum(r["prompt_tokens"] + r["output_tokens"] for r in results),
        "results": results,
    }

    print(f"\n=== {passed}/{n} passed (pass_rate {summary['pass_rate']:.0%}) ===")

    # per-run scorecard (gitignored) + committed summary row
    stamp = summary["timestamp"].replace(":", "-")
    with open(os.path.join(RESULTS_DIR, f"{stamp}.json"), "w") as f:
        json.dump(summary, f, indent=2)
    append_scores_row(summary)


if __name__ == "__main__":
    main()
