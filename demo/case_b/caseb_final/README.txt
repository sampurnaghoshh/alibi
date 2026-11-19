ALIBI - Case B (manipulated audio/video): "voice replaced" clip
=================================================================
Made 2026-11-19 by Nooman for Sam. All media staged by the team (same person, own recordings, with consent).

FILES
-----
caseb_swapped.mp4                  The Case B clip: clip 11's video with another recording's audio.
caseb_swapped_finding.json         Lip-sync witness finding for caseb_swapped.mp4.
caseb_swapped_lipsync_chart.png    Chart: frame + landmarks, mouth vs loudness, correlation vs lag, per-window corr.
caseb_swapped_artifacts/           The witness's own artifact: lipsync_series.json (data for the frontend chart).
genuine_IMG_5414_finding.json      Same witness on the genuine clip 11 (for comparison).
genuine_IMG_5414_lipsync_chart.png
genuine_IMG_5414_artifacts/
lipsync_calibration.png            Pitch: lip-sync calibration (genuine vs swapped overlap) - from docs/pitch/
enf_img5384_100hz.png              Pitch: ENF hour-1 spectrogram, clip closest to detection - from docs/pitch/
enf_img5388_spurious_60hz.png      Pitch: ENF hour-1 spectrogram, spurious 60 Hz trace - from docs/pitch/

SOURCES
-------
Video source:  alibi-media\caseb_1080p\IMG_5414.MOV   (clip 11; 1080p copy of caseb\IMG_5414.MOV)
               duration 8.166667 s, H.264 1080x1920 portrait (upright, no rotation tag), ~29.8 fps
               sha256 08f2b5569835d6c706b4b8b93ba7d6c9476b3dfbb34c49785e07833324277a32
Audio source:  alibi-media\closeup_1080p\IMG_5400.MOV (genuine close-up, calibration set, clear speech)
               duration 21.466667 s; the first 8.166667 s were used (trimmed to the video's exact length)
               sha256 e2d205a09fdaf847f022a2272d4c0674403cc4fa4564a73724d2862823a33636

Output:        caseb_swapped.mp4, duration 8.166667 s (video and audio both exactly 8.166667 s)
               video: H.264 High 1080x1920 portrait, stream-copied from IMG_5414 (no re-encode, orientation kept)
               audio: AAC-LC 48 kHz, 160 kbps, re-encoded from IMG_5400's first 8.166667 s
               sha256 574b836de2b41adbba56040d87550ac5c165f1b8ed0109139a9b05b364fb969e
Face check:    face found in 100% of frames, face height 41.7% of the frame (guard needs >= 25%): OK

LIP-SYNC WITNESS RESULTS (standalone, current main rules: velocity mouth feature, 30 fps, first 12 s,
contradictions switched off = LIPSYNC_ALLOW_CONTRADICTION False, reliability capped at low)
------------------------------------------------------------------------------------------------------------
caseb_swapped.mp4 (voice replaced)
  status / reliability:  inconclusive / low
  title:                 Lip-sync synchrony indicator: weak or unclear
  best lag -0.30 s (positive = audio after mouth), correlation 0.193, peak prominence 0.281
  speech windows 83%, analysed 8.17 of 8.17 s
  weakest-sync timestamps (locations): 1.0 - 8.0 s
  raw rule outcome (details.rule_status): contradiction - held back, reported as inconclusive

IMG_5414.MOV (genuine clip 11, original audio)
  status / reliability:  inconclusive / low
  title:                 Lip-sync synchrony indicator: weak or unclear
  best lag -0.30 s, correlation 0.103, peak prominence 0.109
  speech windows 100%, analysed 8.17 of 8.17 s
  weakest-sync timestamps (locations): 1.0 - 6.0 s
  raw rule outcome (details.rule_status): contradiction - held back, reported as inconclusive

READ THIS BEFORE THE DEMO
-------------------------
- Both clips come out inconclusive / low, as designed. The witness cannot tell them apart: the GENUINE clip
  actually scores LESS in sync (corr 0.10, prominence 0.11) than the swapped one (corr 0.19, prominence 0.28),
  and the raw rule would have flagged both. This is exactly why contradictions are switched off (see
  lipsync_calibration.png: genuine and swapped overlap; on the held-out test 1 genuine clip would have been
  wrongly flagged).
- The chart PNG headers read "Reported: inconclusive / low · raw rule (held back): contradiction": the first part
  is what the witness reports; the raw rule outcome is shown only for transparency (it is never reported).
- Pitch framing for Case B: lip-sync is a synchrony indicator shown for human review ("we tell you when we can't
  decide"), not a detector. Don't present it as catching the voice swap.
