"""
Metadata integrity witness (CLAUDE.md §6.2). Owner: Adithi.   Family: context. Max reliability: medium.

TODO (Adithi):
- exiftool -json -G for images/video; ffprobe -v quiet -print_format json -show_format -show_streams.
- Capture date present and > config.METADATA_MAX_DATE_DIFF_DAYS from the claimed date -> contradiction (medium).
- GPS present and > config.METADATA_MAX_GPS_DIST_KM from the claimed location -> contradiction (medium).
- Present and matching -> consistent (low). Missing -> inconclusive.
- Editing-software tag -> separate finding (n=2), inconclusive (low), "Editing software tag present".
- Always list: "Metadata can be edited or removed; it is supporting evidence, not ground truth."
- Save raw tool output with ctx.artifact_path("metadata_raw.json").

Run alone (from backend/):
    python -m app.witnesses.metadata --file ../demo/case_a/clip.mp4 --caption "Flooding in Chennai yesterday"
"""

from __future__ import annotations

from app.schemas import Finding
from app.witnesses.base import InvestigationContext, Witness, run_cli


class MetadataWitness(Witness):
    name = "metadata"
    label = "Metadata"
    implemented = False  # set True when done

    def applicable(self, ctx: InvestigationContext) -> bool:
        return bool(ctx.inputs)

    def run(self, ctx: InvestigationContext) -> list[Finding]:
        return self.placeholder()


if __name__ == "__main__":
    run_cli(MetadataWitness())
