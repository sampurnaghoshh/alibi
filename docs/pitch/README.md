# Pitch assets: lip-sync calibration and ENF go/no-go

Evidence for the "we tell you when we can't decide" honesty slide. Generated 2026-11-19 from the team's own
recordings (staged by the team).

| File | What it shows |
|---|---|
| `lipsync_calibration.png` | Slide chart (16:9, dark): 1080p close-ups, velocity correlation vs peak prominence, genuine vs swapped, calibrate vs held-out test, with the frozen thresholds. |
| `lipsync_calibration.json` | Every clip of both runs (far-away 4K, close-up 1080p): group, set, faceH, lag / corr / prominence, frozen thresholds and the held-out test outcome. |
| `enf_img5384_100hz.png` | Hour-1 ENF spectrogram of IMG_5384, the clip closest to a mains-hum detection. |
| `enf_img5388_spurious_60hz.png` | Hour-1 ENF spectrogram of IMG_5388, with a spurious 60 Hz trace. |
| `enf_go_no_go.json` | Per-clip SNR table (50 / 60 / 100 / 120 Hz) for the 5 hour-1 clips and the NO-GO result. |

## Lip-sync

Genuine and swapped clips overlap, so lip-sync never accuses. 10 genuine + 10 swapped close-ups; thresholds frozen
on the 5 calibrate clips, then applied once to the held-out test: 1 genuine clip would have been wrongly flagged.
Lip-sync stays inconclusive (reliability capped at low) and is shown for human review only.

## ENF (power-grid hum)

Mains hum was detected in 0 of 5 iPhone clips (3 needed), so the result is NO-GO and ENF is display-only: always
insufficient signal, never a verdict.

IMG_5384’s 100 Hz cleared 6 dB in 43% of frames (60% needed); IMG_5388 shows a spurious 60 Hz trace, correctly ignored, not a 60 Hz detection.
