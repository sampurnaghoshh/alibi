# Demo captions

Read by `scripts/warm_cache.py` (and by humans during rehearsal).
Each case is a `## <name>` heading followed by `key: value` lines.
`caption: TODO` means "not filled in yet" and the case is skipped.

- `posted_at`: when the media was shared (`YYYY-MM-DD` or ISO datetime). Relative words like "yesterday" resolve against it.
- `files`: paths relative to the repo root, comma-separated (informational for now).
- `expect`: what the investigation should show. Controls must **never** show a contradiction.

## case_a
caption: Massive flooding in Chennai yesterday evening
posted_at: 2026-11-19
files: demo/case_a/casea_whatsapp_2.mp4
expect: prior_appearance contradiction (high); weather contradiction (medium); metadata inconclusive; lipsync not applicable
archive: casea_original (HDR original) + casea_circulating (first WhatsApp copy, SDR), both verified Jalahalli, Bengaluru, 5 Oct 2026 17:19 IST (Adithi's own recording). The upload is a second, independent WhatsApp copy.
note: Confirm with the weather witness that Chennai was dry on the claimed evening before relying on this date.

## case_b
caption: TODO
posted_at: TODO
files: demo/case_b/
expect: lipsync contradiction (medium) with flagged timestamps; metadata editing/encoder hints

## control_1
caption: Sampurna explaining our project at the hackathon in Bengaluru today
posted_at: 2026-11-19
files: demo/controls/control_1.MOV
expect: consistent_so_far or insufficient_evidence, never a contradiction
note: genuine clip, true caption. The .MOV files are > 20 MB, so they are not in git (§12); copy them in from the team drive.

## control_2
caption: Teammate talking about ALIBI at the hackathon in Bengaluru today
posted_at: 2026-11-19
files: demo/controls/control_2.MOV
expect: consistent_so_far or insufficient_evidence, never a contradiction

## control_3
caption: Inside the hackathon venue in Bengaluru today
posted_at: 2026-11-19
files: demo/controls/control_3.MOV
expect: consistent_so_far or insufficient_evidence, never a contradiction

## control_4
caption: Outside the venue in Bengaluru this afternoon, clear weather
posted_at: 2026-11-19
files: demo/controls/control_4.MOV
expect: consistent_so_far or insufficient_evidence, never a contradiction
