"""
ELA heatmap (CLAUDE.md §6.7). Owner: Adithi, only if time.   Family: pixels. Reliability: ALWAYS low.

TODO (Adithi):
- Image, or a video keyframe: re-save as JPEG q=config.ELA_JPEG_QUALITY, abs difference x config.ELA_AMPLIFICATION.
- Save heatmap PNG via ctx.artifact_path(...).
- Always status inconclusive, reliability low. It is a visual aid, never a verdict.

Run alone (from backend/):
    python -m app.witnesses.ela --file ../demo/controls/photo.jpg
"""

from __future__ import annotations

from app.schemas import Finding
from app.witnesses.base import InvestigationContext, Witness, run_cli


class ElaWitness(Witness):
    name = "ela"
    label = "ELA"
    implemented = False  # set True when done

    def applicable(self, ctx: InvestigationContext) -> bool:
        return bool(ctx.inputs_of("image") or ctx.inputs_of("video"))

    def run(self, ctx: InvestigationContext) -> list[Finding]:
        return self.placeholder()


if __name__ == "__main__":
    run_cli(ElaWitness())
