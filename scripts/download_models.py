r"""
Download the model weights ALIBI uses into data/models/ (gitignored). Run once, with internet:

    backend\.venv\Scripts\python.exe scripts\download_models.py

- faster-whisper "tiny" (multilingual, CTranslate2 int8-ready) from Systran/faster-whisper-tiny on Hugging
  Face, ~75 MB. Licence: MIT (OpenAI Whisper weights, MIT; Systran conversion, MIT). Used by
  app/transcribe.py for spoken claims. Without it, transcription is skipped with a stated limitation.

Each file's size is checked against the Hugging Face listing after download.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODELS = {
    "faster-whisper-tiny": {"repo": "Systran/faster-whisper-tiny", "licence": "MIT",
                            "files": ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]},
}


def main() -> int:
    try:
        from huggingface_hub import HfApi, hf_hub_download
    except ImportError:
        print("huggingface_hub is missing: pip install faster-whisper (it brings huggingface_hub)")
        return 1
    api = HfApi()
    for name, spec in MODELS.items():
        dest = REPO / "data" / "models" / name
        dest.mkdir(parents=True, exist_ok=True)
        sizes = {s.rfilename: s.size for s in api.model_info(spec["repo"], files_metadata=True).siblings}
        for f in spec["files"]:
            path = Path(hf_hub_download(spec["repo"], f, local_dir=dest))
            ok = sizes.get(f) in (None, path.stat().st_size)
            print(f"{name}/{f}: {path.stat().st_size} bytes {'ok' if ok else 'SIZE MISMATCH'}")
            if not ok:
                return 1
        (dest / "LICENCE.txt").write_text(f"{spec['repo']}: {spec['licence']}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
