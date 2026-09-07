# VYT

Private macOS desktop application that turns a finished HeyGen presenter video
into an automatically edited long-form YouTube video. VYT timestamps the
narration, plans short visual beats, creates AI video and image B-roll, reviews
the assets and renders a final 1080p export.

## What is included

- Electron desktop interface and local durable job queue with no global active-job cap.
- Python planning, provider, quality-review and FFmpeg rendering pipeline.
- Checkpoint and paid-asset recovery logic.
- Optional presenter, split-screen, branding and QR-card placement.
- Optional channel tracker backed by a separate encrypted SQLite database.
- Unit and integration tests using synthetic data only.

API keys, user videos, generated media, channel credentials, checkpoints and
the local Whisper model are deliberately not committed.

## Requirements

- macOS on Apple Silicon.
- Node.js 22.12 or newer and npm.
- Python 3.10 or newer.
- FFmpeg and FFprobe available through Homebrew or on `PATH`.
- `whisper-cli` from whisper.cpp available through Homebrew or on `PATH`.
- A compatible whisper.cpp model; see `assets/models/README.md`.

The Python HTTP dependencies required by the packaged application are kept in
`engine/vendor` so the app does not depend on a user-level pip environment.

## Development

```bash
npm install
npm run app
```

Configure the GeminiGen/SnapGen, Algrow and Vercel AI Gateway keys from the
application's Settings screen. They are encrypted by Electron `safeStorage`
and stored in the current macOS user-data directory, outside this repository.

Build the unpacked macOS application:

```bash
npm run build:mac
```

Run the complete test suite:

```bash
npm test
```

## Project structure

- `electron/`: main process, secure preload bridge and optional Channels data boundary.
- `ui/`: application interface.
- `engine/`: Python production pipeline, prompts, providers and rendering.
- `assets/`: application icon and local-model instructions.
- `tests/`: provider, planning, rendering, recovery and Channels tests.
- `tools/`: local checkpoint-recovery utility.

## Local data and recovery

Runtime data is stored under Electron's VYT user-data directory, normally
`~/Library/Application Support/vyt`. This includes encrypted settings, history,
product cards and resumable checkpoints. Never copy that directory into Git.

Downloaded media now has a durable visual-review receipt (`pending`, `passed`,
or `rejected`) bound to its filename, size and modification time. If review is
unavailable or malformed, the job pauses and retains the paid file. Re-running
the same input and settings reviews that file rather than purchasing another.
Completed legacy checkpoints remain compatible; unindexed recovered files must
pass review before use. Pending local media is protected during budget balancing
and prevents successful checkpoint cleanup. A confirmed video-quality rejection
uses the still-image fallback instead of buying a second motion clip.

`tests/test_review_safety.py` exercises these recovery paths without paid API
calls. These tests verify pipeline behavior, not the visual quality of a new
model generation; reviewers can still miss defects.

The optional Channels screen reads `${VYT_CHANNELS_ROOT:-~/canales}` and expects
an encryption key in `.env` plus `data/canales.db`. The real database and key
are external to this repository and are not required for the video pipeline.

## Security and sharing

This repository is intended to remain private. Before sharing logs or
checkpoints, review them because they can contain local file paths, narration,
provider job identifiers or generated-asset URLs. No public license is granted.
