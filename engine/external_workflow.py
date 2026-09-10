"""Plan and assemble externally generated Veo clips.

This module is the seam between VYT's shared editorial planning and a user's
own generation tool.  It never submits media to a provider.
"""

from __future__ import annotations

import json
import re
import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List

from media import assemble, probe, render_segment, transcribe


MAX_CLIP_SECONDS = 8.0


def _sentences(script: str) -> List[str]:
    values = re.split(r"(?<=[.!?。！？])\s+|\n+", " ".join(str(script or "").split()))
    return [value.strip() for value in values if value.strip()]


def _slice_sentence(text: str, parts: int) -> List[str]:
    words = text.split()
    if parts <= 1 or len(words) < 2:
        return [text]
    chunks = []
    for index in range(parts):
        left = round(index * len(words) / parts)
        right = round((index + 1) * len(words) / parts)
        chunks.append(" ".join(words[left:right]).strip())
    return [chunk for chunk in chunks if chunk]


def build_external_plan(script: str, transcript: Iterable[Dict[str, Any]], style: str) -> Dict[str, Any]:
    """Create numbered <=8-second prompts using audio-derived phrase timing."""
    phrases = _sentences(script)
    timing = [item for item in transcript if str(item.get("text") or "").strip()]
    if not phrases:
        raise ValueError("Añade un guion antes de crear los prompts.")
    if not timing:
        raise ValueError("No se encontraron frases en el audio.")
    audio_start = float(timing[0]["start"])
    audio_end = float(timing[-1]["end"])
    # Whisper gives phrase-level time ranges.  Preserve those real boundaries
    # where possible instead of spreading a script uniformly over the audio.
    # A script is often lightly edited after recording, so this is deliberately
    # sequential and tolerant rather than requiring exact text equality.
    segment_index = 0
    scenes = []
    number = 1
    for phrase_index, phrase in enumerate(phrases):
        remaining_phrases = len(phrases) - phrase_index - 1
        target_words = max(1, len(phrase.split()))
        start_index = segment_index
        spoken_words = 0
        while segment_index < len(timing):
            # Keep one recognised segment available for every remaining phrase.
            if segment_index + 1 >= len(timing) - remaining_phrases and spoken_words > 0:
                break
            spoken_words += max(1, len(str(timing[segment_index]["text"]).split()))
            segment_index += 1
            if spoken_words >= target_words:
                break
        if phrase_index == len(phrases) - 1:
            segment_index = len(timing)
        start = float(timing[start_index]["start"])
        phrase_end = float(timing[max(start_index, segment_index - 1)]["end"])
        parts = max(1, int((phrase_end - start + MAX_CLIP_SECONDS - 0.001) // MAX_CLIP_SECONDS))
        phrase_parts = _slice_sentence(phrase, parts)
        for part_index, part in enumerate(phrase_parts):
            part_start = start + (phrase_end - start) * part_index / len(phrase_parts)
            end = start + (phrase_end - start) * (part_index + 1) / len(phrase_parts)
            duration = round(min(MAX_CLIP_SECONDS, end - part_start), 3)
            prompt = (
                f"{number:03d}. {part}\n"
                f"Style: {style.strip() or 'realistic consumer-camera documentary B-roll'}. "
                "One literal visual moment that directly matches this narration. "
                "Natural lighting, stable camera, no captions, no readable invented text, no watermark."
            )
            scenes.append({
                "number": number, "id": f"{number:03d}", "start": round(part_start, 3),
                "end": round(end, 3), "duration": duration, "narration": part, "prompt": prompt,
                "expected_filename": f"{number:03d}.mp4",
            })
            number += 1
    return {
        "version": 1, "style": style.strip(), "duration": round(audio_end - audio_start, 3),
        "script": script, "scenes": scenes,
        "instructions": "Generate one clip per numbered prompt. Name clips 001.mp4, 002.mp4, etc. Clips may be up to 8 seconds; VYT trims them to the planned timing.",
    }


def validate_clip_inventory(plan: Dict[str, Any], durations: Dict[str, float]) -> Dict[str, Any]:
    if not isinstance(plan.get("scenes"), list) or not plan["scenes"]:
        raise ValueError("El plan de prompts está vacío o no es válido.")
    missing, too_short, ready = [], [], []
    for scene in plan.get("scenes", []):
        number = int(scene["number"])
        filename = str(scene.get("expected_filename") or f"{number:03d}.mp4")
        actual = durations.get(filename)
        required = float(scene["duration"])
        if actual is None:
            missing.append(number)
        elif float(actual) + 0.08 < required:
            too_short.append({"number": number, "required": required, "actual": round(float(actual), 3)})
        else:
            ready.append(number)
    return {"ready": not missing and not too_short, "missing": missing, "too_short": too_short, "ready_numbers": ready}


def find_clips(folder: str | Path) -> Dict[str, Path]:
    folder = Path(folder)
    clips = {}
    for path in folder.iterdir():
        if not path.is_file() or path.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
            continue
        match = re.match(r"^(\d{1,4})\b", path.stem)
        if match:
            clips[f"{int(match.group(1)):03d}.mp4"] = path
    return clips


def render_external_plan(plan: Dict[str, Any], audio_path: str | Path, clips_folder: str | Path, output: str | Path, workspace: str | Path) -> Path:
    clips = find_clips(clips_folder)
    durations = {name: probe(path)["duration"] for name, path in clips.items()}
    report = validate_clip_inventory(plan, durations)
    if not report["ready"]:
        details = []
        if report["missing"]:
            details.append("faltan: " + ", ".join(f"{number:03d}" for number in report["missing"]))
        if report["too_short"]:
            details.append("demasiado cortos: " + ", ".join(f"{item['number']:03d}" for item in report["too_short"]))
        raise ValueError("No se puede montar todavía — " + "; ".join(details))
    workspace = Path(workspace)
    segments_dir = workspace / "external-segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    segments = []
    for index, scene in enumerate(plan["scenes"]):
        rendered = render_segment({**scene, "type": "video"}, audio_path, clips[scene["expected_filename"]], segments_dir / f"{index:04d}.mp4")
        segments.append(rendered)
    return assemble(segments, audio_path, output, float(plan["duration"]), workspace)


def _emit(event: Dict[str, Any]) -> None:
    print("VYT_EVENT:" + json.dumps(event, ensure_ascii=False), flush=True)


def _model_path(root_dir: str | Path) -> Path:
    model = Path(root_dir) / "assets" / "models" / "ggml-base.bin"
    if not model.exists():
        raise RuntimeError("Falta el modelo local de transcripción de VYT. Abre la app una vez para descargarlo.")
    return model


def command_plan(args: argparse.Namespace) -> Dict[str, Any]:
    script = Path(args.script).read_text(encoding="utf-8")
    audio = Path(args.audio)
    if not audio.exists():
        raise RuntimeError("No encuentro el archivo de audio.")
    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    duration = probe(audio)["duration"]
    if duration <= 1:
        raise RuntimeError("El audio no tiene una duración válida.")
    _emit({"status": "running", "progress": 8, "phase": "Transcribiendo audio", "detail": "Detectando frases y tiempos reales"})
    transcript = transcribe(audio, duration, workspace, _model_path(args.root_dir))
    _emit({"status": "running", "progress": 75, "phase": "Planificando clips", "detail": "Creando prompts numerados de hasta 8 segundos"})
    plan = build_external_plan(script, transcript, args.style)
    Path(args.output).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    _emit({"status": "running", "progress": 100, "phase": "Prompts listos", "detail": f"{len(plan['scenes'])} clips planificados"})
    return {"ok": True, "plan_path": str(Path(args.output)), "plan": plan}


def command_validate(args: argparse.Namespace) -> Dict[str, Any]:
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    clips = find_clips(args.clips)
    durations = {name: probe(path)["duration"] for name, path in clips.items()}
    return {"ok": True, "report": validate_clip_inventory(plan, durations), "found": len(clips)}


def command_render(args: argparse.Namespace) -> Dict[str, Any]:
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    _emit({"status": "rendering", "progress": 5, "phase": "Comprobando clips", "detail": "Verificando archivos y duraciones"})
    output = render_external_plan(plan, args.audio, args.clips, args.output, args.workspace)
    _emit({"status": "rendering", "progress": 100, "phase": "Vídeo terminado", "detail": "Guardado en Descargas"})
    return {"ok": True, "output_path": str(output), "cost_usd": 0, "generated_counts": {"video": len(plan.get("scenes", []))}}


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--script", required=True); plan.add_argument("--audio", required=True)
    plan.add_argument("--style", required=True); plan.add_argument("--output", required=True)
    plan.add_argument("--workspace", required=True); plan.add_argument("--root-dir", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--plan", required=True); validate.add_argument("--clips", required=True)
    render = commands.add_parser("render")
    render.add_argument("--plan", required=True); render.add_argument("--audio", required=True)
    render.add_argument("--clips", required=True); render.add_argument("--output", required=True); render.add_argument("--workspace", required=True)
    args = parser.parse_args()
    try:
        result = {"plan": command_plan, "validate": command_validate, "render": command_render}[args.command](args)
    except Exception as error:
        result = {"ok": False, "error": str(error)}
    print("VYT_RESULT:" + json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
