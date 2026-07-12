"""Generate bench/board.html — a self-contained experiment browser from scores.csv.

Opens on the strongest run to date; tabs switch between experiments (one per
full-suite run) and a compare view that lines every config's per-task bars up
side by side. All data is embedded at generation time — one committable file,
no server. runner.py regenerates it after every bench run; by hand:

    python3 bench/board.py
"""

import csv
import datetime
import glob
import json
import os
import sys

BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
SCORES = os.path.join(BENCH, "scores.csv")
SCORES_V0 = os.path.join(BENCH, "scores_v0.csv")
RESULTS_DIR = os.path.join(BENCH, "results")
OUT = os.path.join(BENCH, "board.html")

# Resolve config names to the models they actually ran. Guarded: the board must
# still render from the CSVs alone if the harness modules fail to import.
sys.path.insert(0, ROOT)
sys.path.insert(0, BENCH)
try:
    import agent
    import tools
    from runner import CONFIGS
except Exception:
    agent = tools = None
    CONFIGS = {}

# One-line story per experiment, shown on its tab. Fallback is config_desc().
BLURBS = {
    "split": "The v0 architecture. A big cloud model plans and delegates; a small "
             "local coder writes each file to a spec and self-verifies against a "
             "shell command. The coder never sees the conversation.",
    "gemma-direct": "Ablation: the cloud orchestrator writes code itself. No "
                    "delegation, no local model — tests what the coder adds.",
    "single-gptoss": "No orchestrator. gpt-oss:20b runs the whole loop locally — "
                     "plans, writes, runs, debugs. Fully private. Tool-call "
                     "formatting is its known weak spot.",
    "split-opus9b": "Coder bake-off: the same split, with the coder swapped for a "
                    "9B Opus-4.6 reasoning distill.",
    "split-ds9b": "Coder bake-off: the same split, with the coder swapped for a "
                  "9B DeepSeek-V4-Flash reasoning distill.",
    "single-opus9b": "The Opus-4.6 9B distill running the whole loop alone. Tests "
                     "whether a slow thinker fares better without delegation "
                     "overhead — every step pays the reasoning tax in-transcript.",
    "split-qwen14b": "The v0 coder's big brother: qwen2.5-coder 14B, dense, "
                     "code-gen tuned, no thinking. The purest version of the "
                     "original small-fast-worker thesis.",
    "split-dscoder16b": "DeepSeek-Coder-V2 16B in the coder seat: MoE with 2.4B "
                        "active — the champion's fast shape, but code-specific.",
}


def config_desc(name):
    cfg = CONFIGS.get(name)
    if not cfg or agent is None:
        return ""
    orch = cfg.get("orchestrator") or agent.ORCHESTRATOR
    if cfg.get("direct"):
        return f"{orch} solo — no delegation, writes code itself"
    coder = cfg.get("coder") or tools.CODER_MODEL
    return f"{orch} orchestrates → {coder} writes code"


def series_labels(name):
    """(orch_label, coder_label_or_None) for a config's token bars."""
    cfg = CONFIGS.get(name)
    if not cfg or agent is None:
        return ("orchestrator", "coder")
    orch = cfg.get("orchestrator") or agent.ORCHESTRATOR
    if cfg.get("direct"):
        return (f"{orch} (solo)", None)
    coder = cfg.get("coder") or tools.CODER_MODEL
    return (f"{orch} (orchestrator)", f"{coder} (coder)")


# --- data ------------------------------------------------------------------------

def suite_size():
    tasks_dir = os.path.join(BENCH, "tasks")
    try:
        return sum(os.path.isdir(os.path.join(tasks_dir, d))
                   for d in os.listdir(tasks_dir))
    except OSError:
        return 0


def load_rows():
    if not os.path.exists(SCORES):
        return []
    with open(SCORES) as f:
        return list(csv.DictReader(f))


def load_curves():
    """(config, commit) -> {task: prompt_per_step} from results JSONs, newest wins."""
    curves = {}
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, "*.json"))):
        try:
            data = json.load(open(path))
        except (OSError, ValueError):
            continue
        for r in data.get("rows", []):
            if r.get("prompt_per_step"):
                curves.setdefault((data.get("config"), data.get("commit")), {})[
                    r["task"]] = r["prompt_per_step"]
    return curves


def first_expr_attempt():
    """The solo expr_eval failure from the legacy CSV — the 'before' of this era."""
    if not os.path.exists(SCORES_V0):
        return None
    with open(SCORES_V0) as f:
        for r in csv.DictReader(f):
            if r["tasks"] == "1" and r["passed"] == "0":
                return {"date": r["timestamp"][:10], "commit": r["commit"],
                        "tokens": int(r["total_tokens"]),
                        "steps": int(r["total_steps"])}
    return None


def build_runs():
    """One entry per full-suite (commit, config) run, newest first."""
    n_suite = suite_size()
    curves = load_curves()
    groups = {}
    for r in load_rows():
        groups.setdefault((r["commit"], r["config"]), []).append(r)

    runs = []
    for (commit, config), grp in groups.items():
        by_task = {}
        for r in sorted(grp, key=lambda r: r["timestamp"]):
            by_task[r["task"]] = r
        if len(by_task) < 2:
            continue  # 1-task smoke runs are data, not experiments
        partial = bool(n_suite) and len(by_task) < n_suite  # killed mid-run
        rows = []
        for r in by_task.values():
            orch = int(r["prompt_tokens"]) + int(r["output_tokens"])
            coder = int(r["coder_prompt_tokens"]) + int(r["coder_output_tokens"])
            rows.append({
                "task": r["task"], "passed": int(r["passed"]),
                "status": r["agent_status"], "orch": orch, "coder": coder,
                "steps": int(r["steps"]), "wall": float(r["wall_secs"]),
                "curve": curves.get((config, commit), {}).get(r["task"]),
            })
        rows.sort(key=lambda x: -(x["orch"] + x["coder"]))
        orch_label, coder_label = series_labels(config)
        runs.append({
            "partial": partial,
            "id": f"{config}@{commit}",
            "config": config, "commit": commit,
            "date": max(r["timestamp"] for r in grp)[:10],
            "desc": config_desc(config),
            "blurb": BLURBS.get(config, config_desc(config)),
            "orchLabel": orch_label, "coderLabel": coder_label,
            "passed": sum(r["passed"] for r in rows), "tasks": len(rows),
            "steps": sum(r["steps"] for r in rows),
            "tokens": sum(r["orch"] + r["coder"] for r in rows),
            "wall": round(sum(r["wall"] for r in rows)),
            "rows": rows,
        })
    # Rank strongest first: pass rate, then fewer tokens (the efficiency metric).
    # Partial (killed) runs can't be ranked fairly, so they always sort last.
    runs.sort(key=lambda g: (g["partial"], -(g["passed"] / g["tasks"]), g["tokens"]))
    return runs


FAIL_REASONS = {
    "done": "finished, but the code it wrote fails the tests",
    "llm_error": "gave up after repeated malformed tool calls",
    "timeout": "hit the hard wall-clock kill",
    "budget": "hit the soft wall/token budget",
    "max_steps": "ran out of steps without finishing",
    "error": "crashed with a harness error",
}


def verdict(run, best, n_suite):
    """One-line, data-driven 'what went wrong' relative to the strongest run."""
    if run is best:
        return ("The bar to beat: highest pass rate at the lowest token bill "
                "of any experiment so far.")
    parts = []
    if run["partial"]:
        parts.append(f"run killed after {run['tasks']} of {n_suite} tasks "
                     "(unranked)")
    for f in (r for r in run["rows"] if not r["passed"]):
        reason = FAIL_REASONS.get(f["status"], f["status"])
        parts.append(f"{f['task']} failed — {reason}")
    flaky = [r["task"] for r in run["rows"]
             if r["passed"] and r["status"] == "llm_error"]
    if flaky:
        parts.append(f"{', '.join(flaky)} passed only because partial work "
                     "survived repeated tool-call 500s")
    if best and not run["partial"] and run["tokens"] > best["tokens"] * 1.15:
        parts.append(f"{run['tokens'] / best['tokens']:.1f}× the tokens of "
                     "the strongest run")
    return "; ".join(parts) + "." if parts else \
        "Matched the strongest run on pass rate, just costlier."


def build_data():
    runs = build_runs()
    n_suite = suite_size()
    best = next((r for r in runs if not r["partial"]), None)
    for run in runs:
        run["verdict"] = verdict(run, best, n_suite)
    return {
        "generated": datetime.date.today().isoformat(),
        "suite": n_suite,
        "bestId": best["id"] if best else None,
        "legacy": first_expr_attempt(),
        "runs": runs,
    }


# --- template ---------------------------------------------------------------------

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>localHarness · bench board</title>
<style>
  :root { --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink-2:#52514e;
    --muted:#898781; --grid:#e1e0d9; --baseline:#c3c2b7; --ring:rgba(11,11,11,0.10);
    --good:#0ca30c; --good-text:#006300; --critical:#d03b3b; --track:#f0efec;
    --s1:#2a78d6; --s2:#1baf7a; --s3:#eda100; --s4:#4a3aa7; --s5:#e87ba4; --s6:#eb6834; }
  @media (prefers-color-scheme: dark) { :root { --page:#0d0d0d; --surface:#1a1a19;
    --ink:#fff; --ink-2:#c3c2b7; --muted:#898781; --grid:#2c2c2a; --baseline:#383835;
    --ring:rgba(255,255,255,0.10); --good:#0ca30c; --good-text:#0ca30c;
    --critical:#d03b3b; --track:#262624;
    --s1:#3987e5; --s2:#199e70; --s3:#c98500; --s4:#9085e9; --s5:#d55181; --s6:#d95926; } }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--page); color:var(--ink);
    font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; }
  .mono,h1,h2,.tile-value,.val,.axis-track,.chip,.pill,th,td.num,.tab
    { font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace; }
  .wrap { max-width:980px; margin:0 auto; padding:36px 24px 64px; }
  header { display:flex; flex-wrap:wrap; align-items:baseline; gap:12px; margin-bottom:6px; }
  h1 { font-size:22px; font-weight:600; margin:0; letter-spacing:-0.01em; }
  .sub { color:var(--ink-2); margin:0 0 20px; max-width:70ch; }
  .tabs { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:20px; }
  .tab { font-size:12.5px; padding:6px 14px; border-radius:999px; cursor:pointer;
    border:1px solid var(--ring); background:var(--surface); color:var(--ink-2); }
  .tab:hover { border-color:var(--baseline); }
  .tab[aria-selected="true"] { background:var(--ink); color:var(--page);
    border-color:var(--ink); }
  .tab:focus-visible { outline:2px solid var(--s1); outline-offset:2px; }
  .tab.partial { opacity:0.55; border-style:dashed; }
  .tab.partial[aria-selected="true"] { opacity:0.85; }
  tr.partial-row { opacity:0.55; }
  .chip.warn { color:var(--critical); border-color:var(--critical); }
  .chip { font-size:12px; padding:3px 10px; border-radius:999px;
    border:1px solid var(--ring); color:var(--ink-2); background:var(--surface); }
  .chip.pass { color:var(--good-text); border-color:var(--good); }
  .chip.best { color:var(--good-text); border-color:var(--good); }
  .blurb { font-size:13.5px; color:var(--ink-2); max-width:72ch; margin:2px 0 10px; }
  .verdict { font-size:13px; max-width:72ch; margin:0 0 18px; padding:8px 12px;
    border-left:3px solid var(--baseline); color:var(--ink-2); background:var(--surface);
    border-radius:0 8px 8px 0; }
  .verdict.best-v { border-left-color:var(--good); }
  .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
    gap:12px; margin-bottom:24px; }
  .tile { background:var(--surface); border:1px solid var(--ring); border-radius:10px;
    padding:14px 16px 12px; }
  .tile-label { font-size:11px; text-transform:uppercase; letter-spacing:0.08em;
    color:var(--muted); }
  .tile-value { font-size:27px; font-weight:600; margin:2px 0 0; }
  .tile-note { font-size:12px; color:var(--ink-2); margin-top:2px; }
  .up-good { color:var(--good-text); }
  section { margin-bottom:30px; }
  h2 { font-size:14px; font-weight:600; margin:0 0 4px; }
  .h2-note { font-size:13px; color:var(--ink-2); margin:0 0 14px; max-width:70ch; }
  .panel { background:var(--surface); border:1px solid var(--ring); border-radius:10px;
    padding:18px 18px 16px; }
  .legend { display:flex; flex-wrap:wrap; gap:14px 18px; font-size:12.5px;
    color:var(--ink-2); margin-bottom:12px; }
  .legend span { display:inline-flex; align-items:center; gap:6px; }
  .swatch { width:10px; height:10px; border-radius:3px; display:inline-block;
    flex:none; }
  .task-row { display:grid; grid-template-columns:130px 1fr 84px; gap:10px;
    align-items:center; padding:3px 4px; border-radius:6px; }
  .task-row:hover { background:var(--track); }
  .task-name { font-size:13px; text-align:right; color:var(--ink-2); overflow:hidden;
    text-overflow:ellipsis; white-space:nowrap; }
  .task-track { display:flex; height:16px; }
  .seg { height:16px; min-width:2px; }
  .seg + .seg { margin-left:2px; }
  .val { font-size:12.5px; color:var(--ink-2); font-variant-numeric:tabular-nums; }
  .val .fx { color:var(--critical); }
  .axis { display:grid; grid-template-columns:130px 1fr 84px; gap:10px; margin-top:6px; }
  .axis-track { position:relative; height:16px; font-size:10.5px; color:var(--muted); }
  .axis-track span { position:absolute; transform:translateX(-50%); }
  .axis-track .t0 { transform:none; }
  /* compare: one thin bar per run inside each task group. The value column is
     reserved up front so a full-width bar can never push its label out of the box. */
  .cmp-group { display:grid; grid-template-columns:130px 1fr; gap:10px;
    padding:7px 4px; border-radius:6px; align-items:start; }
  .cmp-group:hover { background:var(--track); }
  .cmp-lines { display:flex; flex-direction:column; gap:3px; }
  .cmp-line { display:grid; grid-template-columns:1fr 92px; gap:8px;
    align-items:center; }
  .cmp-bar { height:12px; border-radius:0 3px 3px 0; min-width:2px; }
  .cmp-val { font-size:11.5px; color:var(--ink-2); white-space:nowrap;
    overflow:hidden; text-overflow:ellipsis; font-variant-numeric:tabular-nums; }
  .ba-row { display:grid; grid-template-columns:110px 1fr; gap:10px;
    align-items:center; margin:10px 0; }
  .ba-head { font-size:13px; }
  .ba-head .date { color:var(--muted); font-size:11.5px; display:block; }
  .ba-track { position:relative; height:26px; }
  .ba-bar { height:18px; margin-top:4px; border-radius:0 4px 4px 0;
    background:var(--s1); min-width:3px; }
  .ba-val { position:absolute; top:3px; font-size:12.5px; color:var(--ink-2);
    white-space:nowrap; }
  .pill { display:inline-block; font-size:11px; padding:1px 8px; border-radius:999px;
    margin-left:6px; border:1px solid; }
  .pill.pass { color:var(--good-text); border-color:var(--good); }
  .pill.fail { color:var(--critical); border-color:var(--critical); }
  .split2 { display:grid; grid-template-columns:1.7fr 1fr; gap:14px;
    align-items:start; }  /* panels hug their content, no stretched empties */
  @media (max-width:720px) { .split2 { grid-template-columns:1fr; } }
  .curve-panel { max-width:460px; }
  .curve-svg { width:100%; height:auto; display:block; }
  .curve-caption { font-size:12px; color:var(--muted); margin-top:6px; }
  table { border-collapse:collapse; width:100%; font-size:13.5px; }
  th { text-align:left; font-size:11px; text-transform:uppercase;
    letter-spacing:0.07em; color:var(--muted); font-weight:500; padding:6px 10px;
    border-bottom:1px solid var(--grid); }
  td { padding:8px 10px; border-bottom:1px solid var(--grid); vertical-align:top; }
  tr:last-child td { border-bottom:none; }
  td.num,th.num { text-align:right; font-variant-numeric:tabular-nums;
    white-space:nowrap; }
  .run-desc { display:block; font-size:11.5px; color:var(--muted); }
  .note { font-size:12px; color:var(--muted); margin-top:10px; max-width:70ch; }
  details { margin-top:12px; }
  summary { font-size:12.5px; color:var(--ink-2); cursor:pointer; }
  summary:focus-visible { outline:2px solid var(--s1); outline-offset:2px; }
  footer { color:var(--muted); font-size:12.5px; border-top:1px solid var(--grid);
    padding-top:14px; }
  #tip { position:fixed; pointer-events:none; z-index:10; display:none;
    background:var(--ink); color:var(--page); white-space:pre;
    font:12px/1.5 ui-monospace,"SF Mono",Menlo,Consolas,monospace;
    padding:8px 10px; border-radius:6px; max-width:280px; }
  @media (prefers-reduced-motion: reduce) { * { transition:none !important; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>localHarness · bench board</h1>
    <span class="chip" id="hdr-chip"></span>
  </header>
  <p class="sub">Every experiment this harness has run, one tab each. Opens on the
  strongest result to date; Compare lines them up task by task.</p>
  <nav class="tabs" id="tabs" role="tablist"></nav>
  <main id="panel"></main>
  <footer>
    Data: bench/scores.csv · generated <span id="gen"></span> by bench/board.py.
    Refresh with <span class="mono">python3 bench/board.py</span> (runner.py does it
    automatically after each run).
  </footer>
</div>
<div id="tip"></div>
<script>
const DATA = __DATA__;
const SLOTS = ["var(--s1)","var(--s2)","var(--s3)","var(--s4)","var(--s5)","var(--s6)"];
const fmt = n => n.toLocaleString("en-US");
const esc = s => String(s).replace(/[&<>"]/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

// stable color per run: index in date-ascending order, so colors never repaint
const ordered = [...DATA.runs].sort((a, b) => a.date < b.date ? -1 : 1);
const runColor = {};
ordered.forEach((r, i) => { runColor[r.id] = SLOTS[i % SLOTS.length]; });

function tile(label, value, note, cls) {
  return `<div class="tile"><div class="tile-label">${label}</div>` +
         `<div class="tile-value ${cls || ""}">${value}</div>` +
         `<div class="tile-note">${note || ""}</div></div>`;
}

function pill(ok) {
  return ok ? '<span class="pill pass">pass ✓</span>'
            : '<span class="pill fail">fail ✗</span>';
}

function curveSvg(vals) {
  if (!vals || vals.length < 2) return "";
  const ymax = Math.max(500, Math.ceil(Math.max(...vals) / 500) * 500);
  const xs = vals.map((_, i) => 70 + i * (186 / (vals.length - 1)));
  const ys = vals.map(v => 120 - v / ymax * 100);
  const pts = xs.map((x, i) => `${x.toFixed(1)},${ys[i].toFixed(1)}`).join(" ");
  let dots = "", steps = "";
  vals.forEach((v, i) => {
    const last = i === vals.length - 1;
    dots += `<circle cx="${xs[i].toFixed(1)}" cy="${ys[i].toFixed(1)}"` +
      ` r="${last ? 4.5 : 4}" fill="${last ? "var(--s1)" : "var(--surface)"}"` +
      ` stroke="var(--s1)" stroke-width="2"/>` +
      `<text x="${xs[i].toFixed(1)}" y="${(ys[i] - 8).toFixed(1)}"` +
      ` text-anchor="middle" font-size="9.5" fill="var(--ink-2)">${fmt(v)}</text>`;
    steps += `<text x="${xs[i].toFixed(1)}" y="134" text-anchor="middle"` +
      ` font-size="9" fill="var(--muted)">step ${i + 1}</text>`;
  });
  return `<h2 style="font-size:12.5px;">Prompt tokens per step, expr_eval</h2>
    <svg class="curve-svg" viewBox="0 0 300 150" role="img"
         aria-label="Prompt tokens per step">
      <line x1="36" y1="120" x2="290" y2="120" stroke="var(--baseline)" stroke-width="1"/>
      <line x1="36" y1="70" x2="290" y2="70" stroke="var(--grid)" stroke-width="1"/>
      <line x1="36" y1="20" x2="290" y2="20" stroke="var(--grid)" stroke-width="1"/>
      <text x="30" y="124" text-anchor="end" font-size="9" fill="var(--muted)">0</text>
      <text x="30" y="74" text-anchor="end" font-size="9" fill="var(--muted)">${fmt(ymax/2)}</text>
      <text x="30" y="24" text-anchor="end" font-size="9" fill="var(--muted)">${fmt(ymax)}</text>
      <polyline points="${pts}" fill="none" stroke="var(--s1)" stroke-width="2"/>
      ${dots}${steps}
    </svg>
    <p class="curve-caption">Flat-ish and linear means history stubbing is doing its job.</p>`;
}

function axisTicks(maxTok) {
  const step = maxTok > 20000 ? 8000 : 4000;
  let t = "";
  for (let v = step; v <= maxTok; v += step)
    t += `<span style="left:${(v / maxTok * 100).toFixed(1)}%;">${v / 1000}k</span>`;
  return `<div class="axis"><div></div><div class="axis-track">` +
         `<span class="t0" style="left:0;">0</span>${t}</div><div></div></div>`;
}

function renderRun(run, isBest) {
  const rate = `${run.passed}/${run.tasks}`;
  const maxTok = Math.max(...run.rows.map(r => r.orch + r.coder));
  const expr = run.rows.find(r => r.task === "expr_eval");
  const twoSeries = run.coderLabel !== null;

  let banner = "";
  if (isBest) banner = `<span class="chip best">★ strongest to date</span> `;
  if (run.partial) banner = `<span class="chip warn">✂ killed at ` +
    `${run.tasks}/${DATA.suite} tasks — unranked</span> `;

  let legend = `<div class="legend"><span><span class="swatch"` +
    ` style="background:var(--s1);"></span>${esc(run.orchLabel)}</span>`;
  if (twoSeries) legend += `<span><span class="swatch"` +
    ` style="background:var(--s2);"></span>${esc(run.coderLabel)}</span>`;
  legend += `</div>`;

  let bars = "", trs = "";
  for (const r of run.rows) {
    const tot = r.orch + r.coder;
    const failMark = r.passed ? "" : ' <span class="fx">✗</span>';
    const tipText = `${r.task}${r.passed ? "" : "  (FAIL)"}\n` +
      (twoSeries ? `orchestrator ${fmt(r.orch)} tok\ncoder ${fmt(r.coder)} tok\n`
                 : `${fmt(tot)} tok\n`) +
      `${r.steps} steps · ${r.wall}s · status ${r.status}`;
    bars += `<div class="task-row" tabindex="0" data-tip="${esc(tipText)}">` +
      `<div class="task-name">${esc(r.task)}</div><div class="task-track">` +
      `<div class="seg" style="background:var(--s1);border-radius:3px 0 0 3px;` +
      `width:${(r.orch / maxTok * 100).toFixed(2)}%"></div>` +
      (twoSeries && r.coder ? `<div class="seg" style="background:var(--s2);` +
        `border-radius:0 4px 4px 0;width:${(r.coder / maxTok * 100).toFixed(2)}%"></div>` : "") +
      `</div><div class="val">${fmt(tot)}${failMark}</div></div>`;
    trs += `<tr><td>${esc(r.task)}${r.passed ? "" : " ✗"}</td>` +
      `<td class="num">${fmt(r.orch)}</td><td class="num">${fmt(r.coder)}</td>` +
      `<td class="num">${fmt(tot)}</td><td class="num">${r.steps}</td>` +
      `<td class="num">${r.wall}s</td></tr>`;
  }

  let turnaround = "";
  if (isBest && DATA.legacy && expr) {
    const before = DATA.legacy.tokens, after = expr.orch + expr.coder;
    const pct = Math.round((before - after) / before * 100);
    const w = Math.min(after / before * 100, 100);
    // wide bar -> label sits inside it, right-aligned, so it can't overflow the box
    const afterVal = w > 55
      ? `<div class="ba-val" style="right:4px;color:#fff;">${fmt(after)} tok · ${expr.steps} steps ${pill(expr.passed)}</div>`
      : `<div class="ba-val" style="left:calc(${w.toFixed(2)}% + 8px);">${fmt(after)} tok · ${expr.steps} steps · ${pill(expr.passed)} <span class="up-good">−${pct}%</span></div>`;
    turnaround = `<section><h2>The expr_eval turnaround</h2>
      <p class="h2-note">The hard task. The first attempt drowned in its own
      transcript; with budgets, error recovery, and context hygiene it passes.</p>
      <div class="split2"><div class="panel">
        <div class="ba-row"><div class="ba-head">first try<span class="date">before
          phase 0</span></div><div class="ba-track">
          <div class="ba-bar" style="width:100%;background:var(--critical);"></div>
          <div class="ba-val" style="right:4px;color:#fff;">${fmt(before)} tok · fail</div>
        </div></div>
        <div class="ba-row"><div class="ba-head">this run<span class="date">${esc(run.config)}</span></div>
        <div class="ba-track"><div class="ba-bar" style="width:${w.toFixed(2)}%;"></div>
          ${afterVal}
        </div></div>
        <div class="ba-row"><div class="ba-head"></div>
        <div class="ba-track" style="border-top:1px solid var(--baseline);height:14px;">
          <div class="ba-val" style="left:0;top:1px;color:var(--muted);font-size:10.5px;">0</div>
          <div class="ba-val" style="right:0;top:1px;color:var(--muted);font-size:10.5px;">${fmt(before)} tokens</div>
        </div></div>
      </div>` +
      (expr.curve ? `<div class="panel">${curveSvg(expr.curve)}</div>` : "") +
      `</div></section>`;
  }

  // Every run tab gets the expr_eval growth curve when it exists; the best tab
  // already shows it inside the turnaround panel.
  let curveSection = "";
  if (expr && expr.curve && !turnaround) {
    curveSection = `<section><div class="panel curve-panel">` +
                   `${curveSvg(expr.curve)}</div></section>`;
  }

  return `
    <p class="blurb">${banner}${esc(run.blurb)}</p>
    <p class="verdict ${isBest ? "best-v" : ""}">${esc(run.verdict)}</p>
    <div class="tiles">
      ${tile("Pass rate", rate, `${esc(run.date)} · commit ${esc(run.commit)}`,
             run.passed === run.tasks ? "up-good" : "")}
      ${tile("Total tokens", fmt(run.tokens), "orchestrator + coder")}
      ${tile("Wall clock", run.wall + "s", `${run.steps} steps across ${run.tasks} tasks`)}
      ${expr ? tile("expr_eval", fmt(expr.orch + expr.coder),
                    expr.passed ? "tokens · passed" : "tokens · failed") : ""}
    </div>
    ${turnaround}${curveSection}
    <section><h2>Task by task</h2>
    <p class="h2-note">${esc(run.desc)}. Hover a row for detail.</p>
    <div class="panel">${legend}<div>${bars}</div>${axisTicks(maxTok)}
      <details><summary>Table view</summary><table>
        <thead><tr><th>task</th><th class="num">orch tok</th><th class="num">coder tok</th>
        <th class="num">total</th><th class="num">steps</th><th class="num">wall</th></tr></thead>
        <tbody>${trs}</tbody></table></details>
    </div></section>`;
}

function renderCompare() {
  const runs = DATA.runs;
  if (!runs.length) return "<p>No full-suite runs yet.</p>";

  let legend = `<div class="legend">` + runs.map(r =>
    `<span${r.partial ? ' style="opacity:0.55"' : ""}><span class="swatch"` +
    ` style="background:${runColor[r.id]};"></span>` +
    `${esc(r.config)} · ${esc(r.date)}${r.partial ? " (killed)" : ""}</span>`)
    .join("") + `</div>`;

  // union of tasks, ordered by the max total across runs
  const taskMax = {};
  for (const run of runs)
    for (const r of run.rows)
      taskMax[r.task] = Math.max(taskMax[r.task] || 0, r.orch + r.coder);
  const tasks = Object.keys(taskMax).sort((a, b) => taskMax[b] - taskMax[a]);
  const maxTok = Math.max(...Object.values(taskMax));

  let groups = "";
  for (const task of tasks) {
    let lines = "";
    for (const run of runs) {
      const r = run.rows.find(x => x.task === task);
      if (!r) continue;
      const tot = r.orch + r.coder;
      lines += `<div class="cmp-line"${run.partial ? ' style="opacity:0.55"' : ""}>` +
        `<div class="cmp-bar" style="background:${runColor[run.id]};` +
        `width:${(tot / maxTok * 100).toFixed(2)}%"></div>` +
        `<span class="cmp-val">${fmt(tot)}${r.passed ? "" : ' <span class="fx" style="color:var(--critical)">✗</span>'}</span></div>`;
    }
    groups += `<div class="cmp-group"><div class="task-name" style="padding-top:1px;">${esc(task)}</div>` +
              `<div class="cmp-lines">${lines}</div></div>`;
  }

  let trs = runs.map((r, i) =>
    `<tr${r.partial ? ' class="partial-row"' : ""}>` +
    `<td class="mono">${r.partial ? "—" : "#" + (i + 1)}</td>` +
    `<td>${esc(r.config)} <span class="mono" style="font-size:11px;color:var(--muted)">${esc(r.date)}</span>` +
    `<span class="run-desc">${esc(r.desc)}</span>` +
    `<span class="run-desc">${esc(r.verdict)}</span></td>` +
    `<td class="mono">${esc(r.commit)}</td><td>${pill(r.passed === r.tasks)}` +
    ` <span class="mono" style="font-size:12px;">${r.passed}/${r.tasks}</span></td>` +
    `<td class="num">${r.steps}</td><td class="num">${fmt(r.tokens)}</td>` +
    `<td class="num">${r.wall}s</td></tr>`).join("");

  let legacyNote = "";
  if (DATA.legacy) legacyNote = `<p class="note">Before this era: expr_eval's first
    attempt (${esc(DATA.legacy.date)}, commit ${esc(DATA.legacy.commit)}) failed at
    ${fmt(DATA.legacy.tokens)} tokens — it predates coder-token accounting, so even
    that undercounts.</p>`;

  return `
    <p class="blurb">Every full-suite experiment, side by side. Total tokens per
    task, one bar per experiment; ✗ marks a failed task.</p>
    <section><h2>Task by task, all experiments</h2>
    <div class="panel">${legend}${groups}</div></section>
    <section><h2>Scoreboard — strongest to weakest</h2>
    <div class="panel" style="overflow-x:auto;"><table>
      <thead><tr><th>rank</th><th>experiment</th><th>commit</th><th>result</th>
      <th class="num">steps</th><th class="num">tokens</th><th class="num">wall</th></tr></thead>
      <tbody>${trs}</tbody></table>${legacyNote}</div></section>`;
}

// --- tabs ---------------------------------------------------------------------
const tabsEl = document.getElementById("tabs");
const panelEl = document.getElementById("panel");
// run tabs come pre-ranked strongest -> weakest from the generator
const tabs = [{ id: "best", label: "★ Best" }, { id: "compare", label: "Compare" }]
  .concat(DATA.runs.map(r => ({
    id: r.id, partial: r.partial,
    label: `${r.config} · ${r.partial ? "killed" : r.passed + "/" + r.tasks}`,
  })));

function select(id) {
  for (const b of tabsEl.children)
    b.setAttribute("aria-selected", b.dataset.id === id ? "true" : "false");
  if (id === "compare") panelEl.innerHTML = renderCompare();
  else {
    const runId = id === "best" ? DATA.bestId : id;
    const run = DATA.runs.find(r => r.id === runId);
    panelEl.innerHTML = run ? renderRun(run, runId === DATA.bestId)
                            : "<p>No runs recorded yet.</p>";
  }
}

for (const t of tabs) {
  const b = document.createElement("button");
  b.className = "tab" + (t.partial ? " partial" : "");
  b.role = "tab"; b.dataset.id = t.id; b.textContent = t.label;
  b.addEventListener("click", () => select(t.id));
  tabsEl.appendChild(b);
}

const best = DATA.runs.find(r => r.id === DATA.bestId);
document.getElementById("hdr-chip").textContent = best
  ? `best: ${best.passed}/${best.tasks} · ${fmt(best.tokens)} tok · ${best.config}`
  : "no runs yet";
document.getElementById("gen").textContent = DATA.generated;
select("best");

// --- tooltip --------------------------------------------------------------------
const tip = document.getElementById("tip");
document.addEventListener("mousemove", e => {
  const row = e.target.closest("[data-tip]");
  if (!row) { tip.style.display = "none"; return; }
  tip.textContent = row.dataset.tip;
  tip.style.display = "block";
  const pad = 14;
  let x = e.clientX + pad, y = e.clientY + pad;
  const r = tip.getBoundingClientRect();
  if (x + r.width > innerWidth - 8) x = e.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = e.clientY - r.height - pad;
  tip.style.left = x + "px"; tip.style.top = y + "px";
});
</script>
</body>
</html>"""


def main():
    data = build_data()
    html_text = TEMPLATE.replace("__DATA__", json.dumps(data))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html_text)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
