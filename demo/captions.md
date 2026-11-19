# Demo captions

Read by `scripts/warm_cache.py` (and by humans during rehearsal).
Each case is a `## <name>` heading followed by `key: value` lines.
`caption: TODO` means "not filled in yet" and the case is skipped.

- `posted_at`: when the media was shared (`YYYY-MM-DD` or ISO datetime). Relative words like "yesterday" resolve against it.
- `files`: paths relative to the repo root, comma-separated (informational for now).
- `expect`: what the investigation should show. Controls must **never** show a contradiction.

## case_a
caption: Massive flooding in Chennai yesterday evening, roads completely underwater
posted_at: 2026-11-19
files: demo/case_a/clip.mp4
expect: prior_appearance contradiction (high); weather contradiction (medium); metadata inconclusive; lipsync not applicable
note: Confirm with the weather witness that Chennai was dry on the claimed evening before relying on this date.

## case_b
caption: TODO
posted_at: TODO
files: demo/case_b/
expect: lipsync contradiction (medium) with flagged timestamps; metadata editing/encoder hints

## control_1
caption: TODO
posted_at: TODO
files: demo/controls/
expect: consistent_so_far or insufficient_evidence, never a contradiction

## control_2
caption: TODO
posted_at: TODO
files: demo/controls/
expect: consistent_so_far or insufficient_evidence, never a contradiction

## control_3
caption: TODO
posted_at: TODO
files: demo/controls/
expect: consistent_so_far or insufficient_evidence, never a contradiction
