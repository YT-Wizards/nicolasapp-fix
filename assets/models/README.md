# Local transcription model

VYT uses `whisper-cli` from whisper.cpp. Place one compatible model in this
directory before running the complete application:

- `ggml-base.bin` (recommended default)
- `ggml-small.bin`
- `ggml-small.en.bin`

The model binary is intentionally excluded from Git because the current local
file is about 148 MB, above GitHub's normal per-file limit. Alternatively, set
`VYT_WHISPER_MODEL` to an absolute path containing a compatible model.
