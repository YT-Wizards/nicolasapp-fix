import json
import hashlib
import os
import re
import shutil
import subprocess
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path


FFMPEG_CANDIDATES = ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "ffmpeg"]
FFPROBE_CANDIDATES = ["/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "ffprobe"]
WHISPER_CANDIDATES = ["/opt/homebrew/bin/whisper-cli", "/usr/local/bin/whisper-cli", "whisper-cli"]


def find_binary(candidates):
    for item in candidates:
        if os.path.isabs(item) and os.path.exists(item):
            return item
        found = shutil.which(item)
        if found:
            return found
    raise RuntimeError(f"No se encuentra {candidates[-1]}.")


FFMPEG = find_binary(FFMPEG_CANDIDATES)
FFPROBE = find_binary(FFPROBE_CANDIDATES)
# Whisper is resolved when transcription is requested. Keeping it lazy allows
# planning, provider and recovery tests to run without the optional local model
# binary installed, while production still gets the same actionable error.
WHISPER = None
DEFAULT_LOCAL_PROCESS_TIMEOUT = 120.0
TRANSCRIPTION_CHUNK_SECONDS = 10 * 60
TRANSCRIPTION_OVERLAP_SECONDS = 1.0


def set_job_deadline(deadline):
    """Legacy compatibility hook; VYT no longer has a job-wide deadline."""
    return None


def _job_timeout(requested):
    # Every subprocess remains bounded even when its caller does not specify a
    # timeout (notably ffprobe), but no local process can end the whole job just
    # because the production has been running for a long time.
    if requested is None:
        return DEFAULT_LOCAL_PROCESS_TIMEOUT
    return max(0.05, float(requested))


def run(command, timeout=None, capture=False):
    kwargs = {"check": True, "timeout": _job_timeout(timeout)}
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    else:
        kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        return subprocess.run(command, **kwargs)
    except subprocess.CalledProcessError as error:
        stderr = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
        raise RuntimeError(stderr[-1800:] or f"Falló: {command[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Una operación local tardó demasiado y VYT la detuvo para poder reanudarla.") from error


def probe(path):
    result = run([FFPROBE, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json", str(path)], capture=True)
    data = json.loads(result.stdout)
    video = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"), {})
    raw_duration = data.get("format", {}).get("duration")
    try:
        duration = float(raw_duration or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {"duration": duration, "width": int(video.get("width") or 0), "height": int(video.get("height") or 0)}


def _transcription_cache_key(source, duration, model_path, start, end):
    source = Path(source)
    stat = source.stat()
    payload = {
        "source": str(source.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "duration": round(float(duration), 3),
        "model": str(Path(model_path).resolve()),
        "start": round(float(start), 3),
        "end": round(float(end), 3),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _parse_whisper_segments(data, offset=0.0):
    segments = []
    for item in data.get("transcription", []):
        offsets = item.get("offsets") or {}
        start = float(offset) + float(offsets.get("from", 0)) / 1000.0
        end = float(offset) + float(offsets.get("to", 0)) / 1000.0
        text = str(item.get("text") or "").strip()
        if text and end >= start:
            segments.append({"start": start, "end": end, "text": text})
    return segments


def _merge_transcription_segments(segments, duration):
    ordered = sorted(segments, key=lambda item: (float(item["start"]), float(item["end"])))
    merged = []
    for item in ordered:
        start = max(0.0, min(float(duration), float(item["start"])))
        end = max(start, min(float(duration), float(item["end"])))
        text = " ".join(str(item.get("text") or "").split()).strip()
        if not text or end <= start:
            continue
        current = {"start": round(start, 3), "end": round(end, 3), "text": text}
        if merged:
            previous = merged[-1]
            overlap = min(previous["end"], current["end"]) - max(previous["start"], current["start"])
            similarity = SequenceMatcher(None, previous["text"].lower(), text.lower()).ratio()
            if overlap > 0 or (current["start"] - previous["end"] < TRANSCRIPTION_OVERLAP_SECONDS and similarity >= 0.72):
                if similarity >= 0.72:
                    if len(text) > len(previous["text"]):
                        merged[-1] = current
                    continue
                current["start"] = max(current["start"], previous["end"])
                if current["end"] <= current["start"]:
                    continue
        merged.append(current)
    if not merged:
        raise RuntimeError("No se ha podido extraer la narración del vídeo.")
    for previous, current in zip(merged, merged[1:]):
        if current["start"] < previous["end"]:
            current["start"] = previous["end"]
    return merged


def _run_whisper(whisper, wav, output_prefix, model_path, timeout, disable_gpu=False):
    # Let whisper.cpp use Metal on macOS when it is available.  ``-ng`` forces
    # CPU-only inference and turned short manual-mode plans into multi-minute
    # waits on this app's target platform.  The binary still falls back to CPU
    # automatically on machines without GPU support.
    command = [whisper, "-m", str(model_path), "-f", str(wav), "-l", "auto", "-oj", "-of", str(output_prefix), "-t", "8", "-np"]
    if disable_gpu:
        command.append("-ng")
    try:
        run(command, timeout=timeout)
    except RuntimeError:
        # English-only models do not accept auto; retry only this chunk.
        command[command.index("auto")] = "en"
        run(command, timeout=timeout)
    output_path = Path(f"{output_prefix}.json")
    return json.loads(output_path.read_text())


def transcribe(source, duration, workspace, model_path):
    whisper = find_binary(WHISPER_CANDIDATES)
    workspace = Path(workspace)
    cache = workspace / "transcription-chunks"
    cache.mkdir(parents=True, exist_ok=True)
    chunk_length = max(1.0, float(TRANSCRIPTION_CHUNK_SECONDS))
    overlap = min(TRANSCRIPTION_OVERLAP_SECONDS, chunk_length / 4)
    chunks = []
    start = 0.0
    index = 0
    while start < float(duration) - 0.01:
        end = min(float(duration), start + chunk_length)
        chunks.append((index, start, end))
        if end >= float(duration):
            break
        start = end - overlap
        index += 1

    all_segments = []
    source = Path(source)
    for index, start, end in chunks:
        key = _transcription_cache_key(source, duration, model_path, start, end)
        cached = cache / f"{key}.json"
        if cached.exists():
            data = json.loads(cached.read_text())
        else:
            wav = cache / f"{key}.wav"
            output_prefix = cache / f"{key}.transcript"
            run([
                FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(source),
                "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav),
            ], timeout=max(600, int((end - start) * 2)))
            data = _run_whisper(
                whisper, wav, output_prefix, model_path,
                timeout=max(600, int((end - start) * 2)),
            )
            # Some whisper.cpp/Metal combinations return a successful JSON
            # envelope with no segments.  Falling back to CPU preserves a
            # usable manual workflow instead of surfacing a cryptic IndexError
            # from the downstream timing planner.
            if not _parse_whisper_segments(data):
                cpu_prefix = cache / f"{key}.cpu.transcript"
                data = _run_whisper(
                    whisper, wav, cpu_prefix, model_path,
                    timeout=max(600, int((end - start) * 2)), disable_gpu=True,
                )
                Path(f"{cpu_prefix}.json").unlink(missing_ok=True)
            cached.write_text(json.dumps(data, ensure_ascii=False))
            wav.unlink(missing_ok=True)
            Path(f"{output_prefix}.json").unlink(missing_ok=True)
        all_segments.extend(_parse_whisper_segments(data, offset=start))
    return _merge_transcription_segments(all_segments, float(duration))


def extract_review_strip(video_path, output_path, visible_duration=None):
    """Build the contact sheet from the exact prefix and crop used in VYT.

    Veo returns eight seconds, while a FaceTuber-style beat is commonly only
    three or four seconds.  Reviewing frames from the unused tail could approve
    an action that never reaches the final edit (or reject an artefact that is
    cut away), so sampling is bounded to the scene duration.  The same small
    safety overscan used by ``render_segment`` is applied here as well.
    """
    info = probe(video_path)
    duration = max(0.2, info["duration"])
    if visible_duration is not None:
        duration = min(duration, max(0.2, float(visible_duration)))
    positions = [max(0.05, duration * fraction) for fraction in (0.12, 0.50, 0.88)]
    frames = []
    output_path = Path(output_path)
    for index, position in enumerate(positions):
        frame = output_path.with_name(f"{output_path.stem}-{index}.jpg")
        review_filter = (
            "crop=trunc(iw*0.92/2)*2:trunc(ih*0.92/2)*2:(iw-ow)/2:(ih-oh)/2,"
            "scale=640:-2:flags=lanczos"
        )
        run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{position:.3f}", "-i", str(video_path), "-frames:v", "1", "-vf", review_filter, "-q:v", "3", str(frame)], timeout=90)
        frames.append(frame)
    run([
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(frames[0]), "-i", str(frames[1]), "-i", str(frames[2]),
        "-filter_complex", "[0:v][1:v][2:v]hstack=inputs=3,scale=1440:-2",
        "-frames:v", "1", "-q:v", "3", str(output_path),
    ], timeout=90)
    for frame in frames:
        frame.unlink(missing_ok=True)
    return output_path


def extract_split_review_crop(image_path, output_path):
    """Preview precisely the centre 8:9 crop shown beside the presenter."""
    output_path = Path(output_path)
    run([
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(image_path), "-frames:v", "1",
        "-vf", "scale=960:1080:force_original_aspect_ratio=increase:flags=lanczos,crop=960:1080,setsar=1",
        "-q:v", "3", str(output_path),
    ], timeout=90)
    return output_path


def extract_presenter_reference_frames(source, duration, workspace):
    """Extract three clean identity references from the HeyGen presenter.

    The source is a talking-head video, so a centred portrait crop removes most
    of the set that would otherwise be copied into generated B-roll.  Three
    moments are kept for the story director; it selects the clearest one once
    for the whole project, rather than changing identity from scene to scene.
    """
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    duration = max(0.1, float(duration))
    paths = []
    for index, fraction in enumerate((0.17, 0.47, 0.77), start=1):
        output = workspace / f"presenter-reference-{index}.jpg"
        position = min(max(0.0, duration - 0.05), duration * fraction)
        portrait_filter = (
            "crop=trunc(iw*0.62/2)*2:trunc(ih*0.94/2)*2:"
            "(iw-ow)/2:(ih-oh)/2,scale=720:-2:flags=lanczos"
        )
        run([
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{position:.3f}", "-i", str(source),
            "-frames:v", "1", "-vf", portrait_filter, "-q:v", "2", str(output),
        ], timeout=90)
        paths.append(output)
    return paths


def image_zoom_travel(duration):
    """Return subtle travel at a near-constant visual speed."""
    return min(0.065, max(0.025, float(duration) * 0.012))


def image_zoom_expression(scene, duration, fps):
    """Return a deterministic, gentle Ken Burns zoom for a still image.

    Basing the value on the absolute output-frame counter (``on``) keeps the
    movement monotonic and independent from FFmpeg's internal zoom state.
    """
    scene_number = int(str(scene.get("id", "0")).lstrip("b") or 0)
    frames = max(2, round(float(duration) * int(fps)))
    travel = image_zoom_travel(duration)
    end_zoom = 1.0 + travel
    step = travel / (frames - 1)
    if scene_number % 2 == 0:
        return f"min(1.0+on*{step:.10f},{end_zoom:.6f})"
    return f"max({end_zoom:.6f}-on*{step:.10f},1.0)"


def image_motion_filter(scene, duration, fps, width=1920, height=1080):
    """Build one high-precision Ken Burns sequence without pixel-grid jitter.

    ``zoompan`` rounds its crop coordinates to source pixels.  Working at four
    times the final resolution makes those rounding steps sub-pixel at output
    size.  The still is supplied once and ``d`` creates the complete sequence,
    so the large resize is not repeated for every output frame.
    """
    fps = int(fps)
    frames = max(2, round(float(duration) * fps))
    width = int(width)
    height = int(height)
    work_width = width * 4
    work_height = height * 4
    zoom = image_zoom_expression(scene, duration, fps)
    return (
        f"scale={work_width}:{work_height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={work_width}:{work_height},"
        f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':d={frames}:s={width}x{height}:fps={fps},setsar=1"
    )


def _plain_words(value):
    normalized = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.findall(r"[a-z0-9]+", normalized)


def product_qr_window(transcript, duration, product_name, display_seconds=None):
    """Locate the spoken CTA and match the card to its real phrase timing.

    Transcription segments already contain word-level timing boundaries.  The QR
    therefore follows the complete segment that contains the strongest CTA
    instead of occupying an arbitrary six-second slot. ``display_seconds`` is
    retained only for backwards compatibility with older callers/tests.
    """
    duration = max(0.0, float(duration))
    product_words = [word for word in _plain_words(product_name) if len(word) >= 3]
    cta_words = {
        "buy", "purchase", "shop", "order", "book", "scan", "website", "link",
        "comprar", "compra", "pedido", "libro", "escanea", "web", "enlace",
        "descripcion", "producto", "tienda",
    }
    candidates = []
    for index, segment in enumerate(transcript or []):
        words = set(_plain_words(segment.get("text", "")))
        start = max(0.0, float(segment.get("start") or 0))
        end = min(duration, max(start, float(segment.get("end") or start)))
        exact = bool(product_words and all(word in words for word in product_words))
        commerce = len(words.intersection(cta_words))
        if exact or commerce:
            # A direct buying/scanning phrase is more useful than a casual early
            # product mention. On equal scores, use the first spoken CTA.
            score = commerce * 5 + (4 if exact else 0)
            candidates.append((score, -index, start, end))
    if candidates:
        _, _, start, end = max(candidates)
        start = max(0.0, start - 0.10)
        end = min(duration, end + 0.10)
        if end - start < 1.5:
            end = min(duration, start + 1.5)
    else:
        # Never invent a sales moment when the narration contains no CTA.
        return None
    return {"start": start, "end": end, "kind": "cta"}


def product_qr_reminder_window(items, primary_window, duration):
    """Use one existing later avatar shot for a single unobtrusive reminder."""
    if not primary_window:
        return None
    primary_end = float(primary_window["end"])
    target = max(float(duration) * 0.68, primary_end + float(duration) * 0.20)
    candidates = [
        item for item in items
        if item.get("type") == "avatar" and float(item.get("start", 0)) >= primary_end + 8.0
    ]
    if not candidates:
        return None
    item = min(candidates, key=lambda value: abs(float(value["start"]) - target))
    return {
        "start": float(item["start"]),
        "end": float(item["end"]),
        "kind": "reminder",
    }


def product_overlay_for_scene(scene, windows):
    if not windows:
        return None
    if isinstance(windows, dict):
        windows = [windows]
    scene_start = float(scene["start"])
    scene_end = float(scene["end"])
    for window in windows:
        overlap_start = max(scene_start, float(window["start"]))
        overlap_end = min(scene_end, float(window["end"]))
        if overlap_end - overlap_start > 0.02:
            return {
                "start": max(0.0, overlap_start - scene_start),
                "end": max(0.0, overlap_end - scene_start),
            }
    return None


def _product_overlay_filter(base_label, card_input, overlay):
    start = max(0.0, float(overlay["start"]))
    end = max(start + 0.02, float(overlay["end"]))
    return (
        f"[{card_input}:v]scale=500:-2:force_original_aspect_ratio=decrease,format=rgba[productcard];"
        f"[{base_label}][productcard]overlay=x=W-w-64:y=58:format=auto:"
        f"enable='between(t,{start:.3f},{end:.3f})',format=yuv420p[vout]"
    )


def render_segment(scene, source, asset, output, fps=30, product_card=None, product_overlay=None):
    duration = float(scene["duration"])
    start = float(scene["start"])
    media_type = scene["type"]
    frames = max(2, round(duration * int(fps)))
    base = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
    encode = ["-an", "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)]
    full_filter = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1"
    show_product = bool(product_card and product_overlay)
    if media_type == "avatar":
        inputs = ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source)]
        if show_product:
            graph = f"[0:v]{full_filter}[base];" + _product_overlay_filter("base", 1, product_overlay)
            command = base + inputs + ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(product_card), "-filter_complex", graph, "-map", "[vout]"] + encode
        else:
            command = base + inputs + ["-vf", full_filter] + encode
    elif media_type == "video":
        # SnapGen's Veo output currently carries a tiny provider mark in the extreme
        # lower-right despite reporting has_watermark=0. A symmetric safety overscan
        # removes only the outer 4% per edge and keeps the framing natural.
        video_filter = (
            "crop=trunc(iw*0.92/2)*2:trunc(ih*0.92/2)*2:(iw-ow)/2:(ih-oh)/2,"
            "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,"
            "tpad=stop_mode=clone:stop_duration=8"
        )
        # Never restart a generated action. If a nominal eight-second provider
        # file is a few frames short, hold its final frame rather than looping
        # back to frame one; the explicit output limit cuts exactly at the beat.
        inputs = ["-i", str(asset)]
        output_limit = ["-t", f"{duration:.3f}"]
        if show_product:
            graph = f"[0:v]{video_filter}[base];" + _product_overlay_filter("base", 1, product_overlay)
            command = base + inputs + ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(product_card), "-filter_complex", graph, "-map", "[vout]"] + output_limit + encode
        else:
            command = base + inputs + ["-vf", video_filter] + output_limit + encode
    elif media_type == "split":
        # FaceTuber keeps one visual grammar: real presenter on the left and the
        # explanatory resource on the right.  Alternating sides makes the host
        # jump across the frame and weakens continuity.
        stack = "[avatar][visual]"
        visual_filter = image_motion_filter(scene, duration, fps, 960, 1080)
        filter_complex = (
            "[0:v]scale=960:1080:force_original_aspect_ratio=increase,crop=960:1080[avatar];"
            f"[1:v]{visual_filter}[visual];"
            f"{stack}hstack=inputs=2,setsar=1"
        )
        inputs = ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source), "-framerate", str(fps), "-i", str(asset)]
        if show_product:
            graph = filter_complex + "[base];" + _product_overlay_filter("base", 2, product_overlay)
            command = base + inputs + ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(product_card), "-filter_complex", graph, "-map", "[vout]", "-frames:v", str(frames)] + encode
        else:
            command = base + inputs + ["-filter_complex", filter_complex, "-frames:v", str(frames)] + encode
    else:
        # A single source frame produces the whole high-precision zoom sequence.
        # This removes the former pause/jump pattern caused by pixel rounding.
        visual_filter = image_motion_filter(scene, duration, fps, 1920, 1080)
        inputs = ["-framerate", str(fps), "-i", str(asset)]
        if show_product:
            graph = f"[0:v]{visual_filter}[base];" + _product_overlay_filter("base", 1, product_overlay)
            command = base + inputs + ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(product_card), "-filter_complex", graph, "-map", "[vout]", "-frames:v", str(frames)] + encode
        else:
            command = base + inputs + ["-vf", visual_filter, "-frames:v", str(frames)] + encode
    run(command, timeout=max(180, int(duration * 20)))
    rendered = Path(output)
    actual = probe(rendered)["duration"]
    if abs(actual - duration) > max(0.18, 2.0 / fps):
        rendered.unlink(missing_ok=True)
        raise RuntimeError(
            f"El plano {scene.get('id', '')} debía durar {duration:.2f} s y duró {actual:.2f} s."
        )
    return rendered


def assemble(segments, source, output, duration, workspace):
    output = Path(output)
    if output.resolve() == Path(source).resolve():
        raise RuntimeError("La salida no puede sobrescribir el vídeo fuente de HeyGen.")
    expected = 0.0
    for item in segments:
        item_duration = probe(item)["duration"]
        if item_duration <= 0:
            raise RuntimeError(f"El montaje contiene un plano vacío: {Path(item).name}.")
        expected += item_duration
    if abs(expected - duration) > max(0.75, len(segments) / 30.0 + 0.20):
        raise RuntimeError(
            f"Los planos suman {expected:.2f} s, pero el vídeo debe durar {duration:.2f} s."
        )
    concat_file = Path(workspace) / "concat.txt"
    concat_file.write_text("\n".join(f"file '{str(Path(item).resolve()).replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'" for item in segments) + "\n")
    silent = Path(workspace) / "silent.mp4"
    run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(silent)], timeout=1200)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.stem}.vyt-partial{output.suffix}")
    partial.unlink(missing_ok=True)
    try:
        run([
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(silent), "-t", f"{duration:.3f}", "-i", str(source),
            "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.3f}", "-movflags", "+faststart", str(partial),
        ], timeout=1200)
        final_info = probe(partial)
        if abs(final_info["duration"] - duration) > 1.2 or final_info["width"] != 1920 or final_info["height"] != 1080:
            raise RuntimeError("La comprobación final detectó una duración o resolución incorrecta.")
        partial.replace(output)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return output
