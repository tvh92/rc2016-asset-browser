"""Read-only adapter for retained PS4 pipeline result reports."""
from __future__ import annotations
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Literal
import hashlib, json

ResultKind = Literal["static", "skinned", "unknown"]
PreviewStatus = Literal["missing", "stale", "invalid", "hash_verified"]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PREVIEW_BYTES = 64 * 1024 * 1024
ASSEMBLY_SCHEMA = "fusion-source-linked-assembly-cache-v1"

@dataclass(frozen=True)
class ResultRef:
    report_path: Path
    model_id: str
    source_path: str
    label: str
    root_path: Path
    report_kind: Literal["aggregate", "manifest", "assembly"]

@dataclass(frozen=True)
class ClipRef:
    name: str
    action: str | None
    classification: Literal["supported", "unsupported", "unknown"]
    source_path: str | None
    animset_path: str | None
    source_duration_seconds: float | None
    fps: float | None
    frames: int | None
    raw_sha256: str | None
    reason: str | None = None

@dataclass(frozen=True)
class ArtifactRef:
    path: Path | None
    declared_sha256: str | None
    actual_sha256: str | None
    hash_status: Literal["match", "mismatch", "missing", "not_declared", "unavailable"]

@dataclass(frozen=True)
class PreviewRef:
    path: Path | None
    declared_sha256: str | None
    purpose: str | None = None
    source_clip: str | None = None
    receipt_hashes_match: bool = True

@dataclass(frozen=True)
class PreviewVerification:
    status: PreviewStatus
    path: Path | None
    actual_sha256: str | None

@dataclass(frozen=True)
class ResultDetails:
    model_id: str
    source_path: str
    label: str
    status: str
    kind: ResultKind
    blend: ArtifactRef
    supported_clips: tuple[ClipRef, ...]
    unsupported_clips: tuple[ClipRef, ...]
    unknown_clips: tuple[ClipRef, ...]
    limitations: tuple[str, ...]
    previews: tuple[PreviewRef, ...]
    look_index: int | None
    import_lod: str | None
    source_variants: tuple[str, ...]
    validation: ArtifactRef | None = None
    summary: tuple[str, ...] = ()
    visual_review_performed: bool | None = None
    animation_status: Literal["available", "none", "unknown"] = "unknown"

def _json(path: Path | None) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError, AttributeError):
        return None

def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def _safe(value: Any, root: Path) -> Path | None:
    if isinstance(value, Path):
        p = value
    elif isinstance(value, str) and value.strip():
        p = Path(value)
    else:
        return None
    if not p.is_absolute():
        p = root / p
    try:
        p = p.resolve(strict=False)
        p.relative_to(root.resolve(strict=False))
        return p
    except (OSError, ValueError):
        return None

def _label(source: str, ident: str) -> str:
    leaf = source.replace("\\", "/").rsplit("/", 1)[-1]
    return leaf[:-6] if leaf.lower().endswith(".model") else (leaf or ident)

def _reports(root: Path):
    root = root.resolve()
    paths = [
        (root / "results.json", root, "aggregate"),
        (root / "reports" / "manifest.json", root, "manifest"),
        (root / "CorpusRegression" / "results.json", root / "CorpusRegression", "aggregate"),
        (root / "Research" / "CorpusRegression" / "results.json", root / "Research" / "CorpusRegression", "aggregate"),
    ]
    # CorpusRegression's known, shallow report buckets; excludes evidence_history.
    for bucket in ("runs", "lifecycle"):
        d = root / bucket
        if d.is_dir():
            paths.extend((p, p.parent.parent, "manifest") for p in sorted(d.glob("*/reports/manifest.json")))
    # Authoritative combined wearable assemblies: runs/<run>/variant_assemblies/<name>/assembly_receipt.json.
    runs = root / "runs"
    if runs.is_dir():
        paths.extend((p, p.parent.parent.parent, "assembly") for p in sorted(runs.glob("*/variant_assemblies/*/assembly_receipt.json")))
    seen = set()
    for p, base, kind in paths:
        p = p.resolve(strict=False)
        if p not in seen and p.is_file():
            seen.add(p)
            yield p, base.resolve(), kind

def discover_results(roots: Iterable[str | Path]) -> list[ResultRef]:
    found, seen = [], set()
    for value in roots:
        root = Path(value)
        if not root.is_dir():
            continue
        for report, base, kind in _reports(root):
            doc = _json(report)
            records = []
            if kind == "aggregate" and isinstance(doc, dict):
                for case in doc.get("cases", []):
                    if isinstance(case, dict):
                        actual = case.get("actual") or {}
                        records.append((actual.get("asset_id"), actual.get("source_model_path")))
            elif kind == "manifest" and isinstance(doc, dict):
                for ident, row in (doc.get("models") or {}).items():
                    if isinstance(row, dict):
                        records.append((ident, row.get("source_model_path") or row.get("source_path") or row.get("model")))
            elif kind == "assembly":
                # An assembly is listed under its V1 source identity, labelled as the combined saved scene.
                for ref in _assembly_refs(report, base.resolve()):
                    key = (report, ref.model_id, ref.source_path)
                    if key not in seen:
                        found.append(ref)
                        seen.add(key)
                continue
            for ident, source in records:
                if not isinstance(ident, str) or not ident or not isinstance(source, str) or not source:
                    continue
                key = (report, ident, source)
                if key not in seen:
                    found.append(ResultRef(report, ident, source, _label(source, ident), base, kind))
                    seen.add(key)
    return sorted(found, key=lambda x: (x.label.casefold(), x.model_id, x.source_path, str(x.report_path)))

def _assembly_refs(report: Path, root: Path) -> list[ResultRef]:
    """List a passed source-linked assembly under its single V1 source identity, or nothing."""
    doc = _json(report)
    if not isinstance(doc, dict) or doc.get("schema") != ASSEMBLY_SCHEMA or doc.get("status") != "passed":
        return []
    inputs = doc.get("inputs") if isinstance(doc.get("inputs"), dict) else {}
    config = inputs.get("config") if isinstance(inputs.get("config"), dict) else {}
    override = ((config.get("field_values") or {}).get("GloveModelOverride") or {}).get("value")
    if not isinstance(override, str):
        return []
    wanted = override.replace("\\", "/").casefold()
    matches = [m for m in inputs.get("models", []) if isinstance(m, dict)
               and isinstance(m.get("archive_path"), str) and m["archive_path"].casefold() == wanted]
    if len(matches) != 1 or not isinstance(matches[0].get("asset_id"), str):
        return []
    v1 = matches[0]
    label = _label(v1["archive_path"], v1["asset_id"]) + " (V1/V5 assembly)"
    return [ResultRef(report, v1["asset_id"], v1["archive_path"], label, root, "assembly")]

def _inspect_assembly(ref: ResultRef, verify_blend: bool) -> ResultDetails:
    """Describe a combined assembly. Its V1/V5 selectors come only from the optional Blender inspection."""
    doc = _json(ref.report_path)
    listed = any(r.model_id == ref.model_id and r.source_path == ref.source_path
                 for r in _assembly_refs(ref.report_path, ref.root_path))
    if not listed or not isinstance(doc, dict):
        empty = ArtifactRef(None, None, None, "unavailable")
        return ResultDetails(ref.model_id, ref.source_path, ref.label, "UNKNOWN", "unknown", empty,
                             (), (), (), ("Assembly receipt or source identity unavailable.",), (), None, None, ())
    blend = _artifact(doc.get("blend_path"), doc.get("blend_sha256"), ref.root_path, verify_blend)
    validation = _artifact(doc.get("validation_path"), doc.get("validation_sha256"), ref.root_path, verify_blend)
    validation_doc = _json(validation.path)
    audit = _json(ref.root_path / "assembly_audit.json")
    limits = []
    for source in (validation_doc, audit):
        if isinstance(source, dict):
            limits.extend(_strings(source.get("limitations")))
    limits.append("Blender-free browsing shows the source V1 identity; V1/V5 switching is the optional Blender inspection of this combined scene.")
    passed = isinstance(validation_doc, dict) and validation_doc.get("status") == "passed"
    review = audit.get("manual_visual_review_performed") if isinstance(audit, dict) else None
    source_animation = validation_doc.get("source_animation_status") if isinstance(validation_doc, dict) else None
    animation_status = "none" if isinstance(source_animation, str) and source_animation.startswith("No source animset") else "unknown"
    summary = ["Build result: " + ("assembly receipt passed" if passed else "assembly validation not passed"),
               f"Separate-process reopen: {validation_doc.get('status') if isinstance(validation_doc, dict) else 'not recorded'}"]
    if verify_blend:
        summary.append("Reopen report integrity: " + validation.hash_status)
    summary.append("Human visual review: performed" if review is True else "Human visual review: not performed" if review is False else "Human visual review: unknown")
    return ResultDetails(ref.model_id, ref.source_path, ref.label,
        "COMPLETE_ASSEMBLY_LIMITATION" if passed else "FAILED", "unknown", blend,
        (), (), (), tuple(dict.fromkeys(limits)), (), None, None, (), validation, tuple(summary),
        review if isinstance(review, bool) else None, animation_status)

def _row(ref: ResultRef, doc: Any) -> dict | None:
    if not isinstance(doc, dict):
        return None
    if ref.report_kind == "manifest":
        row = (doc.get("models") or {}).get(ref.model_id)
        if isinstance(row, dict) and (row.get("source_model_path") or row.get("source_path") or row.get("model")) == ref.source_path:
            return row
        return None
    for case in doc.get("cases", []):
        if isinstance(case, dict):
            actual = case.get("actual") or {}
            if actual.get("asset_id") == ref.model_id and actual.get("source_model_path") == ref.source_path:
                return case
    return None

def _artifact(value: Any, digest: Any, root: Path, verify: bool) -> ArtifactRef:
    path = _safe(value, root)
    expected = digest.lower() if isinstance(digest, str) and len(digest) == 64 else None
    if path is None:
        return ArtifactRef(None, expected, None, "missing" if value else "unavailable")
    if not path.is_file():
        return ArtifactRef(path, expected, None, "missing")
    if not verify:
        return ArtifactRef(path, expected, None, "unavailable" if expected else "not_declared")
    actual = _hash(path)
    state = "not_declared" if expected is None else ("match" if actual == expected else "mismatch")
    return ArtifactRef(path, expected, actual, state)

def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [x for x in value if isinstance(x, str) and x]
    return []

def _clip(row: dict, classification: str, reason: str | None = None) -> ClipRef:
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    def number(x, types):
        return x if isinstance(x, types) and not isinstance(x, bool) else None
    return ClipRef(
        str(row.get("name") or row.get("action") or "Unknown clip"),
        row.get("action") if isinstance(row.get("action"), str) else None,
        classification,
        row.get("raw_clip") if isinstance(row.get("raw_clip"), str) else None,
        row.get("animset_path") if isinstance(row.get("animset_path"), str) else None,
        number(row.get("source_duration_seconds", meta.get("duration_seconds")), (int, float)),
        number(row.get("fps", meta.get("sample_rate_fps")), (int, float)),
        number(row.get("frames", meta.get("original_frame_count")), (int,)),
        row.get("raw_sha256") if isinstance(row.get("raw_sha256"), str) else None, reason)

def inspect_result(ref: ResultRef, verify_blend: bool = True) -> ResultDetails:
    """Inspect a selected record. The large blend hash is opt-in."""
    if ref.report_kind == "assembly":
        return _inspect_assembly(ref, verify_blend)
    row = _row(ref, _json(ref.report_path))
    if row is None:
        empty = ArtifactRef(None, None, None, "unavailable")
        return ResultDetails(ref.model_id, ref.source_path, ref.label, "UNKNOWN", "unknown", empty,
                             (), (), (), ("Result record or source identity unavailable.",), (), None, None, ())
    if ref.report_kind == "manifest":
        status = str(row.get("status") or "UNKNOWN")
        blend = _artifact(row.get("final"), row.get("final_sha256"), ref.root_path, verify_blend)
        model_dir = _safe(row.get("final"), ref.root_path)
        model_dir = model_dir.parent if model_dir else None
        validation_path = _safe(row.get("validation"), ref.root_path)
        limits_path = _safe(row.get("limitations"), ref.root_path)
        previews_data = row.get("previews") or []
        joint_count = row.get("joint_count")
        kind = "skinned" if isinstance(joint_count, int) and joint_count > 0 else "static" if joint_count == 0 else "unknown"
        limitations = _json(limits_path)
        limit_values = _strings(row.get("unsupported"))
        for key in ("features_not_decoded", "unsupported_clips", "dependency_errors", "unresolved_references"):
            limit_values.extend(_strings(limitations.get(key)) if isinstance(limitations, dict) else [])
    else:
        actual = row.get("actual") or {}
        delivery = row.get("delivery") or {}
        blend_row = delivery.get("blend") or {}
        status = str(actual.get("status") or "UNKNOWN")
        blend = _artifact(blend_row.get("path"), blend_row.get("declared_sha256"), ref.root_path, verify_blend)
        model_dir = _safe(blend_row.get("path"), ref.root_path)
        model_dir = model_dir.parent if model_dir else None
        validation_path = _safe((delivery.get("reopen_validation") or {}).get("path"), ref.root_path)
        limits_path = model_dir / "reports" / "limitations.json" if model_dir else None
        previews_data = delivery.get("previews") or []
        limitations = None
        limit_values = [actual["reason"]] if isinstance(actual.get("reason"), str) and actual["reason"] else []
        kind = "unknown"
        # Join the nearby per-output manifest using exact source identity only.
        local_report = model_dir.parent / "reports" / "manifest.json" if model_dir else None
        local_doc = _json(local_report)
        local_row = (local_doc.get("models") or {}).get(ref.model_id) if isinstance(local_doc, dict) else None
        if isinstance(local_row, dict) and (local_row.get("model") or local_row.get("source_model_path")) == ref.source_path:
            joints = local_row.get("joint_count")
            kind = "skinned" if isinstance(joints, int) and joints > 0 else "static" if joints == 0 else "unknown"
            limit_values.extend(_strings(local_row.get("unsupported")))
    validation = _json(validation_path)
    clips_path = model_dir / "reports" / "clips.json" if model_dir else None
    clip_rows = _json(clips_path)
    if ref.report_kind == "aggregate":
        limitations = _json(limits_path)
    if isinstance(validation, dict) and validation.get("source_geometry_available") is False:
        limit_values.append("No source geometry is available in validation.")
    meshes = _json(model_dir / "meshes" / "manifest.json") if model_dir else None
    if isinstance(meshes, dict):
        li = meshes.get("import_look")
        look = li if isinstance(li, int) and not isinstance(li, bool) else None
        lod = meshes.get("import_lod") if isinstance(meshes.get("import_lod"), str) else None
        lods = sorted({m["lod"] for mesh in meshes.get("meshes", []) if isinstance(mesh, dict)
                       for m in mesh.get("memberships", []) if isinstance(m, dict) and isinstance(m.get("lod"), int)})
        variants = ()  # LOD membership is not a model variant identity.
    else:
        look, lod, variants = None, None, ()
    validation_clips = validation.get("clips", []) if isinstance(validation, dict) else []
    valid_names = {x.get("name") for x in validation_clips if isinstance(x, dict) and x.get("selection_passed") is True}
    supported, unknown, unsupported = [], [], []
    for c in clip_rows if isinstance(clip_rows, list) else []:
        if not isinstance(c, dict):
            continue
        if c.get("status") == "included" and isinstance(validation, dict) and c.get("name") in valid_names:
            supported.append(_clip(c, "supported"))
        else:
            unknown.append(_clip(c, "unknown", str(c.get("reason") or "No successful matching action validation recorded.")))
    unsupported_rows = limitations.get("unsupported_clips", []) if isinstance(limitations, dict) else []
    for item in unsupported_rows if isinstance(unsupported_rows, list) else []:
        if isinstance(item, dict):
            unsupported.append(_clip(item, "unsupported", str(item.get("reason") or "Reported unsupported by pipeline.")))
        elif isinstance(item, str):
            unsupported.append(ClipRef(item, None, "unsupported", None, None, None, None, None, None, "Reported unsupported by pipeline."))
    receipt_ok = blend.hash_status not in ("mismatch", "missing")
    previews = []
    for p in previews_data if isinstance(previews_data, list) else []:
        if isinstance(p, dict):
            previews.append(PreviewRef(_safe(p.get("path"), ref.root_path),
                p.get("sha256") or p.get("declared_sha256"),
                p.get("purpose") if isinstance(p.get("purpose"), str) else None,
                p.get("source_clip") if isinstance(p.get("source_clip"), str) else None, receipt_ok))
    if ref.report_kind == "manifest":
        validation_digest = row.get("validation_sha256")
        review = validation.get("visual_review_performed") if isinstance(validation, dict) else row.get("visual_review_performed")
        action_count = row.get("included_action_count")
        source_clip_count = row.get("source_clip_count")
    else:
        validation_row = (delivery.get("reopen_validation") or {})
        validation_digest = validation_row.get("declared_sha256") or validation_row.get("actual_sha256")
        review = validation.get("visual_review_performed") if isinstance(validation, dict) else None
        action_count = validation.get("source_action_count") if isinstance(validation, dict) else None
        source_clip_count = local_row.get("source_clip_count") if isinstance(local_row,dict) else None
    validation_artifact = _artifact(validation_path, validation_digest, ref.root_path, verify_blend)
    previews = [replace(p, receipt_hashes_match=receipt_ok and validation_artifact.hash_status == "match") for p in previews]
    validation_status = validation.get("status") if isinstance(validation, dict) else None

    if isinstance(limitations,dict):
        for key,title in [('dependency_errors','Missing dependency'),('animset_errors','Animation error'),('unresolved_references','Unresolved reference')]:
            rows=limitations.get(key,[])
            for item in rows[:12] if isinstance(rows,list) else []:
                if isinstance(item,dict):
                    path=item.get('path') or item.get('asset') or item.get('source') or ''
                    reason=item.get('reason') or item.get('error') or ''
                    if path or reason:limit_values.append(f'{title}: {path} — {reason}')
            if isinstance(rows,list) and len(rows)>12:limit_values.append(f'{len(rows)-12} further {title.lower()} records are in the artifact report.')
        library=(limitations.get('materials') or {}).get('library',{})
        for material in library.get('materials',[]) if isinstance(library,dict) else []:
            states=material.get('shader_family_status',[])
            name=material.get('name') or material.get('path') or 'Material'
            if 'supported_partial' in states:limit_values.append(f'{name}: partial shader support; full runtime shading remains unproven.')
            elif any('unsupported' in value for value in states):limit_values.append(f'{name}: source-colour fallback; shader behavior is unsupported.')
        for item in library.get('errors',[]) if isinstance(library,dict) else []:
            if isinstance(item,str):limit_values.append('Material dependency: '+item)

    summary = [f"Build result: {status}"]
    summary.append(f"Separate-process reopen: {validation_status or 'not recorded'}")
    if verify_blend:
        summary.append("Reopen report integrity: "+validation_artifact.hash_status)
    summary.append("Human visual review: performed" if review is True else "Human visual review: pending" if review is False else "Human visual review: unknown")
    if isinstance(actual.get("reason"), str) if ref.report_kind == "aggregate" else False:
        summary.append(actual["reason"])
    animation_status = "available" if isinstance(action_count, int) and action_count > 0 else (
        "none" if isinstance(source_clip_count, int) and source_clip_count == 0 else "unknown")
    return ResultDetails(ref.model_id, ref.source_path, ref.label, status, kind, blend,
        tuple(supported), tuple(unsupported), tuple(unknown), tuple(dict.fromkeys(limit_values)),
        tuple(previews), look, lod, variants, validation_artifact, tuple(summary),
        review if isinstance(review, bool) else None, animation_status)

def verify_preview(ref: PreviewRef) -> PreviewVerification:
    if ref.path is None:
        return PreviewVerification("invalid", None, None)
    if not ref.path.is_file():
        return PreviewVerification("missing", ref.path, None)
    if not ref.receipt_hashes_match:
        return PreviewVerification("stale", ref.path, None)
    if not isinstance(ref.declared_sha256, str) or len(ref.declared_sha256) != 64:
        return PreviewVerification("invalid", ref.path, None)
    try:
        size = ref.path.stat().st_size
        if size < len(PNG_SIGNATURE) or size > MAX_PREVIEW_BYTES:
            return PreviewVerification("invalid", ref.path, None)
        with ref.path.open("rb") as f:
            if f.read(len(PNG_SIGNATURE)) != PNG_SIGNATURE:
                return PreviewVerification("invalid", ref.path, None)
        actual = _hash(ref.path)
    except OSError:
        return PreviewVerification("missing", ref.path, None)
    if actual != ref.declared_sha256.lower():
        return PreviewVerification("stale", ref.path, actual)
    return PreviewVerification("hash_verified", ref.path, actual)







