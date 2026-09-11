"""Plan and assemble externally generated Veo clips.

This module is the seam between VYT's shared editorial planning and a user's
own generation tool.  It never submits media to a provider.
"""

from __future__ import annotations

import json
import re
import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List

from media import assemble, probe, render_segment, transcribe
from providers import VercelGatewayClient


MAX_CLIP_SECONDS = 8.0
EXTERNAL_PROMPT_BATCH_SIZE = 6

EXTERNAL_PROMPT_SYSTEM = """
You write production-ready Veo prompts for factual long-form YouTube B-roll.
Return strict JSON only. Every prompt must depict the exact narration, not a vague thematic symbol. Use ordinary, believable consumer-camera documentary footage: natural lighting, practical locations and no cinematic polish.
""".strip()

EXTERNAL_PROMPT_REQUEST = """
Turn every supplied timed narration beat into one self-contained Veo video prompt. The clip will be trimmed to the supplied duration, so design only one stable real-world shot and at most one small physical action.

For every item return id (copy it exactly), visual_prompt (English only), and reject_if (three to six short generation failures to avoid). The visual prompt must state the visible subject, exact action or observable state, environment, composition and camera angle. State the minimum necessary people and important objects.

Hard rules: match the narration literally without inventing facts, text, brands or a named speaker; no person speaking to camera unless testimony is explicit; use a stable side, three-quarter, over-the-shoulder or detail composition when it clarifies the action; no montage, cuts, zooms, transitions or before-and-after sequence; no readable screens, messages, labels, captions or interface text. When a person checks, reads, holds or operates a phone or laptop, its display faces that person; use a side, over-the-shoulder or natural oblique view, never a frontal screen-showing pose unless narration explicitly says they show it to somebody. Avoid hand-heavy staging. If hands matter, specify one easy grip and normal anatomy. Prevent extra limbs, duplicated people and duplicated props. No generic talking heads, no eye contact with camera, watermark, glossy advertising or cinematic lighting. Do not repeat narration verbatim: convert its meaning into a concrete visual moment.

Selected style: {style}

Return exactly this JSON shape:
{{"scenes":[{{"id":"001","visual_prompt":"...","reject_if":["..."]}}]}}

TIMED BEATS:
{beats}
""".strip()


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


def _fallback_visual_prompt(narration: str, style: str) -> str:
    """Usable offline prompt retained only when the AI rewriting pass fails."""
    return (
        "A single stable 16:9 consumer-camera documentary shot illustrating "
        f"this exact narration: {narration.strip()} "
        f"Use an ordinary setting consistent with {style.strip() or 'realistic documentary B-roll'}, "
        "a clear side, three-quarter or detail composition, natural available light, one restrained action at most, "
        "no readable screens or text, and no talking to camera."
    )


def _rewrite_external_prompts(plan: Dict[str, Any], client: Any) -> List[str]:
    """Replace raw narration with structured, scene-specific Veo prompts."""
    scenes = list(plan.get("scenes") or [])
    warnings: List[str] = []

    def rewrite_batch(batch: List[Dict[str, Any]]) -> None:
        """Apply one valid structured model response, or raise without mutation."""
        beats = [{
            "id": scene["id"], "start": scene["start"], "end": scene["end"],
            "duration_seconds": scene["duration"], "narration": scene["narration"],
        } for scene in batch]
        result = client.chat_json(
            EXTERNAL_PROMPT_SYSTEM,
            EXTERNAL_PROMPT_REQUEST.format(
                style=plan.get("style") or "realistic documentary B-roll",
                beats=json.dumps(beats, ensure_ascii=False),
            ),
            max_tokens=2200,
            response_schema={
                "type": "object",
                "properties": {"scenes": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "visual_prompt": {"type": "string"},
                        "reject_if": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["id", "visual_prompt", "reject_if"],
                    "additionalProperties": False,
                }}},
                "required": ["scenes"], "additionalProperties": False,
            },
        )
        rewritten = {str(item.get("id") or "").strip(): item for item in (result.get("scenes") or []) if isinstance(item, dict)}
        missing = [scene["id"] for scene in batch if len(str(rewritten.get(scene["id"], {}).get("visual_prompt") or "").strip()) < 40]
        if missing:
            raise ValueError("missing prompt IDs: " + ", ".join(missing))
        for scene in batch:
            item = rewritten[scene["id"]]
            scene["prompt"] = f"{scene['id']}. {str(item['visual_prompt']).strip()}"
            rejects = item.get("reject_if")
            scene["reject_if"] = [str(value).strip() for value in rejects if str(value).strip()][:6] if isinstance(rejects, list) else []
            scene["prompt_source"] = "ai"

    for offset in range(0, len(scenes), EXTERNAL_PROMPT_BATCH_SIZE):
        batch = scenes[offset:offset + EXTERNAL_PROMPT_BATCH_SIZE]
        try:
            rewrite_batch(batch)
        except Exception as error:
            # A long structured response can occasionally be cut off by a
            # provider despite its retries. Recover the paid batch by asking
            # for one small schema at a time before ever accepting a fallback.
            for scene in batch:
                try:
                    rewrite_batch([scene])
                except Exception as single_error:
                    warnings.append(f"AI rewrite unavailable for prompt {scene['id']}: {single_error}")
                    scene["prompt"] = f"{scene['id']}. {_fallback_visual_prompt(scene['narration'], plan.get('style') or '')}"
                    scene["reject_if"] = ["readable screen or invented text", "talking head", "extra limbs or duplicated props"]
                    scene["prompt_source"] = "fallback"
    return warnings


def build_external_plan(script: str, transcript: Iterable[Dict[str, Any]], style: str, prompt_client: Any = None) -> Dict[str, Any]:
    """Create numbered <=8-second prompts using audio-derived phrase timing."""
    phrases = _sentences(script)
    timing = [item for item in transcript if str(item.get("text") or "").strip()]
    if not phrases:
        raise ValueError("Añade un guion antes de crear los prompts.")
    if not timing:
        raise ValueError("No se encontraron frases en el audio.")
    # Whisper can collapse short or very clean narration into just one broad
    # segment.  The previous segment-to-sentence loop then ran past the end of
    # ``timing`` whenever the written script had several sentences.  Keep real
    # timings when there are enough of them; otherwise create proportional
    # phrase windows inside Whisper's known audio range.
    if len(timing) < len(phrases):
        source_start = float(timing[0]["start"])
        source_end = float(timing[-1]["end"])
        total_words = max(1, sum(max(1, len(phrase.split())) for phrase in phrases))
        cursor = source_start
        expanded_timing = []
        for index, phrase in enumerate(phrases):
            if index == len(phrases) - 1:
                end = source_end
            else:
                share = max(1, len(phrase.split())) / total_words
                end = min(source_end, cursor + (source_end - source_start) * share)
            expanded_timing.append({"start": cursor, "end": end, "text": phrase})
            cursor = end
        timing = expanded_timing
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
            scenes.append({
                "number": number, "id": f"{number:03d}", "start": round(part_start, 3),
                "end": round(end, 3), "duration": duration, "narration": part,
                "prompt": f"{number:03d}. {_fallback_visual_prompt(part, style)}",
                "reject_if": ["readable screen or invented text", "talking head", "extra limbs or duplicated props"],
                "prompt_source": "fallback",
                "expected_filename": f"{number:03d}.mp4",
            })
            number += 1
    plan = {
        "version": 1, "style": style.strip(), "duration": round(audio_end - audio_start, 3),
        "script": script, "scenes": scenes,
        "instructions": "Generate one clip per numbered prompt. Name clips 001.mp4, 002.mp4, etc. Clips may be up to 8 seconds; VYT trims them to the planned timing.",
    }
    warnings = _rewrite_external_prompts(plan, prompt_client) if prompt_client else ["AI prompt rewrite was unavailable; safe local prompts were used."]
    plan["prompt_generation"] = {"mode": "ai" if prompt_client and not warnings else "fallback", "warnings": warnings}
    return plan


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
    gateway_key = os.environ.get("VYT_GATEWAY_KEY", "").strip()
    prompt_client = VercelGatewayClient(gateway_key, "google/gemini-2.5-flash", provider_order=["google", "vertex"]) if gateway_key else None
    plan = build_external_plan(script, transcript, args.style, prompt_client=prompt_client)
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
