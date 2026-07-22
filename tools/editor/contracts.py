"""Versioned scene/take contract helpers for the local video workbench.

This module is intentionally stdlib-only.  It keeps migration and validation
separate from the HTTP handler so the same rules can be reused by CLI tools,
the bake preflight, and tests.
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


SCENE_UID_RE = re.compile(r"^scn_[A-Za-z0-9]{8,32}$")
TAKE_UID_RE = re.compile(r"^take_[A-Za-z0-9]{8,32}$")
ENGINES = {"remotion", "fable", "hyperframe", "media"}
STATUSES = {"planned", "generating", "draft", "approved"}
PROFILES = {"cutaway_h264", "overlay_alpha", "image_norm", "audio_norm", "original"}
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_uid(prefix: str) -> str:
    """Return a sortable ULID-shaped identifier with the requested prefix."""
    value = ((time.time_ns() // 1_000_000) << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return f"{prefix}_{''.join(reversed(chars))}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve_persisted_path(root: Path, project: Path, value: object) -> tuple[Path | None, bool]:
    """Resolve a persisted asset path and report whether it violates containment.

    The historical files use repository-relative ``videos/<project>/...`` paths,
    while a few local tools emitted project-relative ``work/...`` paths.  Both
    forms are accepted, but absolute paths, parent traversal, drive paths, and
    resolved paths outside the project (or the shared media library) are not.
    """
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return None, False
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return None, True
    pure = PurePosixPath(raw)
    if any(part == ".." for part in pure.parts):
        return None, True

    base = root if pure.parts and pure.parts[0] in {"videos", "media"} else project
    resolved = (base / Path(*pure.parts)).resolve(strict=False)
    project_root = project.resolve(strict=False)
    media_root = (root / "media").resolve(strict=False)
    if not (_is_within(resolved, project_root) or _is_within(resolved, media_root)):
        return None, True
    return resolved, False


def _legacy_take(asset: str, root: Path, project: Path, now: str) -> dict | None:
    path, outside = resolve_persisted_path(root, project, asset)
    take = {
        "take_uid": new_uid("take"),
        "file": asset.replace("\\", "/"),
        "created_at": now,
        "conform_profile": "original",
        "provenance": {"provider": "media", "note": "migrated from legacy asset"},
    }
    if not outside and path is not None and path.is_file():
        take["sha256"] = sha256_file(path)
    return take


def _version_to_take(version: dict, root: Path, project: Path, now: str) -> dict:
    take = copy.deepcopy(version)
    take_uid = str(take.pop("vid", "") or take.get("take_uid", ""))
    if not TAKE_UID_RE.fullmatch(take_uid):
        take_uid = new_uid("take")
    take["take_uid"] = take_uid
    if "file" not in take:
        take["file"] = str(take.pop("asset", "") or take.pop("path", ""))
    take.setdefault("created_at", now)
    take.setdefault("conform_profile", "original")
    provenance = take.get("provenance") if isinstance(take.get("provenance"), dict) else {}
    provenance.setdefault("provider", "media")
    provenance.setdefault("note", "migrated from interim versions[]")
    take["provenance"] = provenance
    if not take.get("sha256") and take.get("file"):
        path, outside = resolve_persisted_path(root, project, take["file"])
        if not outside and path and path.is_file():
            take["sha256"] = sha256_file(path)
    return take


def migrate_scene(raw: dict, catalog_ids: set[str], root: Path, project: Path) -> dict:
    """Lazily upgrade one legacy placement to the v2.1 shape."""
    scene = copy.deepcopy(raw)
    now = utc_now()
    legacy_id = str(scene.get("id", "")).strip()
    legacy_asset = str(scene.get("asset", "")).strip()

    engine = str(scene.get("engine", "")).strip().lower()
    if not engine:
        engine = "remotion" if legacy_id in catalog_ids or not legacy_asset else "media"
    scene["engine"] = engine

    scene_uid = str(scene.get("scene_uid", ""))
    if not SCENE_UID_RE.fullmatch(scene_uid):
        scene_uid = new_uid("scn")
    scene["scene_uid"] = scene_uid

    if engine == "remotion":
        scene["composition_id"] = str(scene.get("composition_id", "") or legacy_id).strip()
    else:
        scene.pop("composition_id", None)

    takes = scene.get("takes")
    if not isinstance(takes, list):
        versions = scene.pop("versions", None)
        if isinstance(versions, list):
            takes = [_version_to_take(item, root, project, now) for item in versions if isinstance(item, dict)]
        else:
            migrated = _legacy_take(legacy_asset, root, project, now) if legacy_asset else None
            takes = [migrated] if migrated else []
    else:
        takes = [copy.deepcopy(item) for item in takes if isinstance(item, dict)]
    scene["takes"] = takes

    take_ids = {str(take.get("take_uid", "")) for take in takes}
    active = scene.get("active_take_uid")
    if active not in take_ids:
        active = str(takes[-1].get("take_uid")) if legacy_asset and takes else None
    scene["active_take_uid"] = active

    status = str(scene.get("status", "")).lower()
    status = {"ready": "draft", "needs-change": "draft"}.get(status, status)
    if status not in STATUSES:
        status = "draft" if takes else "planned"
    scene["status"] = status
    scene.setdefault("fit", "hold")
    scene.setdefault("z", 0)
    scene.setdefault("transition_in", {"kind": "cut"})
    scene.setdefault("enabled", True)
    return derive_legacy_fields(scene)


def active_take(scene: dict) -> dict | None:
    active_uid = scene.get("active_take_uid")
    for take in scene.get("takes", []):
        if isinstance(take, dict) and take.get("take_uid") == active_uid:
            return take
    return None


def derive_legacy_fields(scene: dict) -> dict:
    """Bake compatibility: keep old readers on ``id`` and ``asset``."""
    if scene.get("engine") == "remotion":
        scene["id"] = str(scene.get("composition_id", ""))
    else:
        scene["id"] = str(scene.get("scene_uid", ""))
    take = active_take(scene)
    if take and take.get("file"):
        scene["asset"] = str(take["file"])
    else:
        scene.pop("asset", None)
    return scene


def migrate_timeline(payload: dict, catalog_ids: set[str], root: Path, project: Path) -> dict:
    timeline = copy.deepcopy(payload)
    shots = timeline.get("shots")
    if isinstance(shots, list):
        timeline["shots"] = [
            migrate_scene(scene, catalog_ids, root, project)
            for scene in shots
            if isinstance(scene, dict)
        ]
    return timeline


def issue(code: str, severity: str, message: str, scene_uid: str | None = None, data=None) -> dict:
    result = {"code": code, "severity": severity, "message": message}
    if scene_uid:
        result["scene_uid"] = scene_uid
    if data is not None:
        result["data"] = data
    return result


def validate_timeline(
    timeline: dict,
    catalog: dict[str, dict],
    root: Path,
    project: Path,
    duration: float | None = None,
) -> list[dict]:
    """Return stable, machine-readable validation issues."""
    issues: list[dict] = []
    shots = timeline.get("shots")
    if not isinstance(shots, list):
        return [issue("E_SCENE_SCHEMA", "E", "timeline.shots must be an array")]

    seen_uids: set[str] = set()
    enabled: list[dict] = []
    span_valid: set[str] = set()
    for index, scene in enumerate(shots):
        if not isinstance(scene, dict):
            issues.append(issue("E_SCENE_SCHEMA", "E", f"scene {index + 1} must be an object"))
            continue
        uid = str(scene.get("scene_uid", ""))
        if not SCENE_UID_RE.fullmatch(uid) or uid in seen_uids:
            issues.append(issue("E_SCENE_SCHEMA", "E", f"scene {index + 1} has an invalid or duplicate scene_uid", uid or None))
        seen_uids.add(uid)
        engine = str(scene.get("engine", ""))
        scene_type = str(scene.get("type", ""))
        if engine not in ENGINES or scene_type not in {"cutaway", "overlay"}:
            issues.append(issue("E_SCENE_SCHEMA", "E", f"scene {index + 1} has an unsupported engine or type", uid))

        try:
            start = float(scene.get("master_in_s"))
            end = float(scene.get("master_out_s"))
            if start < 0 or end <= start or (duration and end > duration + 0.001):
                raise ValueError
            span_valid.add(uid)
        except (TypeError, ValueError):
            issues.append(issue("E_SPAN_INVALID", "E", f"scene {index + 1} has an invalid master span", uid))

        if scene.get("enabled", True) is False:
            issues.append(issue("W_SCENE_DISABLED", "W", "scene is disabled and will be skipped", uid))
            continue
        enabled.append(scene)

        if engine == "remotion":
            composition_id = str(scene.get("composition_id", ""))
            if composition_id not in catalog:
                issues.append(issue("E_COMP_NOT_FOUND", "E", f"Remotion composition '{composition_id}' was not found", uid))
            take = active_take(scene)
            if take is None and composition_id:
                ext = ".mov" if scene_type == "overlay" else ".mp4"
                out_dir = (root / str(timeline.get("remotion_out", "remotion/out"))).resolve(strict=False)
                expected = (out_dir / f"{composition_id}{ext}").resolve(strict=False)
                if not _is_within(expected, root.resolve(strict=False)):
                    issues.append(issue("E_ASSET_OUTSIDE_PROJECT", "E", "Remotion output path escapes the repository", uid))
                elif not expected.is_file():
                    issues.append(issue(
                        "E_ASSET_MISSING", "E", "Remotion composition has not been rendered",
                        uid, {"file": expected.relative_to(root.resolve(strict=False)).as_posix()},
                    ))
        else:
            take = active_take(scene)
            if take is None:
                issues.append(issue("E_NO_ACTIVE_TAKE", "E", "enabled non-Remotion scene has no active take", uid))
                continue

        take = active_take(scene)
        if take is not None:
            file_value = take.get("file")
            path, outside = resolve_persisted_path(root, project, file_value)
            if outside:
                issues.append(issue("E_ASSET_OUTSIDE_PROJECT", "E", "active take path is outside the project", uid, {"file": file_value}))
            elif path is None or not path.is_file():
                issues.append(issue("E_ASSET_MISSING", "E", "active take file is missing", uid, {"file": file_value}))
            profile = take.get("conform_profile")
            if profile == "original":
                issues.append(issue("E_ASSET_UNCONFORMED", "E", "active take has not been conformed", uid))
            elif profile not in PROFILES:
                issues.append(issue("E_SCENE_SCHEMA", "E", "active take has an invalid conform profile", uid))
            probe = take.get("probe") if isinstance(take.get("probe"), dict) else {}
            if scene_type == "overlay" and (profile != "overlay_alpha" or probe.get("alpha") is False):
                issues.append(issue("E_PROFILE_MISMATCH", "E", "overlay take must preserve alpha", uid))
            if scene_type == "cutaway" and profile == "overlay_alpha" and probe.get("alpha") is True:
                issues.append(issue("E_PROFILE_MISMATCH", "E", "cutaway uses an alpha-only artifact", uid))
            try:
                slot = float(scene["master_out_s"]) - float(scene["master_in_s"])
                delta = abs(float(probe["dur_s"]) - slot)
                if scene.get("fit") == "error" and delta > 0.05:
                    issues.append(issue("E_DURATION_MISMATCH", "E", "take duration differs from its slot", uid, {"delta_s": round(delta, 3)}))
                elif scene.get("fit") != "error" and delta > 0.25:
                    issues.append(issue("W_DURATION_MISMATCH", "W", "take duration differs from its slot", uid, {"delta_s": round(delta, 3)}))
            except (KeyError, TypeError, ValueError):
                pass

    ordered = sorted(
        (item for item in enabled if str(item.get("scene_uid", "")) in span_valid),
        key=lambda item: (float(item["master_in_s"]), float(item["master_out_s"])),
    )
    fps = float(timeline.get("master_fps") or 30)
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            if float(right.get("master_in_s", 0)) >= float(left.get("master_out_s", 0)):
                break
            if left.get("type") != right.get("type"):
                continue
            overlap = float(left["master_out_s"]) - float(right["master_in_s"])
            transition = right.get("transition_in") if isinstance(right.get("transition_in"), dict) else {}
            claimed = float(transition.get("frames", 0) or 0) / fps if transition.get("kind") == "xfade" else 0
            if claimed <= 0 or abs(claimed - overlap) > 0.051:
                lane = left.get("type", "scene")
                issues.append(issue(
                    "E_OVERLAP_UNCLAIMED",
                    "E",
                    f"overlapping active {lane} scenes are not claimed by an exact xfade",
                    str(right.get("scene_uid", "")),
                    {"left_scene_uid": left.get("scene_uid"), "overlap_s": round(overlap, 3)},
                ))

    legacy_assets: dict[str, str] = {}
    for scene in enabled:
        legacy_id = str(scene.get("composition_id") if scene.get("engine") == "remotion" else scene.get("scene_uid"))
        take = active_take(scene)
        asset = str(take.get("file", "")) if take else ""
        if legacy_id in legacy_assets and legacy_assets[legacy_id] != asset:
            issues.append(issue("E_LEGACY_ID_COLLISION", "E", f"legacy id '{legacy_id}' resolves to different assets", str(scene.get("scene_uid", ""))))
        legacy_assets[legacy_id] = asset

    unique = []
    seen = set()
    for item in issues:
        key = (item.get("code"), item.get("scene_uid"), item.get("message"), repr(item.get("data")))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique
