"""
Lip-sync synchrony indicator (CLAUDE.md §6.5). Owner: Nooman.   Family: signal. Max reliability: medium.
Runner time limit: 90 s.

TODO (Nooman):
- Applicable: video with an audio track and a face in >= config.LIPSYNC_MIN_FACE_FRACTION of sampled frames.
  If the face check is expensive, do it in run() and return a not_applicable finding from there.
- MediaPipe mouth aperture (inner-lip distance / face height), median-smoothed.
- ffmpeg audio -> librosa RMS envelope at video fps; adaptive speech mask.
- 3 s windows, 1 s step, cross-correlation over +/- 0.5 s -> best lag + peak corr per speech window.
- Thresholds in config (LIPSYNC_*). Worst windows -> locations (timestamps).
- Artifact: ctx.artifact_path("lipsync_series.json") with { t[], mouth[], audio[], window_lags[], window_corrs[] }.
- Always a "synchrony indicator". Alternatives: dubbing/translation, Bluetooth delay, low frame rate, off-screen speaker.

Run alone (from backend/):
    python -m app.witnesses.lipsync --file ../demo/case_b/talking.mp4
"""

from __future__ import annotations

from app.schemas import Finding
from app.witnesses.base import InvestigationContext, Witness, run_cli


class LipsyncWitness(Witness):
    name = "lipsync"
    label = "Lip-sync"
    implemented = False  # set True when done

    def applicable(self, ctx: InvestigationContext) -> bool:
        return bool(ctx.inputs_of("video"))

    def run(self, ctx: InvestigationContext) -> list[Finding]:
        return self.placeholder()


if __name__ == "__main__":
    run_cli(LipsyncWitness())
