---
title: ALIBI
colorFrom: gray
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Explainable multimedia verification
---

# ALIBI: explainable multimedia verification

**Every piece of media has an alibi. We check whether it holds up.**

ALIBI treats each kind of evidence (caption claims, file metadata, recorded weather, earlier appearances
of the same footage, lip-sync, power-grid hum, error-level analysis) as a witness, and a rule-based
engine cross-examines their statements against the story the caption tells. The result is an evidence
report, not a verdict: what is consistent, what contradicts, what is inconclusive, why, and what else
could explain it. Human review is always required.

- **`/`**: upload media with its caption, or run demo Case A / Case B.
- **`/investigation/?id=...`**: the evidence report, graph and witness cards.
- **`/live`**: the Live Desk big screen (keys: 1 = Case A, 2 = Case B, ? = help).
- **`/api/...`**: the API; `/api/investigations/{id}/report` is the printable report.

All demo media were recorded by the team. Built for SapthaHacks / InnovEdge Hackathon 2026.
Source code: https://github.com/sampurnaghoshh/alibi (MIT). Weather data: Open-Meteo (CC BY 4.0).

This Space runs on the free CPU tier, so an upload takes about 20-60 s and runs disappear when the
Space restarts. The optional Telegram bot starts only when the `TELEGRAM_BOT_TOKEN` secret is set.
