"""
ENF power-grid hum witness (CLAUDE.md §6.6). Owner: Nooman.   Family: signal. Max reliability: medium.
Conditional on the hour-1 go/no-go test (config.ENF_GO).

TODO (Nooman):
- Load audio at native sample rate; STFT with a 4-8 s window (config.ENF_STFT_WINDOW_S).
- For 50/100/60/120 Hz: SNR of +/- 0.5 Hz peak vs a 5 Hz band. Detected if SNR >= 6 dB in >= 60% of frames.
- 50/100 Hz + India location claim -> consistent (medium).
  60/120 Hz + claim in a 50 Hz country -> contradiction (medium), ONLY if config.ENF_GO is True.
  Nothing -> insufficient_signal. Never more than that.
- Artifact: spectrogram PNG 40-130 Hz with the hum line marked.
- Alternatives: phone noise suppression, outdoor/battery-only environments, imported equipment.

Run alone (from backend/):
    python -m app.witnesses.enf --file ../demo/case_b/talking.mp4 --caption "Speech in Bengaluru today"
"""

from __future__ import annotations

from app.schemas import Finding
from app.witnesses.base import InvestigationContext, Witness, run_cli


class EnfWitness(Witness):
    name = "enf"
    label = "Power-grid hum"
    implemented = False  # set True when done

    def applicable(self, ctx: InvestigationContext) -> bool:
        return bool(ctx.inputs_of("video") or ctx.inputs_of("audio"))

    def run(self, ctx: InvestigationContext) -> list[Finding]:
        return self.placeholder()


if __name__ == "__main__":
    run_cli(EnfWitness())
