"""
Prior appearance witness (CLAUDE.md §6.4). Owner: Adithi.   Family: context. Max reliability: high.

TODO (Adithi):
- Load config.ARCHIVE_JSON (built by scripts/build_archive.py).
- Sample the upload at config.PRIOR_SAMPLE_FPS (images = 1 frame), imagehash.phash each frame.
- Frame match: Hamming <= config.PRIOR_MAX_HAMMING. Clip match: >= PRIOR_MIN_MATCHED_FRAMES frames
  or >= PRIOR_MIN_MATCHED_FRACTION of sampled frames match the same archive entry.
- Match + verified date/location disagrees with claims -> contradiction (high),
  title "Frames match a clip verified as recorded in {place} on {date}".
- Match + agrees -> consistent (high).
- No match -> inconclusive: "Not found in our archive of N verified clips. This does not mean the footage is new."
- locations: matched timestamps in the upload.
- Limitation: "Archive is small (N clips). In production this connects to reverse-image search and fact-checker databases."

Run alone (from backend/):
    python -m app.witnesses.prior_appearance --file ../demo/case_a/clip.mp4 --claims-json ../shared/fixtures/sample_investigation.json
"""

from __future__ import annotations

from app.schemas import Finding
from app.witnesses.base import InvestigationContext, Witness, run_cli


class PriorAppearanceWitness(Witness):
    name = "prior_appearance"
    label = "Prior appearance"
    implemented = False  # set True when done

    def applicable(self, ctx: InvestigationContext) -> bool:
        return bool(ctx.inputs_of("video") or ctx.inputs_of("image"))

    def run(self, ctx: InvestigationContext) -> list[Finding]:
        return self.placeholder()


if __name__ == "__main__":
    run_cli(PriorAppearanceWitness())
