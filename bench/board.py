"""Generate bench/board.html — a self-contained progress board from scores.csv.

Stdlib only, no server, no external assets: the output is one HTML file you can
commit, open locally, or serve via GitHub Pages. runner.py regenerates it after
every bench run; run it by hand with:

    python3 bench/board.py
"""

import csv
import datetime
import glob
import html
import json
import os

BENCH = os.path.dirname(os.path.abspath(__file__))
SCORES = os.path.join(BENCH, "scores.csv")
SCORES_V0 = os.path.join(BENCH, "scores_v0.csv")
RESULTS_DIR = os.path.join(BENCH, "results")
OUT = os.path.join(BENCH, "board.html")


# --- data ----------------------------------------------------------------------

def total_tokens(row):
    return (int(row["prompt_tokens"]) + int(row["output_tokens"])
            + int(row["coder_prompt_tokens"]) + int(row["coder_output_tokens"]))


def load_rows():
    if not os.path.exists(SCORES):
        return []
    with open(SCORES) as f:
        return list(csv.DictReader(f))


def latest_run(rows):
    """Rows of the most recent (commit, config) run, one per task, newest wins."""
    if not rows:
        return None, []
    newest = max(rows, key=lambda r: r["timestamp"])
    key = (newest["commit"], newest["config"])
    by_task = {}
    for r in sorted(rows, key=lambda r: r["timestamp"]):
        if (r["commit"], r["config"]) == key:
            by_task[r["task"]] = r
    tasks = sorted(by_task.values(), key=total_tokens, reverse=True)
    return newest, tasks


def suite_size():
    """How many tasks the bench currently has (so history can show full runs only)."""
    tasks_dir = os.path.join(BENCH, "tasks")
    try:
        return sum(os.path.isdir(os.path.join(tasks_dir, d))
                   for d in os.listdir(tasks_dir))
    except OSError:
        return 0


def run_groups(rows):
    """Full-suite (commit, config) runs from the CSV, oldest first. Partial and
    smoke runs (single tasks, aborted sweeps) are data, not history — skipped."""
    n_suite = suite_size()
    groups = {}
    for r in rows:
        groups.setdefault((r["commit"], r["config"]), []).append(r)
    out = []
    for (commit, config), grp in groups.items():
        by_task = {}
        for r in sorted(grp, key=lambda r: r["timestamp"]):
            by_task[r["task"]] = r
        grp = list(by_task.values())
        if n_suite and len(grp) < n_suite:
            continue
        out.append({
            "date": max(r["timestamp"] for r in grp)[:10],
            "label": f"{config} · full suite ({len(grp)} tasks)",
            "commit": commit,
            "passed": sum(int(r["passed"]) for r in grp),
            "tasks": len(grp),
            "steps": sum(int(r["steps"]) for r in grp),
            "tokens": sum(total_tokens(r) for r in grp),
            "starred": False,
        })
    out.sort(key=lambda g: g["date"])
    return out


def first_expr_attempt():
    """The solo expr_eval failure from the legacy CSV — the 'before' of the
    8-task era. (The older 7/7 rows predate expr_eval entirely and stay in
    scores_v0.csv; they never ran the 8th task, so they aren't shown.)"""
    if not os.path.exists(SCORES_V0):
        return None
    with open(SCORES_V0) as f:
        for r in csv.DictReader(f):
            if r["tasks"] == "1" and r["passed"] == "0":
                return {
                    "date": r["timestamp"][:10],
                    "label": "expr_eval added · first attempt (solo)",
                    "commit": r["commit"],
                    "passed": 0,
                    "tasks": 1,
                    "steps": int(r["total_steps"]),
                    "tokens": int(r["total_tokens"]),
                    "starred": True,
                }
    return None


def expr_eval_story(latest_tasks):
    """(before_tokens, after_row) for the turnaround panel, or None."""
    after = next((r for r in latest_tasks if r["task"] == "expr_eval"), None)
    before = first_expr_attempt()
    if not (after and before):
        return None
    return before["tokens"], after


def prompt_curve(task="expr_eval"):
    """prompt_per_step for a task from the newest results JSON, if present."""
    files = sorted(glob.glob(os.path.join(RESULTS_DIR, "*.json")))
    if not files:
        return []
    try:
        data = json.load(open(files[-1]))
        for r in data.get("rows", []):
            if r.get("task") == task:
                return r.get("prompt_per_step") or []
    except (OSError, ValueError):
        pass
    return []


# --- html pieces ----------------------------------------------------------------

def fmt(n):
    return f"{n:,}"


def esc(s):
    return html.escape(str(s))


def curve_svg(vals):
    if len(vals) < 2:
        return ""
    ymax = max(500, ((max(vals) + 499) // 500) * 500)
    xs = [70 + i * (186 / (len(vals) - 1)) for i in range(len(vals))]
    ys = [120 - v / ymax * 100 for v in vals]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{4.5 if i == len(vals)-1 else 4}" '
        f'fill="{"var(--orch)" if i == len(vals)-1 else "var(--surface)"}" '
        f'stroke="var(--orch)" stroke-width="2"/>'
        f'<text x="{x:.1f}" y="{y - 8:.1f}" text-anchor="middle" font-size="9.5" '
        f'fill="var(--ink-2)">{fmt(v)}</text>'
        for i, (x, y, v) in enumerate(zip(xs, ys, vals)))
    steps = "".join(
        f'<text x="{x:.1f}" y="134" text-anchor="middle" font-size="9" '
        f'fill="var(--muted)">step {i+1}</text>' for i, x in enumerate(xs))
    half = fmt(ymax // 2)
    return f"""
      <h2 style="font-size:12.5px;">Prompt tokens per step, expr_eval</h2>
      <svg class="curve-svg" viewBox="0 0 300 150" role="img"
           aria-label="Prompt tokens per step: {', '.join(map(str, vals))}">
        <line x1="36" y1="120" x2="290" y2="120" stroke="var(--baseline)" stroke-width="1"/>
        <line x1="36" y1="70" x2="290" y2="70" stroke="var(--grid)" stroke-width="1"/>
        <line x1="36" y1="20" x2="290" y2="20" stroke="var(--grid)" stroke-width="1"/>
        <text x="30" y="124" text-anchor="end" font-size="9" fill="var(--muted)">0</text>
        <text x="30" y="74" text-anchor="end" font-size="9" fill="var(--muted)">{half}</text>
        <text x="30" y="24" text-anchor="end" font-size="9" fill="var(--muted)">{fmt(ymax)}</text>
        <polyline points="{pts}" fill="none" stroke="var(--orch)" stroke-width="2"/>
        {dots}{steps}
      </svg>
      <p class="curve-caption">Flat-ish and linear means the history stubbing is doing its job.</p>"""


def story_panel(story, curve):
    if not story:
        return ""
    before, after = story
    after_total = total_tokens(after)
    pct = round((before - after_total) / before * 100)
    width = after_total / before * 100
    right = ""
    if curve:
        right = f'<div class="panel">{curve_svg(curve)}</div>'
    return f"""
  <section>
    <h2>The expr_eval turnaround</h2>
    <p class="h2-note">The hard task. The first attempt drowned in its own transcript;
    with budgets, error recovery, and context hygiene it passes.</p>
    <div class="split2">
      <div class="panel">
        <div class="ba-row">
          <div class="ba-head">first try<span class="date">before phase 0</span></div>
          <div class="ba-track">
            <div class="ba-bar" style="width:100%;background:var(--critical);"></div>
            <div class="ba-val" style="right:4px;color:#fff;">{fmt(before)} tok · fail</div>
          </div>
        </div>
        <div class="ba-row">
          <div class="ba-head">latest<span class="date">phase 0+1</span></div>
          <div class="ba-track">
            <div class="ba-bar" style="width:{width:.2f}%;"></div>
            <div class="ba-val" style="left:calc({width:.2f}% + 8px);">{fmt(after_total)} tok ·
              {after['steps']} steps · <span class="pill pass">pass ✓</span>
              <span class="up-good">−{pct}%</span></div>
          </div>
        </div>
        <div class="ba-row">
          <div class="ba-head"></div>
          <div class="ba-track" style="border-top:1px solid var(--baseline);height:14px;">
            <div class="ba-val" style="left:0;top:1px;color:var(--muted);font-size:10.5px;">0</div>
            <div class="ba-val" style="right:0;top:1px;color:var(--muted);font-size:10.5px;">{fmt(before)} tokens</div>
          </div>
        </div>
      </div>
      {right}
    </div>
  </section>"""


def task_section(tasks):
    if not tasks:
        return ""
    maxtok = max(total_tokens(r) for r in tasks)
    bars, trs = [], []
    for r in tasks:
        orch = int(r["prompt_tokens"]) + int(r["output_tokens"])
        coder = int(r["coder_prompt_tokens"]) + int(r["coder_output_tokens"])
        tot = orch + coder
        tip = (f"{esc(r['task'])}&#10;orchestrator {fmt(orch)} tok&#10;coder {fmt(coder)} tok"
               f"&#10;{r['steps']} steps · {r['wall_secs']}s")
        bars.append(f"""
        <div class="task-row" tabindex="0" data-tip="{tip}">
          <div class="task-name">{esc(r['task'])}</div>
          <div class="task-track">
            <div class="seg orch" style="width:{orch / maxtok * 100:.2f}%"></div>
            <div class="seg coder" style="width:{coder / maxtok * 100:.2f}%"></div>
          </div>
          <div class="val">{fmt(tot)}</div>
        </div>""")
        trs.append(f"<tr><td>{esc(r['task'])}</td><td class='num'>{fmt(orch)}</td>"
                   f"<td class='num'>{fmt(coder)}</td><td class='num'>{fmt(tot)}</td>"
                   f"<td class='num'>{r['steps']}</td><td class='num'>{r['wall_secs']}s</td></tr>")
    ticks = "".join(
        f'<span style="left:{v / maxtok * 100:.1f}%;">{v // 1000}k</span>'
        for v in range(4000, maxtok + 1, 4000))
    return f"""
  <section>
    <h2>Latest run, task by task</h2>
    <p class="h2-note">Total tokens per task, split by who spent them. Hover a row for
    steps and wall clock.</p>
    <div class="panel">
      <div class="legend">
        <span><span class="swatch" style="background:var(--orch);"></span>orchestrator (cloud)</span>
        <span><span class="swatch" style="background:var(--coder);"></span>coder (local)</span>
      </div>
      <div id="taskbars">{''.join(bars)}</div>
      <div class="axis"><div></div>
        <div class="axis-track"><span class="t0" style="left:0;">0</span>{ticks}</div>
      <div></div></div>
      <details><summary>Table view</summary>
        <table>
          <thead><tr><th>task</th><th class="num">orch tok</th><th class="num">coder tok</th>
          <th class="num">total</th><th class="num">steps</th><th class="num">wall</th></tr></thead>
          <tbody>{''.join(trs)}</tbody>
        </table>
      </details>
    </div>
  </section>"""


def history_section(runs):
    if not runs:
        return ""
    runs = sorted(runs, key=lambda g: g["date"], reverse=True)  # newest first
    trs = []
    for g in runs:
        ok = g["passed"] == g["tasks"]
        pill = "pass" if ok else "fail"
        star = " *" if g["starred"] else ""
        trs.append(
            f"<tr><td class='mono'>{esc(g['date'])}</td><td>{esc(g['label'])}</td>"
            f"<td class='mono'>{esc(g['commit'])}</td>"
            f"<td><span class='pill {pill}'>{g['passed']}/{g['tasks']}</span></td>"
            f"<td class='num'>{g['steps']}</td><td class='num'>{fmt(g['tokens'])}{star}</td></tr>")
    return f"""
  <section>
    <h2>Run history</h2>
    <div class="panel" style="overflow-x:auto;">
      <table>
        <thead><tr><th>date</th><th>run</th><th>commit</th><th>result</th>
        <th class="num">steps</th><th class="num">tokens</th></tr></thead>
        <tbody>{''.join(trs)}</tbody>
      </table>
      <p class="note">* Ran before coder-token accounting landed, so the starred total
      undercounts its true cost.</p>
    </div>
  </section>"""


CSS = """
  :root { --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink-2:#52514e;
    --muted:#898781; --grid:#e1e0d9; --baseline:#c3c2b7; --ring:rgba(11,11,11,0.10);
    --orch:#2a78d6; --coder:#1baf7a; --good:#0ca30c; --good-text:#006300;
    --critical:#d03b3b; --track:#f0efec; }
  @media (prefers-color-scheme: dark) { :root { --page:#0d0d0d; --surface:#1a1a19;
    --ink:#fff; --ink-2:#c3c2b7; --muted:#898781; --grid:#2c2c2a; --baseline:#383835;
    --ring:rgba(255,255,255,0.10); --orch:#3987e5; --coder:#199e70; --good:#0ca30c;
    --good-text:#0ca30c; --critical:#d03b3b; --track:#262624; } }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--page); color:var(--ink);
    font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; }
  .mono,h1,h2,.tile-value,.val,.axis,.chip,.pill,th,td.num
    { font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace; }
  .wrap { max-width:960px; margin:0 auto; padding:40px 24px 64px; }
  header { display:flex; flex-wrap:wrap; align-items:baseline; gap:12px; margin-bottom:8px; }
  h1 { font-size:22px; font-weight:600; margin:0; letter-spacing:-0.01em; }
  .chip { font-size:12px; padding:3px 10px; border-radius:999px;
    border:1px solid var(--ring); color:var(--ink-2); background:var(--surface); }
  .chip.pass { color:var(--good-text); border-color:var(--good); }
  .sub { color:var(--ink-2); margin:0 0 28px; max-width:62ch; }
  .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
    gap:12px; margin-bottom:28px; }
  .tile { background:var(--surface); border:1px solid var(--ring); border-radius:10px;
    padding:14px 16px 12px; }
  .tile-label { font-size:11px; text-transform:uppercase; letter-spacing:0.08em;
    color:var(--muted); }
  .tile-value { font-size:28px; font-weight:600; margin:2px 0 0; }
  .tile-note { font-size:12px; color:var(--ink-2); margin-top:2px; }
  .up-good { color:var(--good-text); }
  section { margin-bottom:32px; }
  h2 { font-size:14px; font-weight:600; letter-spacing:0.02em; margin:0 0 4px; }
  .h2-note { font-size:13px; color:var(--ink-2); margin:0 0 14px; max-width:68ch; }
  .panel { background:var(--surface); border:1px solid var(--ring); border-radius:10px;
    padding:18px 18px 16px; }
  .ba-row { display:grid; grid-template-columns:110px 1fr; gap:10px; align-items:center;
    margin:10px 0; }
  .ba-head { font-size:13px; }
  .ba-head .date { color:var(--muted); font-size:11.5px; display:block; }
  .ba-track { position:relative; height:26px; }
  .ba-bar { height:18px; margin-top:4px; border-radius:0 4px 4px 0;
    background:var(--orch); min-width:3px; }
  .ba-val { position:absolute; top:3px; font-size:12.5px; color:var(--ink-2);
    white-space:nowrap; }
  .pill { display:inline-block; font-size:11px; padding:1px 8px; border-radius:999px;
    margin-left:6px; border:1px solid; }
  .pill.pass { color:var(--good-text); border-color:var(--good); }
  .pill.fail { color:var(--critical); border-color:var(--critical); }
  .split2 { display:grid; grid-template-columns:1.7fr 1fr; gap:14px; }
  @media (max-width:720px) { .split2 { grid-template-columns:1fr; } }
  .legend { display:flex; gap:18px; font-size:12.5px; color:var(--ink-2);
    margin-bottom:12px; }
  .legend span { display:inline-flex; align-items:center; gap:6px; }
  .swatch { width:10px; height:10px; border-radius:3px; display:inline-block; }
  .task-row { display:grid; grid-template-columns:130px 1fr 74px; gap:10px;
    align-items:center; padding:3px 4px; border-radius:6px; cursor:default; }
  .task-row:hover { background:var(--track); }
  .task-row:focus-visible, summary:focus-visible { outline:2px solid var(--orch);
    outline-offset:2px; }
  .task-name { font-size:13px; text-align:right; color:var(--ink-2); overflow:hidden;
    text-overflow:ellipsis; white-space:nowrap; }
  .task-track { display:flex; height:16px; }
  .seg { height:16px; min-width:2px; }
  .seg.orch { background:var(--orch); border-radius:3px 0 0 3px; }
  .seg.coder { background:var(--coder); border-radius:0 4px 4px 0; margin-left:2px; }
  .val { font-size:12.5px; color:var(--ink-2); font-variant-numeric:tabular-nums; }
  .axis { display:grid; grid-template-columns:130px 1fr 74px; gap:10px; margin-top:6px; }
  .axis-track { position:relative; height:16px; font-size:10.5px; color:var(--muted); }
  .axis-track span { position:absolute; transform:translateX(-50%); }
  .axis-track .t0 { transform:none; }
  .curve-svg { width:100%; height:auto; display:block; }
  .curve-caption { font-size:12px; color:var(--muted); margin-top:6px; }
  table { border-collapse:collapse; width:100%; font-size:13.5px; }
  th { text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:0.07em;
    color:var(--muted); font-weight:500; padding:6px 10px;
    border-bottom:1px solid var(--grid); }
  td { padding:8px 10px; border-bottom:1px solid var(--grid); vertical-align:top; }
  tr:last-child td { border-bottom:none; }
  td.num,th.num { text-align:right; font-variant-numeric:tabular-nums;
    white-space:nowrap; }
  .note { font-size:12px; color:var(--muted); margin-top:10px; max-width:70ch; }
  details { margin-top:12px; }
  summary { font-size:12.5px; color:var(--ink-2); cursor:pointer; }
  footer { color:var(--muted); font-size:12.5px; border-top:1px solid var(--grid);
    padding-top:14px; }
  #tip { position:fixed; pointer-events:none; z-index:10; display:none;
    background:var(--ink); color:var(--page); white-space:pre;
    font:12px/1.5 ui-monospace,"SF Mono",Menlo,Consolas,monospace;
    padding:8px 10px; border-radius:6px; max-width:260px; }
  @media (prefers-reduced-motion: reduce) { * { transition:none !important; } }
"""

TOOLTIP_JS = """
  const tip = document.getElementById("tip");
  document.addEventListener("mousemove", e => {
    const row = e.target.closest(".task-row");
    if (!row) { tip.style.display = "none"; return; }
    tip.textContent = row.dataset.tip;
    tip.style.display = "block";
    const pad = 14;
    let x = e.clientX + pad, y = e.clientY + pad;
    const r = tip.getBoundingClientRect();
    if (x + r.width > innerWidth - 8) x = e.clientX - r.width - pad;
    if (y + r.height > innerHeight - 8) y = e.clientY - r.height - pad;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  });
"""


def build():
    rows = load_rows()
    newest, tasks = latest_run(rows)
    if not newest:
        return "<!doctype html><title>bench board</title><p>No bench runs recorded yet."

    passed = sum(int(r["passed"]) for r in tasks)
    tokens = sum(total_tokens(r) for r in tasks)
    wall = round(sum(float(r["wall_secs"]) for r in tasks))
    all_pass = passed == len(tasks)
    story = expr_eval_story(tasks)
    curve = prompt_curve()

    ee_tile = ""
    if story:
        before, after = story
        after_total = total_tokens(after)
        pct = round((before - after_total) / before * 100)
        ee_tile = f"""
    <div class="tile">
      <div class="tile-label">expr_eval tokens</div>
      <div class="tile-value">{fmt(after_total)}</div>
      <div class="tile-note"><span class="up-good">−{pct}%</span> vs the {fmt(before)} failure</div>
    </div>"""

    today = datetime.date.today().isoformat()
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>localHarness · bench board</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>localHarness · bench board</h1>
    <span class="chip {'pass' if all_pass else ''}">latest: {passed}/{len(tasks)} pass</span>
    <span class="chip">commit {esc(newest['commit'])}</span>
    <span class="chip">config: {esc(newest['config'])}</span>
  </header>
  <p class="sub">Eval progress for the Ollama harness. Orchestrator: gemma4:31b-cloud.
  Coder: gpt-oss:20b, local. Regenerated after each bench run.</p>

  <div class="tiles">
    <div class="tile">
      <div class="tile-label">Pass rate</div>
      <div class="tile-value {'up-good' if all_pass else ''}">{passed}/{len(tasks)}</div>
      <div class="tile-note">latest full run</div>
    </div>{ee_tile}
    <div class="tile">
      <div class="tile-label">Suite total tokens</div>
      <div class="tile-value">{fmt(tokens)}</div>
      <div class="tile-note">coder cost included</div>
    </div>
    <div class="tile">
      <div class="tile-label">Suite wall clock</div>
      <div class="tile-value">{wall}s</div>
      <div class="tile-note">{len(tasks)} tasks, budgets on</div>
    </div>
  </div>
{story_panel(story, curve)}
{task_section(tasks)}
{history_section(([first_expr_attempt()] if first_expr_attempt() else []) + run_groups(rows))}
  <footer>
    Data: bench/scores.csv. Generated {today} by bench/board.py.
    Refresh with <span class="mono">python3 bench/board.py</span> (runner.py does it automatically).
  </footer>
</div>
<div id="tip"></div>
<script>{TOOLTIP_JS}</script>
</body>
</html>"""


def main():
    html_text = build()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html_text)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
