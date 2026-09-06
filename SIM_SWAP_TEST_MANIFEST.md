# Local SIM-swap test asset

The downloaded HeyGen source and its extracted voice track are local-only test
assets and must not be committed to Git.

- Source video: `/Users/wozglas/Downloads/heygen-sim-swap-test.mp4`
- Canonical voiceover: `/Users/wozglas/Downloads/heygen-sim-swap-test-voiceover.m4a`
- Script: `/Users/wozglas/.codex/attachments/a6644b36-5fbb-4450-b915-142874fdc707/pasted-text.txt`
- Source duration: 582.762 seconds
- Source format: 1920×1080 H.264 + stereo AAC 48 kHz
- Source SHA-256: `53e7bc36b60bfa192bb2e4de11c1170a3f503b5784f35bde478fd396d02acec1`
- Voiceover SHA-256: `29e594ee63831b6d904f8ae7074119302615467fb56122e92ba1f14f5c6b2751`

For app tests, select the source video and enable the 90-second test mode. The
pipeline must reuse the audio track already embedded in this source; no second
voice generation is needed.
