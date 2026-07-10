"""CLI front door for the harness.

A read-eval-print loop: you type a task, the agent works on it, you see the answer.
Type 'exit' or Ctrl-D to quit.
"""

from agent import run


def confirm_tool(name, args):
    """Ask the user before a gated tool call runs. Returns True to allow."""
    if name == "delegate_to_coder":
        print(f"\n  the coder will verify with:  {args.get('verify_command', '')}")
    else:
        print(f"\n  the agent wants to run:  {args.get('command', '')}")
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
            answer, metrics = run(task, confirm=confirm_tool)
        except Exception as e:
            print(f"\n[error] {e}\n")
            continue

        print(f"\nagent> {answer}\n")
        tok = metrics["tokens"]
        total = tok["prompt"] + tok["eval"] + tok["coder_prompt"] + tok["coder_eval"]
        line = f"  [{metrics['steps']} steps | orchestrator {tok['prompt']}+{tok['eval']}"
        if tok["coder_prompt"] or tok["coder_eval"]:
            line += f" | coder {tok['coder_prompt']}+{tok['coder_eval']}"
        print(line + f" | {total} tokens total]\n")

    print("bye.")


if __name__ == "__main__":
    main()
