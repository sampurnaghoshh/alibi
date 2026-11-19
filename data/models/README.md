# Models

## face_landmarker.task

Used by the lip-sync witness (`backend/app/witnesses/lipsync.py`) to find the face and the inner-lip
landmarks in each frame. The path is set by `LIPSYNC_FACE_MODEL` in `backend/app/config.py`
(Nooman's section). If the file is missing, lip-sync returns an `inconclusive` finding
("face model not found"); it never crashes an investigation.

- **What it is:** Google MediaPipe Face Landmarker model bundle, **float16**, for the MediaPipe Tasks API
  (`mediapipe.tasks.python.vision.FaceLandmarker`). mediapipe 1.x has no `mp.solutions`, and the model is
  not included in the pip package.
- **Contents:** three models: BlazeFace short-range face detector (128x128 input),
  Face Mesh V2 face landmarker (256x256 input, 478 landmarks) and Blendshape V2 (52 blendshape scores;
  ALIBI does not use the blendshapes).
- **Download:** <https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task>
  (listed under "Models" at <https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker>)
- **Size / SHA-256 of the committed copy:** 3,758,596 bytes,
  `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`
  (downloaded 2026-11-19 from the `latest` URL, which can change).
- **License:** Apache License, Version 2.0. Each of the three official model cards states
  "LICENSED UNDER Apache License, Version 2.0":
  - [MediaPipe BlazeFace Model Card (Short Range)](https://storage.googleapis.com/mediapipe-assets/MediaPipe%20BlazeFace%20Model%20Card%20(Short%20Range).pdf), model date June 9, 2021
  - [Model Card MediaPipe Face Mesh V2](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20MediaPipe%20Face%20Mesh%20V2.pdf), model date September 15, 2022
  - [Model Card Blendshape V2](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Blendshape%20V2.pdf), model date November 11, 2022

  License text: <https://www.apache.org/licenses/LICENSE-2.0>

To re-download (PowerShell, from the repo root):

```powershell
curl.exe -L -o data\models\face_landmarker.task https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task
```
