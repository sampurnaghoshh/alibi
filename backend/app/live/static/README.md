# ALIBI Live Desk page (static)

Standalone big-screen page served by the backend at **`GET /live`** (CLAUDE.md §14 step 3). Owner: Sampurna.
Plain HTML + CSS + vanilla JS, no build step. Everything is local, so it works offline.

| File | What |
|---|---|
| `index.html` | layout: idle screen, caption, witness tiles, graph, result / re-run / Time Machine panels, human-review banner |
| `live.css` | dark projector theme, §9 status colours |
| `live.js` | listens to `/api/live/feed` and `/api/investigations/{id}/events` and draws what they say, as they say it |
| `vendor/` | third-party libraries, vendored as files (no CDN) |

## Vendored libraries

| Library | Version | File | License | Source |
|---|---|---|---|---|
| Cytoscape.js | 3.30.2 | `vendor/cytoscape.min.js` | MIT (Copyright (c) 2016-2024, The Cytoscape Consortium), see `vendor/LICENSE-cytoscape.txt` | https://cdn.jsdelivr.net/npm/cytoscape@3.30.2/dist/cytoscape.min.js |
| qrcode-generator | 1.4.4 | `vendor/qrcode.js` | MIT (Copyright (c) 2009 Kazuhiko Arase), see `vendor/LICENSE-qrcode-generator.txt` | https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.js |

Downloaded once on 2026-11-19. SHA-256:
- `cytoscape.min.js` `83e8c54a6bec655bfd81df07df605649c268af69aeca67a5ea2da54ea42dac81`
- `qrcode.js` `18ae399f81182bc9de916e9c77b195df20cc58d6f2d55a62b085a299f1bf1780`

To update, download the new versions to the same file names and update this table.

## Use

- Start the API, open `http://127.0.0.1:8000/live` on the projector, press **F11**.
- A new case (Telegram bot, upload, demo button or re-run) opens automatically.
- `/live?id=inv_…` reopens a specific case (e.g. after a reload). **I** or **Esc** toggles the idle screen.
- The idle screen shows the bot's @username and a QR code. The username comes from `TELEGRAM_BOT_USERNAME` in `backend/.env`, or from Telegram (`getMe`) when the API starts.
