"""
Everything the bot says. Owner: Sampurna.

Keep to the §9 vocabulary: "does not support", "contradicts the stated…", "no contradictions
found", "evidence indicator", "requires human review". Never the §2.7 forbidden words
(tests/test_bot.py scans every string here and every formatted reply).
"""

from __future__ import annotations

from typing import Any

HUMAN_REVIEW = "Human review required. This is an evidence report, not a verdict."

HELP = (
    "🕵️ ALIBI checks whether a video's story holds up: the place, time and conditions its caption claims.\n\n"
    "How to send a case:\n"
    "1. Record 5–8 s in 1080p.\n"
    "2. Tap 📎 → File (not Gallery/Video) and pick the clip. A normal video upload makes Telegram "
    "remove the capture date and GPS.\n"
    "3. Put the claim in the caption, e.g. \"Heavy rain in Bengaluru this afternoon\".\n\n"
    "📍 Tip: iPhone: in the share sheet tap Options → turn on Location before sending, so the file keeps its "
    "GPS location and the place can be checked.\n\n"
    "You get the claims at once, live progress, then the result and a printable report.\n"
    "To re-check the same file with a different story, reply to the result message with the new caption.\n\n"
    + HUMAN_REVIEW
)

RECEIVED = "📥 Received. Extracting claims…"
GPS_TIP = ("📍 No GPS location found in this file, so the place can't be checked against it.\n"
           "iPhone: in the share sheet tap Options → turn on Location before sending.")
COMPRESSED_VIDEO = ("⚠️ Sent as video, so Telegram removed the capture date/GPS. Metadata will be inconclusive. "
                    "Send as a File for the full check.")
COMPRESSED_PHOTO = ("⚠️ Sent as photo, so Telegram removed the capture date/GPS. Metadata will be inconclusive. "
                    "Send as a File for the full check.")
TOO_BIG = ("📦 This file is {size} MB, and Telegram only lets bots download files up to 20 MB. "
           "Please send a shorter clip (5–8 s in 1080p is plenty), as a File.")
NO_CLAIMS = ("🤔 I couldn't find a checkable claim in that caption. Mention a place, a time and what happened, "
             "for example:\n\"Massive flooding in Chennai yesterday evening\"\n\nSend the file again with a caption "
             "like that, or reply to a result message with a new caption.")
NEED_MEDIA = ("📎 Send a video or photo as a File with the claim in the caption, or reply to one of my result "
              "messages with a new caption to re-check that file. /help for details.")
NOT_A_RESULT = "↩️ To re-check a file with a new story, reply to my result message for that file (the one with the headline)."
DOWNLOAD_FAILED = "⚠️ I couldn't download that file from Telegram. Please send it again."
FAILED = "⚠️ The investigation stopped with an error: {error}\nPlease try again, or show the file to a reviewer directly."
STREAM_LOST = "⚠️ Lost the live progress for this case. Its result will be at {url}"
API_ERROR = "⚠️ {error}"
REPORT_CAPTION = "🧾 Printable report: open it in a browser, then Print → Save as PDF."
RERUN_PREFIX = "🔁 Same file, new story: "
RERUN_HINT = "Reply to this message with a corrected caption to re-check the same file."

CLAIM_LABELS = {"location": "Location", "time": "Time", "weather_condition": "Event", "event": "Event",
                "speaker": "Speaker", "other": "Claim"}
WITNESS_LABELS = [("metadata", "Metadata"), ("weather", "Weather"), ("prior_appearance", "Prior appearance"),
                  ("lipsync", "Lip-sync"), ("enf", "Power-grid hum"), ("ela", "ELA")]
COUNT_LABELS = [("contradiction", "contradiction", "contradictions"), ("consistent", "consistent", "consistent"),
                ("inconclusive", "inconclusive", "inconclusive"),
                ("insufficient_signal", "insufficient signal", "insufficient signal"),
                ("not_applicable", "not applicable", "not applicable")]


def claims_line(claims: list[Any]) -> str:
    """'🔎 Claims found: Location: Delhi · Event: flooding · Time: yesterday'"""
    parts = [f"{CLAIM_LABELS.get(c.type, 'Claim')}: {c.source.text}" for c in claims]
    return "🔎 Claims found: " + " · ".join(parts)


ICON_CONTRADICTION, ICON_CONSISTENT, ICON_INCONCLUSIVE, ICON_NOT_APPLICABLE, ICON_PENDING = "⚠️", "✅", "❔", "➖", "…"
LEGEND = "⚠️ contradiction · ✅ consistent · ❔ inconclusive · ➖ not applicable"


def progress(state: dict[str, str], done: bool = False) -> str:
    """state: witness -> icon (see mark()); witnesses not finished yet show '…'."""
    head = "🔍 Analysis complete: " if done else "🔍 Checking: "
    return head + witness_line(state)


def witness_line(state: dict[str, str]) -> str:
    return " · ".join(f"{label} {state.get(name, ICON_PENDING)}" for name, label in WITNESS_LABELS)


def mark(findings: list[dict]) -> str:
    """One icon per witness, by STATUS. With several findings the most significant wins:
    contradiction > consistent > inconclusive / insufficient signal > not applicable."""
    statuses = {f.get("status") for f in findings}
    if "contradiction" in statuses:
        return ICON_CONTRADICTION
    if "consistent" in statuses:
        return ICON_CONSISTENT
    if statuses & {"inconclusive", "insufficient_signal"}:
        return ICON_INCONCLUSIVE
    if statuses == {"not_applicable"}:
        return ICON_NOT_APPLICABLE
    return ICON_INCONCLUSIVE        # empty / unknown: nothing to report either way


def marks_from_findings(findings: list[dict]) -> dict[str, str]:
    by_witness: dict[str, list[dict]] = {}
    for f in findings:
        by_witness.setdefault(f["witness"], []).append(f)
    return {w: mark(fs) for w, fs in by_witness.items()}


def counts_line(counts: dict[str, int]) -> str:
    parts = [f"{counts[k]} {one if counts[k] == 1 else many}" for k, one, many in COUNT_LABELS if counts.get(k)]
    return " · ".join(parts) or "no findings"


def _one_line(text: str, limit: int = 220) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def final(inv: dict, rerun: bool = False) -> str:
    a = inv["assessment"]
    lines = [("🧾 " if not rerun else RERUN_PREFIX) + a["headline_text"], counts_line(a["counts"]),
             witness_line(marks_from_findings(inv["findings"])), LEGEND]
    contradictions = [f for f in inv["findings"] if f["status"] == "contradiction"]
    if contradictions:
        lines.append("")
        for f in contradictions:
            weak = " (weak indicator)" if f["reliability"] == "low" else ""
            lines.append(f"• {f['title']}{weak}: {_one_line(f['summary'])}")
    lines += ["", HUMAN_REVIEW, RERUN_HINT]
    return "\n".join(lines)
