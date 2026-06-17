"""CLI front door for the harness.

A read-eval-print loop: you type a task, the agent works on it, you see the answer.
Type 'exit' or Ctrl-D to quit.
"""

from agent import run


def confirm_bash(name, args):
    """Ask the user before running a shell command. Returns True to allow."""
    command = args.get("command", "")
    print(f"\n  the agent wants to run:  {command}")
    answer = input("  allow? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def main():
    print("local harness. type a task, or 'exit' to quit.\n")
    while True:
        try:
            task = input("you> ").strip()
        except EOFError:
            print()
            break

        if not task:
            continue
        if task.lower() in ("exit", "quit"):
            break

        try:
            answer, metrics = run(task, confirm=confirm_bash)
        except Exception as e:
            print(f"\n[error] {e}\n")
            continue

        print(f"\nagent> {answer}\n")
        tok = metrics["tokens"]
        total = tok["prompt"] + tok["eval"]
        print(f"  [{metrics['steps']} steps | "
              f"{tok['prompt']} prompt + {tok['eval']} output = {total} tokens]\n")

    print("bye.")


if __name__ == "__main__":
    main()
