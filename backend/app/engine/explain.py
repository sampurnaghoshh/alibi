"""
Explanations (CLAUDE.md §7.6). Owner: Sampurna.

1. Templates: any finding with an empty title/summary gets a fixed template text.
   Witness.finding() also uses these when a witness omits title/summary.
2. Optional LLM rewrite (hook, not wired yet): the LLM may only REWORD title/summary,
   given only the finding JSON. Guard: the rewrite is rejected unless it keeps exactly
   the same numbers and contains no forbidden words. Status, reliability, details and
   every other field are never touched. With ALIBI_LLM_PROVIDER=none, templates only.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Optional

from app import config
from app.schemas import Finding

log = logging.getLogger("alibi.explain")

_FORBIDDEN = re.compile(config.FORBIDDEN_WORDS_RE, re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

_TITLE = {
    "contradiction": "{label} evidence contradicts the stated context",
    "consistent": "{label} evidence is consistent with the stated context",
    "inconclusive": "{label} evidence is inconclusive",
    "insufficient_signal": "{label}: insufficient signal to assess",
    "not_applicable": "{label}: not applicable to these inputs",
}
_SUMMARY = {
    "contradiction": "The {label_lc} witness found evidence that does not support the stated context ({reliability} reliability). See details for the measured values.",
    "consistent": "The {label_lc} witness found no contradiction with the stated context ({reliability} reliability). See details for the measured values.",
    "inconclusive": "The {label_lc} witness could not decide either way. See limitations and alternatives.",
    "insufficient_signal": "The {label_lc} witness did not find enough signal to analyse. This is not evidence of manipulation.",
    "not_applicable": "The inputs or claims do not contain what the {label_lc} witness needs.",
}


def _fmt(template: str, witness: str, reliability: str = "") -> str:
    label = config.WITNESS_LABELS.get(witness, witness)
    return template.format(label=label, label_lc=label[:1].lower() + label[1:] if label != "ELA" else label,
                           reliability=reliability)


def template_title(witness: str, status: str) -> str:
    return _fmt(_TITLE[status], witness)


def template_summary(witness: str, status: str, reliability: str) -> str:
    return _fmt(_SUMMARY[status], witness, reliability)


def fill_missing(f: Finding) -> Finding:
    """Return a copy with blank title/summary replaced by templates."""
    f = f.model_copy()
    if not f.title.strip():
        f.title = template_title(f.witness, f.status)
    if not f.summary.strip():
        f.summary = template_summary(f.witness, f.status, f.reliability)
    return f


def is_safe_rewrite(f: Finding, title: str, summary: str) -> bool:
    """Accept an LLM rewrite only if it keeps the same numbers and avoids forbidden words."""
    if not title.strip() or not summary.strip():
        return False
    if _FORBIDDEN.search(title) or _FORBIDDEN.search(summary):
        return False
    before = Counter(_NUMBER.findall(f.title + " " + f.summary))
    after = Counter(_NUMBER.findall(title + " " + summary))
    return before == after


_REWRITE_SYSTEM = (
    "You reword one finding from a media-verification report for a newsroom reader. "
    "You receive the finding as JSON. Rewrite only its title and summary in plain English. "
    "Keep every number exactly as written and do not add new numbers. Do not change the meaning, "
    "the status or the reliability, and do not add conclusions that are not in the JSON. "
    "Never use the words fake, real, authentic, proven, definitely or AI-generated. "
    'Return ONLY JSON: {"title": "...", "summary": "..."}. Title under 90 characters, summary under 300.'
)


def llm_rewrite(f: Finding) -> Optional[tuple[str, str]]:
    """Ask the LLM to reword title/summary, given ONLY the finding JSON.
    Returns (title, summary) or None. The caller still checks is_safe_rewrite()."""
    from app.claims import llm  # noqa: PLC0415 - avoid import cycles at module load

    data = llm.complete_json(_REWRITE_SYSTEM, f.model_dump_json(), max_tokens=2000)
    if isinstance(data, dict) and isinstance(data.get("title"), str) and isinstance(data.get("summary"), str):
        return data["title"].strip(), data["summary"].strip()
    return None


def llm_enabled() -> bool:
    return config.LLM_EXPLAIN and config.LLM_PROVIDER != "none" and config.llm_configured()


def _guarded_rewrite(f: Finding) -> Finding:
    """Apply an LLM rewording only if it passes the guard. Status/reliability/details never change."""
    try:
        rewrite = llm_rewrite(f)
    except Exception as exc:  # noqa: BLE001 - explanations are optional
        log.warning("LLM rewrite failed for %s: %s", f.id, exc)
        return f
    if rewrite and is_safe_rewrite(f, *rewrite):
        f.title, f.summary = rewrite
    elif rewrite:
        log.info("LLM rewrite rejected for %s (numbers changed or forbidden word)", f.id)
    return f


def explain_findings(findings: list[Finding]) -> list[Finding]:
    """Templates for blanks, then (optionally) a guarded LLM rewording. Never raises."""
    out = [fill_missing(f) for f in findings]
    if llm_enabled() and out:
        # One call per finding, in parallel, so the whole step stays within one LLM timeout.
        from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

        with ThreadPoolExecutor(max_workers=min(8, len(out))) as pool:
            out = list(pool.map(_guarded_rewrite, out))
    return out
