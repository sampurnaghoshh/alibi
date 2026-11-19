"""
Printable investigation report (CLAUDE.md §8: GET /api/investigations/{id}/report). Owner: Sampurna.

One self-contained HTML page: inline CSS, A4 print layout, artifact images embedded as
data URIs so "Save as PDF" works offline. Only reads the Investigation (and the run's
artifacts folder for images); never changes anything.

All fixed text here must avoid the forbidden words of §9 (tests/test_report.py checks).
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from html import escape as e
from typing import Any, Iterable, Optional

from app import config
from app.engine.summary import summarize
from app.schemas import Finding, Investigation
from app.storage import RunStore, now

STATUS_LABEL = {
    "consistent": "Consistent",
    "contradiction": "Contradiction",
    "inconclusive": "Inconclusive",
    "insufficient_signal": "Insufficient signal",
    "not_applicable": "Not applicable",
}
# Contradictions first, not_applicable last; within a status, most reliable first.
# Count pills: (singular, plural). Status adjectives don't take a plural.
COUNT_WORDS = {
    "contradiction": ("contradiction", "contradictions"),
    "consistent": ("consistent", "consistent"),
    "inconclusive": ("inconclusive", "inconclusive"),
    "insufficient_signal": ("with insufficient signal", "with insufficient signal"),
    "not_applicable": ("not applicable", "not applicable"),
}
STATUS_ORDER = {"contradiction": 0, "consistent": 1, "inconclusive": 2, "insufficient_signal": 3, "not_applicable": 4}
RELIABILITY_ORDER = {"high": 0, "medium": 1, "low": 2}
FAMILY_LABEL = {"context": "Context", "signal": "Signal", "pixels": "Pixels"}

PAIR_KEYS = ("frame_matches",)      # prior_appearance Time Machine pairs: drawn by _pairs_html
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
MAX_IMAGES_PER_FINDING = 6
MAX_IMAGE_BYTES = 3 * 1024 * 1024

CSS = """
:root { --ink:#1b1f24; --muted:#5b6470; --line:#d6dae0; --bg:#ffffff; --soft:#f4f6f8;
        --red:#b42318; --green:#127a3a; --grey:#5b6470; --accent:#1f4e8c; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:10.5pt/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
main { max-width:190mm; margin:0 auto; padding:12mm 0; }
h1 { font-size:20pt; margin:0; letter-spacing:.08em; }
h2 { font-size:13pt; margin:18px 0 8px; padding-bottom:4px; border-bottom:2px solid var(--ink); }
h3 { font-size:11pt; margin:0; }
code, .mono { font-family:ui-monospace,"Cascadia Mono",Consolas,monospace; font-size:9pt; }
.break { word-break:break-all; }
.muted { color:var(--muted); }
.top { display:flex; justify-content:space-between; align-items:flex-end; gap:12px; }
.meta { text-align:right; font-size:9pt; color:var(--muted); }
.banner { margin:12px 0; padding:8px 12px; border:2px solid var(--ink); font-weight:600; background:var(--soft); }
.headline { font-size:14pt; font-weight:600; margin:4px 0 8px; }
.pills span, .chip { display:inline-block; padding:1px 8px; margin:0 4px 4px 0; border-radius:10px; border:1px solid var(--line); font-size:9pt; }
.s-contradiction { color:var(--red); border-color:var(--red); }
.s-consistent { color:var(--green); border-color:var(--green); }
.s-inconclusive, .s-not_applicable { color:var(--grey); border-color:var(--grey); }
.s-insufficient_signal { color:var(--grey); border:1px dashed var(--grey); }
.caption { padding:10px 12px; background:var(--soft); border-left:4px solid var(--accent); font-size:11pt; }
mark { background:#fff1b8; padding:0 2px; border-bottom:2px solid #c99a00; }
mark sup { font-size:7pt; color:var(--muted); margin-left:1px; }
table { width:100%; border-collapse:collapse; font-size:9pt; }
th, td { text-align:left; vertical-align:top; padding:3px 6px; border-bottom:1px solid var(--line); }
th { color:var(--muted); font-weight:600; }
.finding { border:1px solid var(--line); border-left:5px solid var(--grey); padding:10px 12px; margin:0 0 10px; break-inside:avoid; }
.finding.f-contradiction { border-left-color:var(--red); }
.finding.f-consistent { border-left-color:var(--green); }
.finding.f-not_applicable { opacity:.75; }
.finding .head { display:flex; justify-content:space-between; gap:8px; align-items:baseline; }
.finding .sub { font-size:9pt; color:var(--muted); margin:2px 0 6px; }
.finding h4 { font-size:9pt; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); margin:8px 0 2px; }
.finding ul { margin:0; padding-left:18px; }
.imgs { display:flex; flex-wrap:wrap; gap:6px; margin-top:6px; }
.imgs figure { margin:0; width:calc(50% - 3px); }
.imgs img { display:block; width:100%; max-height:70mm; object-fit:contain; background:#111; border:1px solid var(--line); }
.imgs figcaption { font-size:8pt; color:var(--muted); }
.score { font-size:9pt; color:var(--muted); }
.nowrap { white-space:nowrap; }
td.hash { font-size:8.5pt; color:var(--muted); padding-top:0; }
td.hash .mono { color:var(--ink); }
tbody.input { break-inside:avoid; }
.summary { margin:0 0 8px; font-size:10.5pt; }
.auth { margin:4px 0 8px; padding:6px 10px; border-left:4px solid var(--line); background:var(--soft); }
.auth b.s-contradiction { color:var(--red); border:0; }
.pairs { margin-top:6px; }
.pair { display:grid; grid-template-columns:1fr 1fr; gap:6px; margin-bottom:6px; break-inside:avoid; }
.pair figure { margin:0; }
.pair img { display:block; width:100%; height:46mm; object-fit:contain; background:#111; border:1px solid var(--line); }
.pair figure.archive img { border-color:var(--red); }
.pair figcaption { font-size:8pt; color:var(--muted); margin-top:2px; }
.verified { font-size:9pt; margin:4px 0 0; }
details.appendix summary { cursor:pointer; color:var(--muted); font-size:9pt; }
details.appendix pre { font-size:8pt; white-space:pre-wrap; word-break:break-word; background:var(--soft); padding:6px 8px; }
footer { margin-top:20px; padding-top:8px; border-top:1px solid var(--line); font-size:8.5pt; color:var(--muted); }
a { color:var(--accent); }
@page { size:A4; margin:14mm 12mm; }
@media print {
  main { padding:0; max-width:none; }
  a { color:inherit; text-decoration:none; }
  h2, h3, h4 { break-after:avoid; }
  .finding { break-inside:avoid; }
  .finding.long { break-inside:auto; }
  .finding .keep, .pair, figure, tr, .finding ul { break-inside:avoid; }
  .finding .keep { break-after:avoid; }
  .no-print { display:none; }
}
"""


# --- small formatters ----------------------------------------------------------

def _dt(d: datetime) -> str:
    return f"{d:%d %b %Y, %H:%M} (UTC{d:%z})"


def _chip(status: str) -> str:
    return f'<span class="chip s-{e(status)}">{e(STATUS_LABEL.get(status, status))}</span>'


def _value(v: Any, limit: int = 220) -> str:
    """Scalars as-is; lists/dicts as compact JSON, truncated."""
    if v is None:
        return "—"
    if isinstance(v, (str, int, float, bool)):
        text = str(v)
    else:
        text = json.dumps(v, ensure_ascii=False, separators=(", ", ": "))
    return e(text if len(text) <= limit else text[: limit - 1] + "…")


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


# --- sections ------------------------------------------------------------------

def _caption_html(inv: Investigation) -> str:
    """Caption with each claim span wrapped in <mark>. Overlapping spans: first one wins."""
    if not inv.caption:
        return '<p class="muted">No caption was provided.</p>'
    out, pos = [], 0
    for c in sorted(inv.claims, key=lambda c: (c.source.start, -c.source.end)):
        s, t = c.source.start, c.source.end
        if s < pos:
            continue
        out.append(e(inv.caption[pos:s]))
        out.append(f'<mark title="{e(c.id)} · {e(c.type)}">{e(inv.caption[s:t])}<sup>{e(c.id)}</sup></mark>')
        pos = t
    out.append(e(inv.caption[pos:]))
    return f'<div class="caption">{"".join(out)}</div>'


def _tz_name(d: datetime) -> str:
    off = d.utcoffset()
    if off is None:
        return ""
    if off == timedelta(hours=5, minutes=30):
        return "IST"
    if off == timedelta(0):
        return "UTC"
    sign = "+" if off >= timedelta(0) else "-"
    mins = abs(int(off.total_seconds())) // 60
    return f"UTC{sign}{mins // 60:02d}:{mins % 60:02d}"


def _day(d: datetime) -> str:
    return f"{d.day} {d:%b %Y}"


def _time_window(n: dict) -> str:
    """'18 Nov 2026, 17:00-21:00 IST · part of day' | '9-15 Nov 2026 · week' | 'Nov 2026 · month'."""
    try:
        start, end = datetime.fromisoformat(n["start"]), datetime.fromisoformat(n["end"])
    except (KeyError, TypeError, ValueError):
        return n.get("date") or "—"
    precision = str(n.get("precision") or "")
    tz = _tz_name(start)
    tz = f" {tz}" if tz else ""
    if start.time() == end.time() == datetime.min.time() and end > start:
        last = end - timedelta(days=1)                       # day-based windows end at the next midnight
        if precision == "year":
            text = f"{start:%Y}"
        elif precision == "month":
            text = f"{start:%b %Y}"
        elif last.date() == start.date():
            text = _day(start)
        elif (start.year, start.month) == (last.year, last.month):
            text = f"{start.day}–{last.day} {last:%b %Y}"
        else:
            text = f"{_day(start)} – {_day(last)}"
    elif start.date() == end.date():
        text = f"{_day(start)}, {start:%H:%M}–{end:%H:%M}{tz}"
    else:
        text = f"{_day(start)}, {start:%H:%M} – {_day(end)}, {end:%H:%M}{tz}"
    return text + (f" · {precision.replace('_', ' ')}" if precision else "")


def _normalized_text(c) -> str:
    """The claim's normalized value in words; the raw JSON is in the appendix."""
    n = c.normalized or {}
    if c.type == "location" and n.get("lat") is not None and n.get("lon") is not None:
        lat, lon = float(n["lat"]), float(n["lon"])
        text = f"{abs(lat):.4f}° {'N' if lat >= 0 else 'S'}, {abs(lon):.4f}° {'E' if lon >= 0 else 'W'}"
        return text + (f" ({n['country']})" if n.get("country") else "")
    if c.type == "time" and (n.get("start") or n.get("date")):
        return _time_window(n)
    if n.get("condition"):
        return str(n["condition"]).replace("_", " ")
    return "—" if not n else json.dumps(n, ensure_ascii=False, separators=(", ", ": "))


def _claims_appendix(inv: Investigation) -> str:
    if not inv.claims:
        return ""
    raw = json.dumps({c.id: c.normalized for c in inv.claims}, ensure_ascii=False, indent=2)
    return ('<h2>Appendix: raw claim data</h2>'
            '<details class="appendix" open><summary>Normalized values as stored (JSON)</summary>'
            f"<pre class='mono'>{e(raw)}</pre></details>")


def _claims_table(inv: Investigation) -> str:
    if not inv.claims:
        return '<p class="muted">No claims were extracted from the caption.</p>'
    rows = "".join(
        f"<tr><td class='mono'>{e(c.id)}</td><td>{e(c.type)}</td><td>{e(c.value)}</td>"
        f"<td>{e(_normalized_text(c))}</td><td class='mono'>[{c.source.start}:{c.source.end}]</td>"
        f"<td>{e(c.extracted_by)}</td></tr>"
        for c in inv.claims
    )
    return ("<table><tr><th>ID</th><th>Type</th><th>Value</th><th>Normalized</th>"
            f"<th>Span</th><th>Extracted by</th></tr>{rows}</table>")


def _image_refs(f: Finding) -> list[tuple[str, str]]:
    """(ref, caption) for image artifacts named in evidence or anywhere in details."""
    refs: list[tuple[str, str]] = []

    def add(ref: str, label: str) -> None:
        if ref.startswith("artifacts/") and any(ref.lower().endswith(x) for x in IMAGE_MIME) and ref not in dict(refs):
            refs.append((ref, label))

    for ev in f.evidence:
        if ev.kind == "artifact":
            add(ev.ref, ev.label)

    def walk(v: Any, key: str) -> None:
        if isinstance(v, str):
            add(v, key)
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(x, k)
        elif isinstance(v, list):
            for x in v:
                walk(x, key)

    walk({k: v for k, v in f.details.items() if k not in PAIR_KEYS}, "details")
    return refs[:MAX_IMAGES_PER_FINDING]


def _images_html(f: Finding, store: Optional[RunStore]) -> str:
    """Embed artifact images that exist on disk. Missing ones are silently skipped."""
    if store is None:
        return ""
    figs = []
    for ref, label in _image_refs(f):
        try:
            path = store.resolve(ref)
        except (FileNotFoundError, ValueError):
            continue
        if path.stat().st_size > MAX_IMAGE_BYTES:
            continue
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        mime = IMAGE_MIME[path.suffix.lower()]
        figs.append(f'<figure><img src="data:{mime};base64,{data}" alt="{e(label)}">'
                    f"<figcaption>{e(label)} · <code>{e(ref)}</code></figcaption></figure>")
    return f'<div class="imgs">{"".join(figs)}</div>' if figs else ""


def _data_uri(store: Optional[RunStore], ref: Optional[str]) -> Optional[str]:
    if store is None or not ref:
        return None
    try:
        path = store.resolve(ref)
    except (FileNotFoundError, ValueError):
        return None
    if path.suffix.lower() not in IMAGE_MIME or path.stat().st_size > MAX_IMAGE_BYTES:
        return None
    return f"data:{IMAGE_MIME[path.suffix.lower()]};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _pair_figure(uri: Optional[str], cls: str, caption: str) -> str:
    img = f'<img src="{uri}" alt="{e(caption)}">' if uri else '<div class="muted">frame not available</div>'
    return f'<figure class="{cls}">{img}<figcaption>{e(caption)}</figcaption></figure>'


def _pairs_html(f: Finding, store: Optional[RunStore]) -> str:
    """Time Machine: each matched upload frame next to its verified archive frame."""
    d = f.details or {}
    rows = []
    for p in d.get("frame_matches") or []:
        up, ar = _data_uri(store, p.get("upload_frame")), _data_uri(store, p.get("archive_frame"))
        if not up and not ar:
            continue
        rows.append('<div class="pair">'
                    + _pair_figure(up, "upload", f"This upload at {float(p.get('upload_s') or 0):.2f} s")
                    + _pair_figure(ar, "archive", f"Verified archive at {float(p.get('archive_s') or 0):.2f} s"
                                                  f" · Hamming {p.get('hamming')}")
                    + "</div>")
    if not rows:
        return ""
    who = " · ".join(str(x) for x in (d.get("verified_date"), d.get("verified_location"), d.get("verified_by")) if x)
    verified = f'<p class="verified">Verified record: {e(who)}</p>' if who else ""
    # the label, the verified record and the first pair stay together on one page
    first, rest = rows[0], "".join(rows[1:])
    return (f'<div class="keep"><h4>Matched frames (upload | verified archive)</h4>{verified}'
            f'<div class="pairs">{first}</div></div><div class="pairs">{rest}</div>')


def _evidence_html(f: Finding, inv: Investigation) -> str:
    items = []
    for ev in f.evidence:
        if ev.kind == "api":
            items.append(f'<li>{e(ev.label)}: <a class="break" href="{e(ev.ref)}">{e(ev.ref)}</a></li>')
        elif ev.kind == "artifact":
            href = f"/api/files/{inv.id}/{ev.ref}"
            items.append(f'<li>{e(ev.label)}: <a href="{e(href)}"><code>{e(ev.ref)}</code></a></li>')
        else:
            items.append(f"<li>{e(ev.label)}: <code>{e(ev.ref)}</code> <span class='muted'>({e(ev.kind)})</span></li>")
    return f"<h4>Evidence</h4><ul>{''.join(items)}</ul>" if items else ""


def _locations_html(f: Finding, inv: Investigation) -> str:
    names = {i.file_id: i.name for i in inv.inputs}
    items = []
    for loc in f.locations:
        name = e(names.get(loc.file_id, loc.file_id))
        if loc.kind == "timestamp":
            items.append(f"<li>{name}: {loc.start_s:.1f}–{loc.end_s:.1f} s</li>")
        else:
            frame = f"frame at {loc.frame_s:.1f} s, " if loc.frame_s is not None else ""
            x, y, w, h = loc.bbox
            items.append(f"<li>{name}: {frame}region x={x:.2f} y={y:.2f} w={w:.2f} h={h:.2f} (fraction of frame)</li>")
    return f"<h4>Flagged locations</h4><ul>{''.join(items)}</ul>" if items else ""


def _details_html(f: Finding) -> str:
    if not f.details:
        return ""
    rows = "".join(f"<tr><th>{e(str(k))}</th><td class='mono'>{_value(v)}</td></tr>"
                   for k, v in f.details.items() if k not in PAIR_KEYS)
    return f"<h4>Key details</h4><table>{rows}</table>"


def _list(title: str, items: Iterable[str]) -> str:
    lis = "".join(f"<li>{e(x)}</li>" for x in items)
    return f"<h4>{e(title)}</h4><ul>{lis}</ul>" if lis else ""


def _finding_html(f: Finding, inv: Investigation, store: Optional[RunStore], weak: set[str]) -> str:
    weak_tag = ' <span class="chip">Weak indicator</span>' if f.id in weak else ""
    claims = ", ".join(f.claim_ids) or "whole file"
    # A finding with matched frame pairs is taller than a page: let it break, but only between its parts.
    long = " long" if (f.details or {}).get("frame_matches") else ""
    return f"""
<section class="finding f-{e(f.status)}{long}">
  <div class="keep">
  <div class="head"><h3>{e(f.title)}</h3><div>{_chip(f.status)}{weak_tag}</div></div>
  <div class="sub">{e(config.WITNESS_LABELS.get(f.witness, f.witness))} witness ·
    {e(FAMILY_LABEL[f.family])} family · reliability <b>{e(f.reliability)}</b> ·
    claims: {e(claims)} · <code>{e(f.id)}</code> · {f.elapsed_ms} ms</div>
  <p>{e(f.summary)}</p>
  </div>
  {_pairs_html(f, store)}
  {_images_html(f, store)}
  {_details_html(f)}
  {_evidence_html(f, inv)}
  {_locations_html(f, inv)}
  {_list("Alternative explanations", f.alternatives)}
  {_list("Limitations", f.limitations)}
</section>"""


def _custody_html(inv: Investigation) -> str:
    inputs = "".join(
        f"<tbody class='input'><tr><td class='mono'>{e(i.file_id)}</td><td>{e(i.name)}</td><td>{e(i.mime)}</td>"
        f"<td class='nowrap'>{_size(i.size)}</td>"
        f"<td class='nowrap'>{'—' if i.duration_s is None else f'{i.duration_s:.1f} s'}</td>"
        f"<td class='mono break'>{e(i.stored_path)}</td></tr>"
        f"<tr><td></td><td colspan='5' class='hash'>SHA-256 <span class='mono'>{e(i.sha256)}</span></td></tr></tbody>"
        for i in inv.inputs
    ) or "<tr><td colspan='6' class='muted'>No files were submitted.</td></tr>"
    tz = f" ({_tz_name(inv.history[0].ts) or 'local time'})" if inv.history else ""
    history = "".join(
        f"<tr><td class='mono nowrap'>{h.ts:%Y-%m-%d %H:%M:%S}.{h.ts.microsecond // 1000:03d}</td>"
        f"<td class='nowrap'>{e(h.event)}</td><td class='mono break'>{e(h.detail)}</td></tr>"
        for h in inv.history
    ) or "<tr><td colspan='3' class='muted'>No history recorded.</td></tr>"
    return f"""
<h3>Inputs (originals stored read-only)</h3>
<table><tr><th>ID</th><th>Name</th><th>Type</th><th>Size</th><th>Duration</th><th>Stored at</th></tr>{inputs}</table>
<h3 style="margin-top:10px">History log</h3>
<table><tr><th>Time{e(tz)}</th><th>Event</th><th>Detail</th></tr>{history}</table>"""


# --- page ------------------------------------------------------------------------

def render_report(inv: Investigation, store: Optional[RunStore] = None) -> str:
    """Full HTML page. `store` (the run folder) is only used to embed artifact images."""
    a = inv.assessment
    weak = set(a.weak_indicators)
    sm = summarize(inv.findings, inv.claims)                    # the same text the Live Desk shows
    sentence, auth = sm["sentence"], sm["ai_check"]
    by_id = {f.id: f for f in inv.findings}
    ordered = sorted(inv.findings, key=lambda f: (STATUS_ORDER[f.status], RELIABILITY_ORDER[f.reliability]))

    counts = a.counts.model_dump()
    pills = "".join(
        f'<span class="s-{s}">{counts[s]} {e(COUNT_WORDS[s][0 if counts[s] == 1 else 1])}</span>'
        for s in STATUS_ORDER if counts.get(s)
    ) or '<span>No findings</span>'
    families = "".join(
        f"<tr><th>{e(FAMILY_LABEL[fam])}</th><td>{_chip(getattr(a.families, fam))}</td></tr>"
        for fam in ("context", "signal", "pixels")
    )
    weak_html = "".join(
        f"<li><code>{e(fid)}</code> {e(by_id[fid].title)} ({e(STATUS_LABEL[by_id[fid].status].lower())}, "
        f"reliability {e(by_id[fid].reliability)})</li>"
        for fid in a.weak_indicators if fid in by_id
    )
    score = (
        f'<p class="score">Experimental score: <b class="mono">{a.experimental_score.value}</b> / 100. '
        f"{e(a.experimental_score.label)}. It is not used for the headline above.</p>"
        if a.experimental_score else ""
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ALIBI report {e(inv.id)}</title>
<style>{CSS}</style></head>
<body><main>

<header class="top">
  <div><h1>ALIBI</h1><div class="muted">Explainable multimedia verification · Investigation report</div></div>
  <div class="meta"><div class="mono">{e(inv.id)}</div>
    <div>Created {e(_dt(inv.created_at))}</div><div>Shared {e(_dt(inv.posted_at))}</div></div>
</header>
<div class="banner">Human review required. This is an evidence report, not a verdict.</div>
<p class="no-print muted">Use your browser's Print → Save as PDF to keep a copy.</p>

<h2>Assessment</h2>
<p class="auth"><span class="muted">{e(auth["question"])}</span> <b class="{'s-contradiction' if auth['kind'] != 'none' else ''}">{e(auth["answer"])}</b><br>
<span class="muted" style="font-size:8.5pt">{e(auth["note"])}</span></p>
<div class="headline">{e(auth["answer"]) if sm["caption_less"] else e(a.headline_text)}</div>
{'<p class="muted">No caption was given, so place, time and weather checks were skipped. Rule-engine headline: '
 + e(a.headline_text) + '</p>' if sm["caption_less"] else ''}
{f'<p class="summary">{e(sentence)}</p>' if sentence else ""}
<div class="pills">{pills}</div>
<table style="width:auto;margin-top:6px">{families}
  <tr><th>Human review</th><td>{e(a.human_review)}</td></tr></table>
{score}

<h2>Caption and claims</h2>
{_caption_html(inv)}
<div style="margin-top:8px">{_claims_table(inv)}</div>

<h2>Findings ({len(inv.findings)})</h2>
{"".join(_finding_html(f, inv, store, weak) for f in ordered) or '<p class="muted">No findings.</p>'}

<h2>Weak indicators</h2>
<p class="muted">Low-reliability signals. They are listed for completeness and never set a family status or the headline.</p>
{f"<ul>{weak_html}</ul>" if weak_html else '<p class="muted">None.</p>'}

<h2>Chain of custody</h2>
{_custody_html(inv)}

{_claims_appendix(inv)}

<footer>
  ALIBI v{e(config.APP_VERSION)} · report generated {e(_dt(now()))}.
  Results are evidence indicators produced by deterministic rules over the witnesses listed above.
  They can be incomplete or mistaken and require review by a qualified person before any decision is made.
</footer>
</main></body></html>"""
