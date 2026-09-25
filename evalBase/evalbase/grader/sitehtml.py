"""Static HTML pages for the site bundle `evalbase.grader.export` writes.

The site title and the category list are parameters of `write_html_site`.

Everything here is plain HTML, inline CSS and inline SVG: no script tag, no
external resource, no fetch. A page opens from `file://` as well as from a
server, which is what makes the bundle safe to hand to someone as a directory.
`grader/viewer.html` is the other half -- the interactive JSON explorer, which
does need a server -- and is linked from the leaderboard.

Three page kinds:

  index.html                                   the leaderboard across the runs
  results/<candidate>/index.html               one model
  results/<candidate>/cases/<replay>/index.html  one replay

The pages read the JSON the exporter has already written, so regenerating them
never re-reads an output file. Nothing here computes a score; every number on a page
came out of the grader's report.
"""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

CATEGORIES = ("replay", "procedural", "performance")
SITE_TITLE = "evalbase"

# The site never prints a number as if it were a fact about money, and never
# prints a record claim of its own; both sentences are the site's footer.
COST_FOOTER = (
    "Cost figures are list-price estimates of what the tokens would have cost on "
    "the API (the CLI's own <code>costBasis: list</code>), not money anyone was "
    "billed: these attempts ran on a subscription login. They are an order-of-"
    "magnitude comparison between runs, not spend.")
GRADE_FOOTER = (
    "The headline score of a run is the grade of its final source on an idle host "
    "(the <code>final</code> row of <code>checkpoint-curve.json</code>). The "
    "<em>during-run</em> number beside it is the grade the attempt took of itself "
    "while it was still running and competing with the solver for the machine, so "
    "its performance category is timing-contaminated. A run with no graded final "
    "point has only the during-run number and says so.")
RECORD_FOOTER = (
    "Every row is labelled from <code>labels.json</code>, which is edited by hand. "
    "The exporter can only ever add a row as <em>interim</em>; it cannot promote "
    "one, so nothing on this site claims to be a number of record unless a person "
    "wrote that down.")

STYLE = """
:root { color-scheme: light dark;
  --bg:#12131a; --panel:#1b1d26; --line:#2c2f3d; --fg:#e7e9f0; --dim:#9aa0b4;
  --accent:#7fb4ff; --good:#62c37a; --warn:#e2b04a; --bad:#e05252; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.55 ui-sans-serif,
       system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
header { padding:14px 20px; border-bottom:1px solid var(--line); display:flex; gap:14px;
         align-items:baseline; flex-wrap:wrap; }
header h1 { font-size:16px; margin:0; letter-spacing:.02em; }
main { padding:20px; max-width:1400px; }
h2 { font-size:15px; margin:26px 0 8px; } h2:first-child { margin-top:0; }
h3 { font-size:13px; margin:18px 0 6px; color:var(--dim); text-transform:uppercase;
     letter-spacing:.05em; font-weight:600; }
table { border-collapse:collapse; width:100%; margin:8px 0 18px; }
th,td { text-align:left; padding:5px 10px; border-bottom:1px solid var(--line);
        font-variant-numeric:tabular-nums; vertical-align:top; }
th { color:var(--dim); font-weight:500; font-size:12px; text-transform:uppercase;
     letter-spacing:.05em; }
td.num, th.num { text-align:right; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:8px;
        padding:12px 14px; margin-bottom:14px; }
.kv { display:grid; grid-template-columns:max-content 1fr; gap:3px 16px; font-size:13px; }
.kv div:nth-child(odd) { color:var(--dim); }
.muted { color:var(--dim); } .err { color:var(--bad); } .ok { color:var(--good); }
.warn { color:var(--warn); }
.pill { font-size:11px; padding:1px 8px; border:1px solid var(--line); border-radius:999px;
        color:var(--dim); white-space:nowrap; }
.pill.record { border-color:var(--good); color:var(--good); }
.pill.interim { border-color:var(--warn); color:var(--warn); }
.pill.compromised, .pill.superseded { border-color:var(--bad); color:var(--bad); }
.bar { height:6px; background:#000; border-radius:3px; overflow:hidden; min-width:60px; }
.bar > i { display:block; height:100%; background:var(--accent); }
.panels { display:grid; gap:10px; grid-template-columns:repeat(3,minmax(0,1fr));
          align-items:start; margin-bottom:8px; }
.panels figure { margin:0; } .panels img { width:100%; image-rendering:pixelated;
          border:1px solid var(--line); border-radius:4px; background:#000; }
figcaption { color:var(--dim); font-size:12px; padding-top:4px; }
.grid { display:grid; gap:12px; grid-template-columns:repeat(auto-fill,minmax(210px,1fr)); }
.grid img { width:100%; image-rendering:pixelated; border-radius:4px; background:#000; }
code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }
footer { padding:16px 20px 40px; color:var(--dim); font-size:12px; max-width:1000px; }
ul.reasons { margin:4px 0 0 18px; padding:0; }
.scroll { overflow-x:auto; }
"""


# ------------------------------------------------------------------ formatting

def esc(text) -> str:
    return html.escape("" if text is None else str(text), quote=True)


def num(value, digits: int = 4) -> str:
    if value is None or isinstance(value, bool):
        return "—" if value is None else str(value)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return esc(value)
    if not math.isfinite(value):
        return "—"
    return f"{value:.{digits}f}"


def duration(seconds) -> str:
    """Wall time as a human reads it, with the raw seconds kept beside it."""
    if seconds is None:
        return "—"
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return esc(seconds)
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    text = f"{h} h {m:02d} min" if h else (f"{m} min {s:02d} s" if m else f"{s} s")
    return f"{text} <span class=\"muted\">({seconds:,.0f} s)</span>"


def short(sha, n: int = 12) -> str:
    return esc((sha or "")[:n]) or "—"


def bar(value) -> str:
    try:
        pct = max(0.0, min(1.0, float(value))) * 100
    except (TypeError, ValueError):
        pct = 0.0
    return f'<div class="bar"><i style="width:{pct:.1f}%"></i></div>'


def _read(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def page(title: str, crumb: str, body: str, footer: str = "") -> str:
    return (f"<!doctype html>\n<meta charset=\"utf-8\">\n"
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{esc(title)}</title>\n<style>{STYLE}</style>\n"
            f"<header><h1>{esc(SITE_TITLE)}</h1><span class=\"muted\">{crumb}</span></header>\n"
            f"<main>\n{body}\n</main>\n<footer>{footer}</footer>\n")


# ------------------------------------------------------------------ inline SVG

def line_chart(xs, ys, *, width=760, height=200, ymax=1.0, rule=None, rule_label="",
               rule_color="#e05252", color="#7fb4ff", xlabel="", ylabel="",
               xticks=None) -> str:
    """A plain polyline chart. No script: the tooltips are SVG <title>s."""
    if not xs or not ys or len(xs) != len(ys):
        return '<p class="muted">nothing to plot</p>'
    pad_l, pad_b, pad_t, pad_r = 46, 26, 10, 12
    finite = [v for v in ys if v is not None and math.isfinite(v)]
    top = max([ymax] + [abs(v) for v in finite] + ([rule * 1.4] if rule else [])) or 1.0
    x0, x1 = min(xs), max(xs)
    span = (x1 - x0) or 1.0

    def px(x):
        return pad_l + (x - x0) / span * (width - pad_l - pad_r)

    def py(v):
        return height - pad_b - (max(0.0, min(top, v)) / top) * (height - pad_b - pad_t)

    pts = " ".join(f"{px(x):.1f},{py(v):.1f}" for x, v in zip(xs, ys)
                   if v is not None and math.isfinite(v))
    grid = "".join(
        f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{py(top * f):.1f}" '
        f'y2="{py(top * f):.1f}" stroke="#2c2f3d"/>'
        f'<text x="2" y="{py(top * f) + 4:.1f}" fill="#9aa0b4" font-size="10">'
        f'{top * f:.2f}</text>' for f in (0.0, 0.5, 1.0))
    ruler = ""
    if rule is not None and rule > 0:
        ruler = (f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{py(rule):.1f}" '
                 f'y2="{py(rule):.1f}" stroke="{rule_color}" stroke-dasharray="5 3"/>'
                 f'<text x="{width - pad_r - 2}" y="{py(rule) - 4:.1f}" fill="{rule_color}" '
                 f'font-size="10" text-anchor="end">{esc(rule_label)}</text>')
    dots = "".join(
        f'<circle cx="{px(x):.1f}" cy="{py(v):.1f}" r="3" fill="{color}">'
        f'<title>{esc(x)}: {v:.6g}</title></circle>'
        for x, v in zip(xs, ys) if v is not None and math.isfinite(v))
    ticks = ""
    for tx, label in (xticks or []):
        ticks += (f'<text x="{px(tx):.1f}" y="{height - 8}" fill="#9aa0b4" font-size="10" '
                  f'text-anchor="middle">{esc(label)}</text>')
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'style="max-width:100%;height:auto" role="img">'
            f'<rect x="{pad_l}" y="{pad_t}" width="{width - pad_l - pad_r}" '
            f'height="{height - pad_b - pad_t}" fill="#0c0d12" stroke="#2c2f3d"/>'
            f'{grid}{ruler}<polyline fill="none" stroke="{color}" stroke-width="2" '
            f'points="{pts}"/>{dots}{ticks}'
            f'<text x="2" y="{pad_t - 1}" fill="#9aa0b4" font-size="10">{esc(ylabel)}</text>'
            f'<text x="{width - pad_r}" y="{pad_t - 1}" fill="#9aa0b4" font-size="10" '
            f'text-anchor="end">{esc(xlabel)}</text></svg>')


# ------------------------------------------------------------------ fragments

def grade_source_cell(row: dict) -> str:
    """The during-run number under a headline, with its delta, or the fallback note."""
    record = (row or {}).get("grade_of_record") or {}
    during = (record.get("during_run") or {}).get("overall")
    if record.get("source") != "final_checkpoint":
        return ('<div class="warn" style="font-size:11px">during-run grade '
                '(may be timing-contaminated) — no graded final</div>')
    if during is None:
        return '<div class="muted" style="font-size:11px">idle-host final</div>'
    delta = record.get("delta_vs_during_run")
    sign = "" if delta is None else f" ({delta:+.5f})"
    return (f'<div class="muted" style="font-size:11px">idle-host final · during-run '
            f'{num(during, 5)}{esc(sign)}</div>')


def grade_source_block(record: dict) -> str:
    """The run page's panel saying which report every number on it came from."""
    record = record or {}
    during = record.get("during_run") or {}
    if record.get("source") == "final_checkpoint":
        head = ('<span class="ok">idle-host final</span> — checkpoint '
                f'<code>{esc(record.get("checkpoint") or "final")}</code> of '
                '<code>checkpoint-curve.json</code>')
    else:
        head = ('<span class="warn">during-run grade (may be timing-contaminated)</span>'
                ' — this run has no graded final curve point')
    delta = record.get("delta_vs_during_run")
    return f"""<div class="card"><div class="kv">
  <div>headline</div><div>{num(record.get('overall'), 5)} — {head}</div>
  <div>during-run</div><div>{num(during.get('overall'), 5)}
      <span class="warn">{esc(during.get('label') or '')}</span>
      {'' if delta is None else f'<span class="muted">Δ {delta:+.5f}</span>'}</div>
  <div>report</div><div><code>{esc(record.get('report'))}</code></div>
  <div>during-run report</div><div><code>{esc(during.get('report'))}</code></div>
  <div>why</div><div class="muted">{esc(record.get('note'))}</div>
</div></div>"""


def status_pill(label: dict) -> str:
    status = label.get("status") or "interim"
    return (f'<span class="pill {esc(status)}">{esc(status)}</span>'
            f'<span class="muted"> {esc(label.get("note") or "")}</span>')


def validity_block(row: dict) -> str:
    """measurement_valid, the reasons, and the incidents -- never just a tick."""
    valid = row.get("measurement_valid")
    reasons = row.get("measurement_invalid_reasons") or []
    kinds = row.get("incident_kinds") or []
    n_inc = row.get("n_infrastructure_incidents") or 0
    if valid is True:
        head = '<span class="ok">measurement_valid: true</span>'
    elif valid is False:
        head = '<span class="err">measurement_valid: false</span>'
    else:
        head = '<span class="warn">measurement_valid: not recorded</span>'
    body = [head]
    if reasons:
        body.append('<ul class="reasons">'
                    + "".join(f"<li class=\"err\">{esc(r)}</li>" for r in reasons)
                    + "</ul>")
    else:
        body.append('<div class="muted">measurement_invalid_reasons: none</div>')
    if n_inc:
        body.append(f'<div class="err">{n_inc} infrastructure incident(s): '
                    f'{esc(", ".join(kinds) or "unspecified")}</div>')
    else:
        body.append('<div class="muted">infrastructure_incidents: none</div>')
    body.append('<div class="muted">A valid measurement is not the same as a number of '
                'record: validity is about the harness, the label below is the writer\'s '
                'judgement about the run.</div>')
    return f'<div class="card">{"".join(body)}</div>'


def cost_line(cost: dict | None) -> str:
    if not cost:
        return "—"
    if cost.get("amount_usd") is None:
        return '<span class="muted">not reported</span>'
    klass = "" if cost.get("is_billed_charge") else "warn"
    return (f'<span class="{klass}">{esc(cost.get("label"))}</span>'
            f'<div class="muted" style="font-size:12px">{esc(cost.get("note"))}</div>')


# ------------------------------------------------------------------ leaderboard

def leaderboard_html(board: list[dict], labels: dict) -> str:
    rows = []
    for i, r in enumerate(board, 1):
        label = _label_of(labels, r["candidate"])
        secs = r.get("sections") or {}
        valid = r.get("measurement_valid")
        valid_cell = ('<span class="ok">yes</span>' if valid is True else
                      '<span class="err">no</span>' if valid is False else
                      '<span class="muted">—</span>')
        if r.get("measurement_invalid_reasons"):
            valid_cell += ('<div class="err" style="font-size:11px">'
                           + esc(", ".join(r["measurement_invalid_reasons"])) + "</div>")
        curve = r.get("curve") or {}
        curve_cell = (f'{curve.get("n_graded", 0)}/{curve.get("n_checkpoints", 0)}'
                      if curve.get("present") else
                      f'<span class="muted">0/{curve.get("n_checkpoints", 0)}</span>')
        cost = r.get("cost") or {}
        cost_cell = ("—" if cost.get("amount_usd") is None else
                     f'<span class="muted">~${cost["amount_usd"]:,.0f} est.</span>')
        rows.append(f"""<tr>
      <td class="muted">{i}</td>
      <td><a href="results/{esc(r['candidate'])}/index.html">{esc(r['label'])}</a>
          <div style="margin-top:3px">{status_pill(label)}</div></td>
      <td class="muted">{esc(r.get('model') or '—')}<div style="font-size:11px">
          {esc(r.get('harness') or '')}</div></td>
      <td class="num">{num(r.get('overall'), 5)}<div style="margin-top:3px">
          {bar(r.get('overall'))}</div>{grade_source_cell(r)}</td>
      {"".join(f'<td class="num">{num((secs.get(c) or {}).get("score"))}</td>'
               for c in CATEGORIES)}
      <td class="num">{esc(r.get('n_testcases') or '—')}</td>
      <td class="muted">{esc(r.get('stop_reason') or '—')}</td>
      <td class="num">{'—' if r.get('solver_seconds') is None
                       else f"{float(r['solver_seconds']) / 3600:.2f} h"}</td>
      <td class="num">{curve_cell}</td>
      <td>{valid_cell}</td>
      <td class="num">{cost_cell}</td>
    </tr>""")
    headline = ((labels.get("headline") or {}).get("text")) if isinstance(
        labels.get("headline"), dict) else None
    banner = f'<div class="card">{esc(headline)}</div>' if headline else ""
    heads = "".join(f'<th class="num">{c}</th>' for c in CATEGORIES)
    body = f"""<h2>Leaderboard <span class="muted">({len(board)} run(s), sorted by overall)</span></h2>
{banner}
<div class="scroll"><table>
  <tr><th>#</th><th>Run</th><th>Model</th><th class="num">Overall</th>{heads}
      <th class="num">Cases</th><th>Stop</th><th class="num">Solver</th>
      <th class="num">Curve</th><th>Valid</th><th class="num">Cost est.</th></tr>
  {"".join(rows)}
</table></div>
<p class="muted">Overall is the weighted mean over the categories present
(nominal weights renormalised over what was graded), taken from the run's grade of record: the idle-host grade of its
final source where there is one, with the during-run number and the delta printed
under it. The three category columns are the grader's own
<code>aggregate.categories[*].score</code>; the per-family means, which are a
different rollup and do not average to these, are on each run's page.
<b>Curve</b> is graded checkpoints / checkpoints recorded.
<b>Cost est.</b> is a list-price estimate, never spend.</p>
<h3>Data</h3>
<p class="muted"><a href="leaderboard.json">leaderboard.json</a> ·
<a href="{esc(LABELS_LINK)}">labels.json</a> ·
<a href="viewer.html">viewer.html</a> (interactive JSON explorer; needs
<code>python3 -m http.server</code> in this directory) ·
per run: {" · ".join(f'<a href="results/{esc(r["candidate"])}/summary.json">'
                     f'{esc(r["candidate"])}</a>' for r in board)}</p>"""
    return page(f"{SITE_TITLE} results", "leaderboard", body,
                footer=f"<p>{GRADE_FOOTER}</p><p>{RECORD_FOOTER}</p><p>{COST_FOOTER}</p>")


LABELS_LINK = "labels.json"


def _label_of(labels: dict, candidate: str) -> dict:
    row = (labels.get("runs") or {}).get(candidate)
    return row if isinstance(row, dict) else {"status": "interim", "note": None}


# ------------------------------------------------------------------ run page

def run_html(row: dict, summary: dict, meta: dict, checkpoints: list, curve: dict,
             labels: dict) -> str:
    candidate = summary["candidate"]
    tv = (meta or {}).get("task_version") or {}
    budget = (meta or {}).get("budget") or {}
    sections = summary.get("sections") or {}
    cases = sorted(summary.get("per_testcase", {}).items(),
                   key=lambda kv: (kv[1].get("score") if kv[1].get("score") is not None else 0))

    section_rows = "".join(f"""<tr><td>{esc(k)}</td><td class="num">{num(v.get('score'), 5)}</td>
      <td class="num">{num(v.get('weight'), 2)}</td>
      <td class="num">{num(v.get('effective_weight'), 3)}</td>
      <td class="num">{esc(v.get('n'))}</td>
      <td class="muted">{esc(' · '.join(f'{a} {b:.2f}' for a, b in
                                        sorted((v.get('family_means')
                                                or v.get('subsystems') or {}).items())))}</td>
    </tr>""" for k, v in sections.items())
    family_note = next((v.get("family_means_note") for v in sections.values()
                        if v.get("family_means_note")), None)

    case_rows = "".join(f"""<tr>
      <td><a href="cases/{esc(name)}/index.html">{esc(name)}</a></td>
      <td class="muted">{esc(e.get('section'))}</td>
      <td class="muted">{esc(e.get('subsystem'))}</td>
      <td class="num">{num(e.get('score'))}</td>
      <td class="num">{num(e.get('fidelity_score'))}</td>
      <td class="num">{num(e.get('threshold'), 4)}</td>
      <td class="num">{esc(e.get('n_snapshots'))}</td>
      <td class="{'err' if e.get('exit') != 'ok' else 'muted'}">{esc(e.get('exit'))}</td>
      <td class="muted">{_case_extra(e)}</td>
    </tr>""" for name, e in cases)

    worst = [(n, e) for n, e in cases if e.get("images")][:12]
    thumbs = "".join(f"""<div class="card">
      <a href="cases/{esc(n)}/index.html"><img src="cases/{esc(n)}/{esc(
          e['images'][min(e.get('worst_image_index') or 0,
                          len(e['images']) - 1)]['cand'].split('/')[-1])}" alt=""></a>
      <div style="margin-top:6px"><a href="cases/{esc(n)}/index.html">{esc(n)}</a></div>
      <div class="muted" style="font-size:12px">score {num(e.get('score'))} ·
        {esc(e.get('section'))}</div></div>""" for n, e in worst)

    record = summary.get("grade_of_record") or row.get("grade_of_record") or {}
    during = (record.get("during_run") or {}).get("overall")
    headline_aside = (
        f' <span class="muted">· during-run {num(during, 5)}'
        + ('' if record.get("delta_vs_during_run") is None
           else f' (Δ {record["delta_vs_during_run"]:+.5f})')
        + '</span>' if during is not None else '')

    body = f"""<h2>{esc(summary.get('label'))}
  <span class="muted">overall {num(summary.get('overall'), 5)}</span>{headline_aside}
  {'<span class="pill ok">full success</span>' if summary.get('full_success') else ''}</h2>
<div style="margin-bottom:10px">{status_pill(_label_of(labels, candidate))}</div>

<h3>Grade of record</h3>
{grade_source_block(record)}

<h3>Measurement</h3>
{validity_block(row)}

<h3>Run</h3>
<div class="card"><div class="kv">
  <div>model</div><div>{esc(summary.get('model') or '—')}
      <span class="muted">{esc((meta or {}).get('model_served') or '')}</span></div>
  <div>harness</div><div>{esc(summary.get('harness') or '—')}
      <span class="muted">{esc((meta or {}).get('harness_version') or '')}</span></div>
  <div>stop reason</div><div>{esc((meta or {}).get('stop_reason') or '—')}
      <span class="muted">policy {esc((meta or {}).get('stop_policy') or '—')}</span></div>
  <div>solver wall time</div><div>{duration((meta or {}).get('solver_seconds'))}</div>
  <div>budget</div><div>{esc(budget.get('hours') or '—')} h wall cap, checkpoint every
      {esc(round((budget.get('checkpoint_seconds') or 0) / 60) or '—')} min</div>
  <div>window</div><div>{esc((meta or {}).get('started') or '—')} →
      {esc((meta or {}).get('ended') or '—')}</div>
  <div>graded corpus</div><div>{esc((meta or {}).get('graded_corpus') or '—')}
      <span class="muted">refcache {esc((meta or {}).get('graded_refcache') or '—')}</span></div>
  <div>tokens</div><div>{esc(f"{(row.get('tokens_used') or 0):,}") } in+out
      <span class="muted">({esc(((meta or {}).get('usage') or {}).get('input_tokens'))} in /
      {esc(((meta or {}).get('usage') or {}).get('output_tokens'))} out)</span></div>
  <div>cost</div><div>{cost_line((meta or {}).get('cost'))}</div>
  <div>task version</div><div><code>{short(tv.get('git_commit'))}</code> git ·
      <code>{short(tv.get('corpus_sha256'))}</code> corpus ·
      <code>{short(tv.get('grader_sha256'))}</code> grader ·
      <code>{short(tv.get('harness_sha256'))}</code> harness</div>
  <div>final source</div><div><code>{short(summary.get('candidate_sha256'), 16)}</code></div>
</div></div>

<h2>Scores</h2>
<table>
  <tr><th>Category</th><th class="num">Score</th><th class="num">Weight</th>
      <th class="num">Effective</th><th class="num">n</th>
      <th>Per-family means <span class="muted">(not a rollup of the score)</span></th></tr>
  {section_rows}
  <tr><td><b>overall</b></td><td class="num"><b>{num(summary.get('overall'), 5)}</b></td>
      <td class="num">—</td><td class="num">—</td>
      <td class="num">{esc(summary.get('n'))}</td><td class="muted">weighted mean</td></tr>
</table>
<p class="muted"><b>Score</b> is the grader's own
<code>aggregate.categories[*].score</code> from
<code>{esc(record.get('report') or '')}</code>. The per-family column is
{esc(family_note or 'an unweighted mean over families and does not average to the score')}.</p>

<h2>Checkpoint progression</h2>
{checkpoint_section(curve, checkpoints, summary.get('overall'))}

<h2>Worst replays</h2>
<div class="grid">{thumbs or '<span class="muted">no pictures exported</span>'}</div>

<h2>All replays <span class="muted">({len(cases)}, worst first)</span></h2>
<div class="scroll"><table>
  <tr><th>Replay</th><th>Category</th><th>Family</th><th class="num">Score</th>
      <th class="num">Fidelity</th><th class="num">T</th><th class="num">Snapshots</th>
      <th>Exit</th><th>Notes</th></tr>
  {case_rows}
</table></div>

<h3>Data</h3>
<p class="muted"><a href="summary.json">summary.json</a> ·
<a href="metadata.json">metadata.json</a> ·
<a href="bands.json">bands.json</a> ·
<a href="checkpoints/index.json">checkpoints/index.json</a> ·
<a href="checkpoints/curve.json">checkpoints/curve.json</a></p>"""
    crumb = f'<a href="../../index.html">leaderboard</a> / {esc(candidate)}'
    return page(f"{summary.get('label')} — {SITE_TITLE}", crumb, body,
                footer=f"<p>{GRADE_FOOTER}</p><p>{COST_FOOTER}</p>")


def _case_extra(e: dict) -> str:
    bits = []
    if e.get("checks_total"):
        failed = e.get("checks_failed") or []
        bits.append(f'{e["checks_total"] - len(failed)}/{e["checks_total"]} checks')
    if "ratio" in e:
        bits.append("ratio —" if e.get("ratio") is None
                    else f'ratio {e["ratio"]:.2f}'
                         + ("" if e.get("within_gate") else " (over gate)"))
    if e.get("missing_snapshots"):
        bits.append(f'{e["missing_snapshots"]} snapshot(s) missing')
    if e.get("first_diverge_snapshot") is not None:
        bits.append(f'diverges @{e["first_diverge_snapshot"]}')
    return esc(" · ".join(bits))


def checkpoint_section(curve: dict, checkpoints: list, final_overall) -> str:
    curve = curve or {}
    points = curve.get("points") or []
    out = []
    if len(points) >= 2:
        xs = [(p.get("elapsed_seconds") or 0) / 3600.0 for p in points]
        ys = [p.get("overall") for p in points]
        out.append(line_chart(xs, ys, rule=final_overall, rule_label="final grade",
                              rule_color="#62c37a", xlabel="hours into the attempt",
                              ylabel="overall"))
    elif curve.get("present"):
        out.append('<p class="muted">the curve has fewer than two graded points</p>')
    else:
        out.append(f'<div class="card warn">No checkpoint curve for this run. '
                   f'{esc(curve.get("reason_absent") or "")}<br>'
                   f'<span class="muted">The {curve.get("n_checkpoints", 0)} checkpoints '
                   f'below are what the attempt recorded: source hash, trigger and wall '
                   f'time. Their grades are the missing part, not the points.</span></div>')
    rows = "".join(f"""<tr><td class="num">{esc(c.get('checkpoint_id'))}</td>
      <td class="muted">{esc(c.get('trigger'))}</td>
      <td class="num">{'—' if c.get('elapsed_seconds') is None
                       else f"{c['elapsed_seconds'] / 3600:.2f} h"}</td>
      <td class="{'muted' if c.get('status') in ('ok', 'not_graded') else 'err'}">
          {esc(c.get('status'))}{_ckpt_flags(c)}</td>
      <td class="num">{num(c.get('overall'))}</td>
      <td><code>{short(c.get('source_sha256'), 10)}</code></td>
      <td class="muted">{esc((c.get('note') or '')[:140])}</td></tr>"""
                   for c in (checkpoints or []))
    if rows:
        out.append(f"""<div class="scroll"><table>
  <tr><th class="num">#</th><th>Trigger</th><th class="num">Elapsed</th><th>Status</th>
      <th class="num">Overall</th><th>Source</th><th>Note</th></tr>{rows}</table></div>""")
    else:
        out.append('<p class="muted">no checkpoints recorded for this run</p>')
    return "".join(out)


def _ckpt_flags(c: dict) -> str:
    bits = []
    if c.get("grade_reused"):
        bits.append(f'reused from {c.get("reused_from")}')
    if c.get("skipped"):
        bits.append(str(c["skipped"]))
    if c.get("error"):
        bits.append(str(c["error"]))
    return f' <span class="muted">({esc(" · ".join(bits))})</span>' if bits else ""


# ------------------------------------------------------------------ case page

def case_html(candidate: str, label: str, name: str, e: dict) -> str:
    ref = e.get("reference") or {}
    images = e.get("images") or []
    panels = []
    for img in images:
        cap_c = "candidate" + (" — missing, shown black"
                               if img.get("candidate_missing") else "")
        cap_h = "block defect" + (" — near-uniform candidate, D forced to 1"
                                  if img.get("monochrome_override") else "")
        panels.append(f"""<h3>Snapshot {esc(img.get('index'))} — {esc(img.get('snapshot'))}
      <span class="muted">D {num(img.get('defect'))} · score {num(img.get('score'))}</span></h3>
    <div class="panels">
      <figure><img src="{esc(Path(img['ref']).name)}" alt="reference snapshot">
        <figcaption>reference</figcaption></figure>
      <figure><img src="{esc(Path(img['cand']).name)}" alt="candidate snapshot">
        <figcaption>{esc(cap_c)}</figcaption></figure>
      <figure><img src="{esc(Path(img['heat']).name)}" alt="block-defect heatmap">
        <figcaption>{esc(cap_h)}</figcaption></figure>
    </div>""")
    pictures = "".join(panels) or '<p class="muted">no snapshots exported for this replay</p>'

    scores = e.get("snapshot_scores") or []
    defects = e.get("snapshot_defects") or []
    thr = e.get("threshold") or 0.0
    charts = ""
    if scores:
        charts += ("<h3>Per-snapshot score (Hill)</h3>"
                   + line_chart(list(range(len(scores))), scores, rule=0.5,
                                rule_label="0.5 (D = T)", xlabel="snapshot", ylabel="score"))
    if defects:
        charts += ("<h3>Per-snapshot defect D against the threshold T</h3>"
                   + line_chart(list(range(len(defects))), defects, ymax=max(thr * 1.4, 0.05),
                                rule=thr, rule_label=f"T = {thr:.4f}", color="#e2b04a",
                                xlabel="snapshot", ylabel="D"))

    extra = ""
    if "ratio" in e:
        gate = e.get("perf_gate")
        if e.get("ratio") is None:
            verdict = (f'<span class="err">not measurable</span> '
                       f'<span class="muted">{esc(e.get("ratio_note") or "")}</span>')
        else:
            klass = "ok" if e.get("within_gate") else "err"
            verdict = (f'<span class="{klass}">{e["ratio"]:.3f}×</span> '
                       f'<span class="muted">candidate / reference — '
                       f'{"within" if e.get("within_gate") else "over"} the '
                       f'{num(gate, 1)}× full-success gate</span>')
        extra += f"""<h2>Performance</h2><div class="card"><div class="kv">
      <div>time ratio</div><div>{verdict}</div>
      <div>gate</div><div>{num(gate, 1)}× (MetricSpec.perf_gate — the bar a
          performance case has to clear for full success)</div>
      <div>score half-point</div><div>{num(e.get('perf_half'), 1)}× (PERF_HALF: the ratio
          that scores 0.5)</div>
      <div>candidate wall</div><div>{num(e.get('candidate_wall_seconds'), 3)} s</div>
      <div>reference wall</div><div>{num(e.get('reference_wall_seconds'), 3)} s</div>
      <div>fidelity gate</div><div>fidelity score {num(e.get('fidelity_score'))} — a case whose
          fidelity scores below 0.5 gets no ratio at all</div>
    </div></div>"""
    if e.get("checks_total"):
        failed = e.get("checks_failed") or []
        listing = ("".join(f"<li class=\"err\"><code>{esc(c)}</code></li>" for c in failed)
                   if failed else '<li class="ok">none</li>')
        extra += f"""<h2>Procedural checks</h2><div class="card">
      <div>{e.get('checks_passed', e['checks_total'] - len(failed))}/{e['checks_total']}
      passed{f' — {len(failed)} failed' if failed else ''}</div>
      <h3>Failed checks</h3><ul class="reasons">{listing}</ul></div>"""
    if e.get("device_errors"):
        extra += ('<h2>Device errors</h2><div class="card"><ul class="reasons">'
                  + "".join(f"<li><code>{esc(d)}</code></li>" for d in e["device_errors"])
                  + "</ul></div>")

    # The strip is an APNG when every exported snapshot is the same size and a
    # still when it is not; `anim_note` is why (grader/export.py, export_case).
    strip = ""
    if e.get("anim"):
        kind = ("reference | candidate | block-defect heatmap" if e.get("anim_animated")
                else "reference | candidate | block-defect heatmap — still, not animated")
        note = (f'<div class="warn" style="font-size:12px">{esc(e["anim_note"])}</div>'
                if e.get("anim_note") else "")
        strip = (f'<h2>Combined strip</h2>'
                 f'<img src="{esc(Path(e["anim"]).name)}" style="max-width:100%;'
                 f'image-rendering:pixelated;border:1px solid var(--line);'
                 f'border-radius:4px" alt="">'
                 f'<div class="muted" style="font-size:12px">{kind}</div>{note}')

    body = f"""<h2>{esc(name)} <span class="muted">{esc(e.get('section'))} ·
    {esc(e.get('subsystem'))}</span></h2>
<div class="card"><div class="kv">
  <div>score</div><div>{num(e.get('score'))}{'' if e.get('fidelity_score') == e.get('score')
      else f" <span class=\"muted\">(fidelity score {num(e.get('fidelity_score'))})</span>"}</div>
  <div>threshold T</div><div>{num(e.get('threshold'), 5)}
      <span class="muted">noise {num(ref.get('noise'), 4)} ·
      sensitivity {num(ref.get('sensitivity'), 4)} ·
      motion p90 {num(ref.get('motion_p90'), 4)}</span></div>
  <div>snapshots</div><div>{esc(e.get('n_snapshots'))} scored, {len(images)} exported
      ({esc(e.get('pictures_exported'))})
      {f'<span class="err">— {e["missing_snapshots"]} missing from the candidate</span>'
       if e.get('missing_snapshots') else ''}</div>
  <div>first divergence</div><div>{esc(e.get('first_diverge_snapshot'))
      if e.get('first_diverge_snapshot') is not None else '—'}
      <span class="muted">(first snapshot scoring below 0.5)</span></div>
  <div>exit</div><div class="{'ok' if e.get('exit') == 'ok' else 'err'}">{esc(e.get('exit'))}
      <span class="muted">returncode {esc(e.get('returncode'))}
      {esc(e.get('signal') or '')}</span></div>
  <div>wall</div><div>candidate {num(e.get('candidate_wall_seconds'), 3)} s ·
      reference {num(e.get('reference_wall_seconds'), 3)} s</div>
</div></div>

<h2>Reference vs candidate</h2>
{pictures}
{strip}

<h2>Timeline</h2>
{charts or '<p class="muted">no per-snapshot numbers in the report</p>'}
<p class="muted">One heatmap cell is one metric block. Grey is below the per-block
floor and scores zero; the cool→hot ramp runs from just above the floor to a fully
wrong block, which is red.</p>
{extra}"""
    crumb = (f'<a href="../../../../index.html">leaderboard</a> / '
             f'<a href="../../index.html">{esc(candidate)}</a> / {esc(name)}')
    return page(f"{name} — {label}", crumb, body)


# ------------------------------------------------------------------ entry point

def write_html_site(site, board: list[dict], labels: dict, *, title: str | None = None,
                    categories=None) -> int:
    """Write index.html, a page per run and a page per case. Returns the count."""
    global SITE_TITLE, CATEGORIES
    if title:
        SITE_TITLE = title
    if categories:
        CATEGORIES = tuple(categories)
    site = Path(site)
    site.mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text(leaderboard_html(board, labels))
    pages = 1
    for row in board:
        out = site / "results" / row["candidate"]
        summary = _read(out / "summary.json")
        if not isinstance(summary, dict):
            continue
        meta = _read(out / "metadata.json") or {}
        checkpoints = _read(out / "checkpoints" / "index.json") or []
        curve = _read(out / "checkpoints" / "curve.json") or {}
        (out / "index.html").write_text(
            run_html(row, summary, meta, checkpoints, curve, labels))
        pages += 1
        for name, entry in (summary.get("per_testcase") or {}).items():
            case_dir = out / "cases" / name
            case_dir.mkdir(parents=True, exist_ok=True)
            (case_dir / "index.html").write_text(
                case_html(row["candidate"], summary.get("label") or row["candidate"],
                          name, entry))
            pages += 1
    return pages
