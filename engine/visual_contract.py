"""Semantic visual contracts shared by planning, generation, QA and Resume.

The module deliberately exposes four operations only: build a contract from a
scene, validate it before spending, apply it to the scene/router, and compile
its observable requirements for prompts and review.  Provider adapters do not
need to understand individual object names or old phone-specific switches.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List


CONTRACT_SCHEMA_VERSION = "2026-09-09-v1"
QA_POLICY_VERSION = "2026-09-09-contract-evidence-v1"


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _lower(value: Any) -> str:
    return _text(value).lower()


def _hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _language(text: str) -> str:
    lowered = _lower(text)
    if re.search(r"[а-яё]", lowered):
        return "ru"
    if re.search(r"\b(?:el|la|los|las|teléfono|móvil|pantalla|muestra)\b", lowered):
        return "es"
    return "en"


# These words only identify candidates. The actual result is expressed as
# subject/object relations below; no caller makes decisions from these lists.
_SCREEN_OBJECTS = {
    "phone": r"\b(?:phone|smartphone|telephone|teléfono|telefono|móvil|movil|телефон|смартфон)\b",
    "laptop": r"\b(?:laptop|notebook computer|notebook|portable computer|ноутбук)\b",
    "tablet": r"\b(?:tablet|ipad|планшет)\b",
    "monitor": r"\b(?:monitor|computer screen|display|монитор|экран)\b",
}
_OBJECTS = {
    **_SCREEN_OBJECTS,
    "book": r"\b(?:book|document|paper|papers|книга|документ|бумага)\b",
    "tool": r"\b(?:tool|hammer|screwdriver|instrument|молоток|отв[её]ртка|инструмент)\b",
    "container": r"\b(?:cup|mug|bottle|glass|чашка|кружка|бутылка|стакан)\b",
}
_READING = re.compile(r"\b(?:read(?:s|ing)?|look(?:s|ing)?\s+(?:at|down at)|check(?:s|ing)?|view(?:s|ing)?|"
                      r"смотрит|читает|проверяет|изучает|lee|mira|revisa)\b", re.IGNORECASE)
_USING = re.compile(r"\b(?:use(?:s|ing)?|operate(?:s|ing)?|type(?:s|ing)?|write(?:s|ing)?|tap(?:s|ping)?|"
                    r"press(?:es|ing)?|browse(?:s|ing)?|turn(?:s|ing)?|использует|печатает|нажимает|вводит|поворачивает)\b", re.IGNORECASE)
_HOLDING = re.compile(r"\b(?:hold(?:s|ing)?|carry(?:s|ing)?|pick(?:s|ing)?\s+up|держит|бер[её]т|нес[её]т|sostiene|lleva)\b", re.IGNORECASE)
_CALLING = re.compile(r"\b(?:call(?:s|ing)?|on a call|speaks? on (?:the )?phone|talk(?:s|ing)? on (?:the )?phone|"
                     r"звонит|разговаривает по телефону)\b", re.IGNORECASE)
_CAMERA_ADDRESS = re.compile(r"\b(?:address(?:es|ing)? (?:the )?camera|speak(?:s|ing)? (?:directly )?to (?:the )?camera|"
                             r"talk(?:s|ing)? (?:directly )?to (?:the )?camera|looks? into (?:the )?camera|"
                             r"обращается к камере|смотрит в камеру|говорит в камеру)\b", re.IGNORECASE)
_SHOW_VIEWER = re.compile(r"\b(?:show(?:s|ing)?|display(?:s|ing)?|present(?:s|ing)?)\b.{0,48}"
                          r"\b(?:viewer|audience|camera|зрител[юяе]|камер[еуы])\b", re.IGNORECASE)
_SHOW_OTHER = re.compile(r"\b(?:show(?:s|ing)?|display(?:s|ing)?|present(?:s|ing)?)\b.{0,48}"
                         r"\b(?:another person|someone else|other person|friend|them|другому|человеку|кому-то)\b", re.IGNORECASE)
_EXPLICIT_SPEAKER = re.compile(r"\b(?:interview|witness testimony|named speaker|expert explains|"
                               r"интервью|свидетель рассказывает|эксперт объясняет)\b", re.IGNORECASE)
_EXACT_SCREEN_TEXT = re.compile(r"\b(?:show(?:s|ing)?|display(?:s|ing)?|read(?:s|ing)?)\b.{0,60}"
                                  r"(?:['\"]([^'\"]{1,48})['\"]|\b(SOS)\b)", re.IGNORECASE)


def _entities(text: str) -> List[Dict[str, Any]]:
    found = [{"id": "actor", "role": "actor", "kind": "person", "min": 1, "max": 1, "continuity_id": ""}]
    for object_id, pattern in _OBJECTS.items():
        if re.search(pattern, text, re.IGNORECASE):
            found.append({
                "id": object_id,
                "role": "object",
                "kind": "screen_device" if object_id in _SCREEN_OBJECTS else "object",
                "min": 1,
                "max": 1,
                "continuity_id": object_id if object_id in _SCREEN_OBJECTS else "",
            })
    return found


def _required(identifier: str, relation: str, observable: str, severity: str = "critical", **extra: Any) -> Dict[str, Any]:
    return {
        "id": identifier,
        "relation": relation,
        "observable": observable,
        "severity": severity,
        **extra,
    }


@dataclass
class VisualSceneContract:
    identity: Dict[str, Any]
    narration: Dict[str, Any]
    intent: Dict[str, Any]
    entities: List[Dict[str, Any]]
    relations: List[Dict[str, Any]]
    camera: Dict[str, Any]
    temporal: Dict[str, Any]
    constraints: Dict[str, List[Dict[str, Any]]]
    observability: List[Dict[str, Any]]
    realization: Dict[str, Any]
    risk: Dict[str, Any]
    style: Dict[str, Any]
    continuity_references: List[str] = field(default_factory=list)
    repair_history: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_scene(cls, scene: Dict[str, Any], job_id: str = "", language: str = "") -> "VisualSceneContract":
        narration = _text(scene.get("narration"))
        depicted = _text(" ".join(str(scene.get(key) or "") for key in ("literal_subject", "image_prompt", "video_prompt")))
        evidence_text = _text(f"{narration} {depicted}")
        entities = _entities(evidence_text)
        screen_entities = [entity for entity in entities if entity["kind"] == "screen_device"]
        object_entity = screen_entities[0] if screen_entities else next((entity for entity in entities if entity["role"] == "object"), None)
        has_reading = bool(_READING.search(evidence_text) or _USING.search(evidence_text))
        has_holding = bool(_HOLDING.search(evidence_text))
        has_call = bool(_CALLING.search(evidence_text))
        addresses_camera = bool(_CAMERA_ADDRESS.search(evidence_text))
        shows_viewer = bool(_SHOW_VIEWER.search(evidence_text))
        shows_other = bool(_SHOW_OTHER.search(evidence_text))
        relations: List[Dict[str, Any]] = []
        required: List[Dict[str, Any]] = []
        preferred: List[Dict[str, Any]] = []
        forbidden: List[Dict[str, Any]] = [
            _required("no_extra_limb", "anatomy->has_extra_limb->none", "all visible limbs connect naturally", "critical"),
            _required("no_duplicate_entity", "entity->duplicates->none", "count of visible required entities", "critical"),
            _required("no_generated_talking_head", "camera->contains->generic_talking_head", "person is not speaking directly to camera", "critical"),
        ]
        explicit_evidence = []

        if object_entity and (has_reading or has_holding or has_call or shows_viewer or shows_other):
            relations.append({"subject": "actor", "predicate": "uses", "object": object_entity["id"]})
            required.append(_required("one_required_object", "actor->uses->object", "one clear object and one user"))
        if object_entity and has_holding:
            relations.append({"subject": "hand", "predicate": "holds", "object": object_entity["id"]})
            required.append(_required("natural_grip", "hand->holds->object", "one uncomplicated connected grip"))
        # An explicit observing/using action remains meaningful even if the
        # person is also on a call (for example, checking a laptop while
        # speaking).  Only direct address to camera legitimately replaces the
        # device-gaze relation.
        if object_entity and has_reading and not addresses_camera:
            relations.append({"subject": "actor", "predicate": "looks_at", "object": object_entity["id"]})
            required.append(_required("actor_looks_at_device", "actor->looks_at->device", "visible gaze and body oriented to device"))
            if object_entity["kind"] == "screen_device":
                relations.append({"subject": "display_surface", "predicate": "faces", "object": "actor"})
                required.append(_required("display_faces_actor", "display_surface->faces->actor", "surface is usable by actor"))
                preferred.append(_required("natural_screen_visibility", "camera->sees->display_surface", "only a natural oblique view when visible", "preferred"))
        if object_entity and has_call:
            relations.append({"subject": object_entity["id"], "predicate": "near", "object": "actor_ear"})
            required.append(_required("call_position", "device->near->actor_ear", "device is held naturally for a call"))
        if addresses_camera:
            relations.append({"subject": "actor", "predicate": "looks_at", "object": "camera"})
            required.append(_required("actor_looks_at_camera", "actor->looks_at->camera", "direct address is explicitly narrated"))
        if object_entity and shows_viewer:
            relations.append({"subject": "display_surface", "predicate": "faces", "object": "camera"})
            required.append(_required(
                "display_faces_camera", "display_surface->faces->camera", "screen is deliberately shown to viewer",
                allowed_by_explicit_demonstration=True,
            ))
            explicit_evidence.append("explicit_screen_demonstration_to_viewer")
        elif object_entity and shows_other:
            relations.append({"subject": "display_surface", "predicate": "faces", "object": "recipient"})
            required.append(_required("display_faces_recipient", "display_surface->faces->recipient", "screen faces the named recipient"))
            explicit_evidence.append("explicit_screen_demonstration_to_recipient")

        exact_match = _EXACT_SCREEN_TEXT.search(evidence_text)
        if exact_match and shows_viewer:
            exact_text = next((group for group in exact_match.groups() if group), "")
            if exact_text:
                required.append(_required("exact_screen_text", "display_surface->shows->text", "readable local composition", text=exact_text))
                explicit_evidence.append(f"exact_text:{exact_text}")

        # A mere received message (including SOS) is an event, not proof that a
        # device must be held toward the lens or that generated text is needed.
        received_message = bool(re.search(r"\b(?:received|got|gets|получил[аи]?|получает)\b.{0,48}\b(?:message|sms|text|сообщени[ея])\b", evidence_text, re.IGNORECASE))
        if received_message:
            required.append(_required("message_event", "actor->receives->message", "ordinary reaction or notification context", "preferred"))
            explicit_evidence.append("message_event_without_screen_demonstration")

        action = "observe" if has_reading else "call" if has_call else "show" if (shows_viewer or shows_other) else "hold" if has_holding else "context"
        interaction_complexity = sum(bool(value) for value in (has_reading, has_holding, has_call, shows_viewer, shows_other))
        exact_text_required = any(item["id"] == "exact_screen_text" for item in required)
        high_risk = interaction_complexity > 1 or exact_text_required or len(entities) > 2
        requested_type = str(scene.get("requested_type") or scene.get("type") or "image")
        if exact_text_required:
            route = "local_screen_composite"
        elif high_risk:
            route = "stable_image"
        elif requested_type == "video":
            route = "veo_video"
        elif requested_type == "avatar":
            route = "source_presenter"
        else:
            route = "stable_image"
        return cls(
            identity={"job_id": str(job_id or scene.get("job_id") or ""), "scene_id": str(scene.get("id") or ""), "contract_schema_version": CONTRACT_SCHEMA_VERSION},
            narration={
                "start": float(scene.get("start") or 0), "end": float(scene.get("end") or 0),
                "exact_text": narration, "language": language or _language(narration), "explicit_evidence": explicit_evidence,
            },
            intent={"action": action, "meaning": narration, "viewer_facts": [item["id"] for item in required]},
            entities=entities,
            relations=relations,
            camera={"recommended": "over_the_shoulder_or_three_quarter" if has_reading else "ordinary_observer", "allowed_occlusions": ["natural_hand_occlusion"], "screen_visibility": "explicit_demo" if shows_viewer else "natural_only"},
            temporal={"initial_state": "single stable setup", "action": action, "final_state": "same entities and orientation", "must_remain": ["entity_count", "object_identity"]},
            constraints={"required": required, "preferred": preferred, "forbidden": forbidden},
            observability=[{"constraint_id": item["id"], "evidence": item["observable"]} for item in required],
            realization={"route": route, "alternatives": ["stable_image", "source_presenter_editorial_only"], "requested_type": requested_type},
            risk={"interaction_complexity": interaction_complexity, "exact_text": exact_text_required, "identity": requested_type == "avatar", "temporal": requested_type == "video", "level": "high" if high_risk else "normal"},
            style={"profile": "consumer_camera_documentary", "continuity_ids": list(scene.get("continuity_ids") or [])},
            continuity_references=list(scene.get("continuity_ids") or []),
            repair_history=list(scene.get("repair_history") or []),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identity": self.identity, "narration": self.narration, "intent": self.intent,
            "entities": self.entities, "relations": self.relations, "camera": self.camera,
            "temporal": self.temporal, "constraints": self.constraints,
            "observability": self.observability, "realization": self.realization,
            "risk": self.risk, "style": self.style,
            "continuity_references": self.continuity_references, "repair_history": self.repair_history,
        }

    @property
    def semantic_hash(self) -> str:
        payload = self.to_dict()
        payload.pop("repair_history", None)
        payload["realization"] = {"route": payload["realization"].get("route"), "requested_type": payload["realization"].get("requested_type")}
        return _hash(payload)


def validate_contract(contract: VisualSceneContract) -> List[str]:
    data = contract.to_dict()
    errors: List[str] = []
    for key in ("identity", "narration", "intent", "entities", "relations", "camera", "temporal", "constraints", "observability", "realization", "risk", "style"):
        if key not in data:
            errors.append(f"missing:{key}")
    identity = data.get("identity", {})
    if identity.get("contract_schema_version") != CONTRACT_SCHEMA_VERSION:
        errors.append("unsupported_contract_schema")
    required = data.get("constraints", {}).get("required", [])
    ids = {item.get("id") for item in required if isinstance(item, dict)}
    direct_camera = next((item for item in required if item.get("id") == "display_faces_camera"), {})
    if "display_faces_actor" in ids and "display_faces_camera" in ids and not direct_camera.get("allowed_by_explicit_demonstration"):
        errors.append("contradictory:display_faces_actor_and_camera")
    if "actor_looks_at_device" in ids and "actor_looks_at_camera" in ids:
        errors.append("contradictory:actor_gaze_device_and_camera")
    if "exact_screen_text" in ids and data.get("realization", {}).get("route") != "local_screen_composite":
        errors.append("exact_text_requires_local_screen_composite")
    if not data.get("narration", {}).get("exact_text"):
        errors.append("missing_exact_narration")
    return errors


def prompt_requirements(contract: Dict[str, Any]) -> str:
    """Compile only observable contract requirements, never a generic word list."""
    required = (contract or {}).get("constraints", {}).get("required", [])
    forbidden = (contract or {}).get("constraints", {}).get("forbidden", [])
    lines = []
    for item in required:
        if not isinstance(item, dict):
            continue
        identifier = item.get("id")
        if identifier == "actor_looks_at_device":
            lines.append("The person's gaze, hands and body are oriented toward the device; the person's gaze and body are directed at the device while using it.")
        elif identifier == "display_faces_actor":
            lines.append("The display faces the person and is usable by the actor; do not pose it screen-first to the camera.")
        elif identifier == "display_faces_camera":
            lines.append("The screen is deliberately presented to the viewer because the narration explicitly asks for that.")
        elif identifier == "display_faces_recipient":
            lines.append("The screen faces the named recipient, not automatically the camera.")
        elif identifier == "actor_looks_at_camera":
            lines.append("Direct gaze to camera is required by the narration.")
        elif identifier == "natural_grip":
            lines.append("Show one simple, physically connected hand grip.")
        elif identifier == "one_required_object":
            lines.append("Show only the one required object and user.")
        elif identifier == "exact_screen_text":
            lines.append("Do not synthesize readable interface text; this scene requires a local screen composition.")
    for item in forbidden:
        if isinstance(item, dict) and item.get("id") == "no_extra_limb":
            lines.append("Reject extra, fused or detached limbs; natural cropping is allowed.")
    return " ".join(lines)


def apply_visual_contract(scene: Dict[str, Any], job_id: str = "", is_opening: bool = False) -> Dict[str, Any]:
    """Attach and enforce the single scene contract before any paid submit."""
    contract = VisualSceneContract.from_scene(scene, job_id=job_id)
    errors = validate_contract(contract)
    data = contract.to_dict()
    scene["visual_contract"] = data
    scene["contract_schema_version"] = CONTRACT_SCHEMA_VERSION
    scene["contract_semantic_hash"] = contract.semantic_hash
    scene["qa_policy_version"] = QA_POLICY_VERSION
    scene["realization"] = dict(contract.realization)
    scene["contract_validation_errors"] = errors
    required_ids = {item["id"] for item in contract.constraints["required"]}
    current_type = str(scene.get("type") or "image")
    if not is_opening and current_type == "avatar" and "actor_looks_at_device" in required_ids:
        scene["requested_type"] = scene.get("requested_type", "avatar")
        scene["type"] = "image"
        scene["realization"]["route"] = "stable_image"
        scene["contract_fallback"] = "stable_interaction_not_source_avatar_slice"
    elif current_type != "avatar" and "actor_looks_at_camera" in required_ids and not _EXPLICIT_SPEAKER.search(_text(scene.get("narration"))):
        scene["requested_type"] = scene.get("requested_type", current_type)
        scene["type"] = "avatar"
        scene["contract_fallback"] = "source_presenter_not_generic_talking_head"
    elif contract.realization["route"] == "local_screen_composite":
        # A provider cannot reliably invent exact UI. Keep the scene in a
        # reviewable static route until the local compositor owns the surface.
        scene["type"] = "image"
        scene["realization"]["route"] = "local_screen_composite"
    reject_if = list(scene.get("reject_if") or [])
    reject_if.extend(item["id"] for item in contract.constraints["forbidden"] if item["id"] not in reject_if)
    scene["reject_if"] = reject_if
    return scene


def migrate_or_apply_contract(scene: Dict[str, Any], job_id: str = "", is_opening: bool = False) -> Dict[str, Any]:
    """Upgrade legacy checkpoints in place without deleting their paid media."""
    existing = scene.get("visual_contract")
    if isinstance(existing, dict) and existing.get("identity", {}).get("contract_schema_version") == CONTRACT_SCHEMA_VERSION:
        scene.setdefault("contract_semantic_hash", _hash(existing))
        scene.setdefault("qa_policy_version", QA_POLICY_VERSION)
        scene.setdefault("realization", existing.get("realization", {}))
        return scene
    return apply_visual_contract(scene, job_id=job_id, is_opening=is_opening)
