#!/usr/bin/env python3
"""Safely publish one verified VYT checkpoint under a stable identity.

This utility intentionally keeps both source checkpoints untouched.  It copies
only the compatible base asset library and records the second attempt as cost
and inert orphan metadata, so incompatible scene plans can never be mixed.
"""

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


COST_KEYS = ("video", "image", "analysis", "review")


def load(path):
    return json.loads(path.read_text())


def stable_identity(source, duration, version, branding, max_cost, qr_enabled):
    source = Path(source).resolve()
    stat = source.stat()
    return {
        "version": version,
        "source": str(source),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "duration": round(float(duration), 3),
        "branding": bool(branding),
        "max_cost_usd": round(float(max_cost), 4),
        "product_sale": {"enabled": bool(qr_enabled), "card": None},
    }


def checkpoint_key(identity):
    encoded = json.dumps(identity, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate(base, extra, base_assets, version):
    if base.get("version") != version or extra.get("version") != version:
        raise RuntimeError("La versión de los checkpoints no coincide con la app actual.")
    if base.get("transcript") != extra.get("transcript"):
        raise RuntimeError("Las transcripciones no son idénticas; no es seguro unir los intentos.")
    if base.get("beats") != extra.get("beats"):
        raise RuntimeError("Los tiempos narrativos no son idénticos; no es seguro unir los intentos.")
    if not base.get("planned_scenes") or not base.get("reviewed_scenes"):
        raise RuntimeError("El checkpoint base no contiene un plan completo.")
    missing = []
    completed = base.get("completed_assets") or {}
    for scene_id, record in completed.items():
        filename = str((record or {}).get("file") or "")
        path = base_assets / filename
        if not filename or not path.exists() or path.stat().st_size < 1000:
            missing.append(scene_id)
    if missing:
        raise RuntimeError(f"Faltan recursos pagados del checkpoint base: {', '.join(missing[:8])}")
    return len(completed)


def merge(base, extra, identity, base_stem, extra_stem):
    result = dict(base)
    base_cost = base.get("cumulative_cost_breakdown") or {}
    extra_cost = extra.get("cumulative_cost_breakdown") or {}
    result["cumulative_cost_breakdown"] = {
        key: round(float(base_cost.get(key) or 0) + float(extra_cost.get(key) or 0), 4)
        for key in COST_KEYS
    }
    result["checkpoint_identity"] = identity
    result["checkpoint_rescue"] = {
        "base": base_stem,
        "cost_only_attempt": extra_stem,
        "reason": "stable_qr_identity",
    }
    result["orphaned_paid_jobs"] = {
        "origin": extra_stem,
        "pending_video_jobs": dict(extra.get("pending_video_jobs") or {}),
        "pending_image_jobs": dict(extra.get("pending_image_jobs") or {}),
        "completed_asset_ids": sorted((extra.get("completed_assets") or {}).keys()),
        "cost_breakdown": {
            key: round(float(extra_cost.get(key) or 0), 4) for key in COST_KEYS
        },
        "note": "Inert audit record; incompatible plan was not merged.",
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--extra", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--max-cost", type=float, required=True)
    parser.add_argument("--branding", action="store_true")
    parser.add_argument("--qr-enabled", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    cache = Path(args.cache_dir).expanduser().resolve()
    base_json = cache / f"{args.base}.json"
    extra_json = cache / f"{args.extra}.json"
    base_assets = cache / f"{args.base}.assets"
    base = load(base_json)
    extra = load(extra_json)
    identity = stable_identity(
        args.source, args.duration, args.version, args.branding,
        args.max_cost, args.qr_enabled,
    )
    target_stem = checkpoint_key(identity)
    target_json = cache / f"{target_stem}.json"
    target_assets = cache / f"{target_stem}.assets"
    completed_count = validate(base, extra, base_assets, args.version)
    merged = merge(base, extra, identity, args.base, args.extra)
    total = sum(merged["cumulative_cost_breakdown"].values())

    print(json.dumps({
        "target": target_stem,
        "completed_assets": completed_count,
        "pending_video_jobs": len(merged.get("pending_video_jobs") or {}),
        "pending_image_jobs": len(merged.get("pending_image_jobs") or {}),
        "combined_cost_usd": round(total, 4),
        "apply": bool(args.apply),
    }, ensure_ascii=False))
    if not args.apply:
        return
    if target_json.exists():
        existing = load(target_json)
        if existing.get("checkpoint_rescue") == merged.get("checkpoint_rescue"):
            print("Checkpoint estable ya publicado; no se realizaron cambios.")
            return
        raise RuntimeError("Ya existe un checkpoint estable distinto; no se sobrescribió.")
    if target_assets.exists():
        raise RuntimeError("Ya existe una carpeta de recursos estable sin JSON; revísala antes de continuar.")

    temporary_root = Path(tempfile.mkdtemp(prefix=f".{target_stem}.rescue-", dir=cache))
    temporary_assets = temporary_root / "assets"
    temporary_json = temporary_root / "checkpoint.json"
    try:
        shutil.copytree(base_assets, temporary_assets)
        with temporary_json.open("w", encoding="utf-8") as handle:
            json.dump(merged, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_assets, target_assets)
        os.replace(temporary_json, target_json)
        published = load(target_json)
        if published.get("checkpoint_rescue") != merged.get("checkpoint_rescue"):
            raise RuntimeError("La validación posterior a la publicación falló.")
        print("Checkpoint estable publicado correctamente.")
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


if __name__ == "__main__":
    main()
