"""Fail-closed resolution of source-authored model version links.

Inputs are decoded archive/index records. A link is accepted only when the
decoder supplies the field and value byte spans inside the pinned config
section; nearby strings and asset names are never sufficient evidence.
"""
from __future__ import annotations

import hashlib
import json
import sys
import struct
from pathlib import Path


FIELDS = {"DefaultSkinModels": "V1", "UpgradedSkinModels": "V5"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def assembly_receipt_fingerprint(inputs: dict, blend_sha256: str, validation_sha256: str) -> str:
    payload = {"schema": "fusion-source-linked-assembly-cache-v1", "inputs": inputs,
               "blend_sha256": blend_sha256, "validation_sha256": validation_sha256}
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def verify_assembly_receipt(receipt, expected_inputs: dict, blend_path, validation_path) -> dict:
    """Verify every cached assembly dependency and reopened delivery artifact."""
    if not isinstance(receipt, dict):
        return {"valid": False, "reason": "assembly receipt missing or malformed"}
    if receipt.get("schema") != "fusion-source-linked-assembly-cache-v1":
        return {"valid": False, "reason": "assembly receipt schema mismatch"}
    if receipt.get("status") != "passed":
        return {"valid": False, "reason": "assembly receipt status is not passed"}
    if receipt.get("inputs") != expected_inputs:
        return {"valid": False, "reason": "merge implementation, trace proof, config link, or model input changed"}
    blend_path, validation_path = Path(blend_path), Path(validation_path)
    if not blend_path.is_file() or not validation_path.is_file():
        return {"valid": False, "reason": "delivered blend or validation report is missing"}
    actual_blend = sha256(blend_path.read_bytes())
    actual_validation = sha256(validation_path.read_bytes())
    if receipt.get("blend_path") != str(blend_path.resolve()) or receipt.get("blend_sha256") != actual_blend:
        return {"valid": False, "reason": "delivered blend path/hash differs from receipt"}
    if receipt.get("validation_path") != str(validation_path.resolve()) or receipt.get("validation_sha256") != actual_validation:
        return {"valid": False, "reason": "validation report path/hash differs from receipt"}
    try:
        report = json.loads(validation_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"valid": False, "reason": f"validation report cannot be read: {exc}"}
    if report.get("status") != "passed":
        return {"valid": False, "reason": "reopened validation status is not passed"}
    if report.get("delivered_blend_sha256") != actual_blend or report.get("blend_sha256") != actual_blend:
        return {"valid": False, "reason": "reopened report does not match current delivered blend hash"}
    if Path(report.get("delivered_blend", "")).resolve() != blend_path.resolve():
        return {"valid": False, "reason": "reopened report points to a different delivered blend"}
    want = assembly_receipt_fingerprint(expected_inputs, actual_blend, actual_validation)
    if receipt.get("fingerprint") != want:
        return {"valid": False, "reason": "assembly receipt fingerprint mismatch"}
    return {"valid": True, "reason": "all source, implementation, output, and reopen hashes match", "fingerprint": want}


def discover_config_referrers(archive, selected_asset_ids, max_configs=2000) -> dict:
    """Find configs that directly reference selected indexed assets.

    The full config corpus is scanned to avoid candidate-name heuristics. This
    discovers only direct source edges; it does not infer actor activation.
    Returns source config bytes/hash so a caller can pass them to trace_config
    and resolve_variant_links.
    """
    selected = list(dict.fromkeys(selected_asset_ids))
    missing = [key for key in selected if key not in archive.entries]
    if missing:
        return {"status": "unsupported", "reason": "selected IDs absent from archive index", "missing_ids": missing, "configs": []}
    configs = [e for e in archive.entries.values() if e.get("type") == "config"]
    if len(configs) > max_configs:
        return {"status": "unsupported", "reason": "config scan bound exceeded", "config_count": len(configs), "configs": []}
    try:
        from rc2016.formats.index import references
    except ImportError:
        from rc2016.formats.index import references
    wanted = set(selected)
    found = {}
    for entry in configs:
        path = archive.decode(entry["id"])
        raw = path.read_bytes()
        digest = sha256(raw)
        for ref in references(raw):
            ids = archive.resolve(ref["path"])
            for target in wanted.intersection(ids):
                found.setdefault(entry["id"], {"id": entry["id"], "path": entry["path"],
                    "sha256": digest, "bytes": raw, "edges": []})["edges"].append({
                    "source_asset_id": entry["id"], "source_path": entry["path"],
                    "source_sha256": digest, "target_asset_id": target,
                    "target_path": archive.entries[target]["path"],
                    "byte_offset": ref["byte_offset"], "evidence": "null-terminated decoded source reference"})
    matches = list(found.values())
    status = "source_exact" if len(matches) == 1 else "ambiguous" if matches else "unsupported"
    return {"status": status, "reason": "exact indexed config-to-asset reference scan" if matches else
            "no indexed config contains an exact source reference to selected asset IDs",
            "scanned_config_count": len(configs), "configs": matches}


def resolve_fusion_wearable_pair(archive, selected_asset_id) -> dict:
    """Resolve the validated Fusion Grenade wearable pair from its weapon config.

    This narrow production adapter is hash-pinned to the proven config and
    model records. It activates only when the selected model is exactly the
    config's GloveModelOverride asset and both indexed source models match the
    byte-span trace. It makes no actor-selection or attachment-transform claim.
    """
    expected = {
        "config_id": "945a11a8211e07f98fdf",
        "config_sha256": "0e279bd053848baefd5fbe005d7a0d0d0d9453bb01737895ce4bc8e073776646",
        "v1_id": "09e11ce58f6ade64fd6e",
        "v1_sha256": "012952ab9cabab0457e8f0c279ed49f037b6a02d2588ba466fd6982b1308bb31",
        "v5_id": "e39cd2d0cd8d3632c0fa",
        "v5_sha256": "e94e5a59ab78aadf5d029a9919fcab82c8fd96c93b1e679ab26651a0167cbf28",
    }
    if selected_asset_id != expected["v1_id"]:
        return {"status": "not_applicable", "reason": "selected asset is not the source-linked V1 override model"}
    discovery = discover_config_referrers(archive, [selected_asset_id])
    configs = discovery.get("configs", [])
    if discovery.get("status") != "source_exact" or len(configs) != 1:
        return {"status": "unsupported", "reason": "selected model has no unique source-linked config", "discovery": discovery}
    cfg = configs[0]
    if cfg["id"] != expected["config_id"] or cfg["sha256"] != expected["config_sha256"]:
        return {"status": "unsupported", "reason": "config identity/hash differs from validated Fusion source", "discovery": discovery}
    traced = trace_config(cfg["bytes"], ["GloveModelOverride", "GloveUpgradeModel"])
    wanted = {"GloveModelOverride": expected["v1_id"], "GloveUpgradeModel": expected["v5_id"]}
    models = {}
    for field, asset_id in wanted.items():
        value = traced["fields"][field]["value"]
        resolved = archive.resolve(value)
        if resolved != [asset_id]:
            return {"status": "unsupported", "reason": f"{field} path does not resolve uniquely to its pinned asset ID"}
        path = archive.decode(asset_id)
        digest = sha256(path.read_bytes())
        if digest != expected["v1_sha256" if field == "GloveModelOverride" else "v5_sha256"]:
            return {"status": "unsupported", "reason": f"source hash mismatch for {field}"}
        models[field] = {"asset_id": asset_id, "path": archive.entries[asset_id]["path"],
                         "decoded_path": str(path), "sha256": digest,
                         "field_trace": traced["fields"][field]}
    return {"status": "source_exact", "config": {"asset_id": cfg["id"],
            "path": cfg["path"], "sha256": cfg["sha256"], "bytes": cfg["bytes"],
            "edges": cfg["edges"]}, "models": models,
            "limitations": ["Actor selection of this weapon config is unproven",
                            "Cross-version attachment transform is unproven"]}


def trace_config(data: bytes, watched_fields=None) -> dict:
    """Decode DAT1 config content with byte provenance, without editing ALERT.

    Records object-child name references, the referenced string-table payload,
    and inline string payload spans for the two skin fields.
    """
    from rc2016.formats.container import read_container
    alert_root = str(Path(__file__).resolve().parents[2] / "third_party" / "ALERT")
    added_path = alert_root not in sys.path
    if added_path:
        sys.path.insert(0, alert_root)
    try:
        from dat1lib.types.sections.config.serialized import ConfigContentSection
    finally:
        if added_path:
            sys.path.remove(alert_root)

    container = read_container(data, 0, {})
    table_start = container["table_end"]
    table_end = min(s["offset"] for s in container["sections"])
    section = next(s for s in container["sections"] if s["label_hash"] == "0xE501186F")
    section_start = section["offset"]
    section_bytes = data[section_start:section_start + section["size"]]

    class StringTable:
        version = 202300
        def get_string(self, offset):
            pos = int(offset)  # DAT1 serialized references are absolute container offsets
            if pos < table_start or pos >= table_end:
                return None
            end = data.find(b"\0", pos, table_end)
            return data[pos:end].decode("utf-8") if end >= 0 else None

    watched = set(FIELDS if watched_fields is None else watched_fields)
    trace, active = {}, {"field": None}
    class TracedConfigContentSection(ConfigContentSection):
        def _deserialize_object(self, stream):
            head_start = stream.tell()
            _zero, _unknown, count, data_len = struct.unpack("<IIII", stream.read(16))
            start = stream.tell()
            children = [struct.unpack("<IHBB", stream.read(8)) for _ in range(count)]
            ref_positions = [stream.tell() + i * 4 for i in range(count)]
            offsets = [struct.unpack("<I", stream.read(4))[0] for _ in range(count)]
            result = {}
            for i, child in enumerate(children):
                field = StringTable().get_string(offsets[i])
                previous = active["field"]
                active["field"] = field
                if field in watched:
                    rel = offsets[i]
                    nend = data.find(b"\0", rel, table_end)
                    trace[field] = {"field": field,
                        "field_reference_span": [section_start + ref_positions[i], section_start + ref_positions[i] + 4],
                        "field_name_span": [rel, nend],
                        "field_name_reference": offsets[i], "node_type": child[3]}
                items = child[1] >> 4
                result[field] = (self._deserialize_array(stream, child[3], items) if items != 1
                                 else self._deserialize_node(stream, child[3]))
                active["field"] = previous
            self._align(stream, 4)
            finish = stream.tell()
            stream.seek(head_start + 16 + data_len)
            if stream.tell() < finish:
                raise ValueError("DAT1 object exceeded declared size")
            return result

        def _deserialize_string(self, stream):
            start = stream.tell()
            value = super()._deserialize_string(stream)
            end = stream.tell()
            field = active["field"]
            if field in watched and "value_span" not in trace.get(field, {}):
                length = struct.unpack_from("<I", stream.getbuffer(), start)[0]
                trace[field]["value_span"] = [section_start + start + 16, section_start + start + 16 + length]
                trace[field]["encoded_value_span"] = [section_start + start, section_start + end]
            return value

    parsed = TracedConfigContentSection(section_bytes, StringTable()).root
    for name in watched:
        if name not in parsed or name not in trace:
            raise ValueError(f"DAT1 trace did not bind {name} to an inline string value")
        trace[name]["value"] = parsed[name]
    return {"root": parsed, "fields": trace, "section_span": [section_start, section_start + section["size"]],
            "section_label": section["label_hash"]}


def resolve_variant_links(config: dict, assets: list[dict]) -> dict:
    """Resolve links from {id,path,sha256,bytes,decoded_fields} and index rows.

    Each decoded_fields entry must include value, field_span and value_span as
    half-open offsets into ``bytes``. Index rows supply exact id/path/hash.
    """
    field_map = config.get("field_map", FIELDS)
    base = {"status": "unsupported", "confidence": "none", "links": [],
            "reason": "missing source-bound field/value provenance"}
    raw = config.get("bytes")
    if not isinstance(raw, bytes):
        return base
    if sha256(raw) != config.get("sha256"):
        return {**base, "status": "rejected", "reason": "config hash mismatch"}
    try:
        actual_trace = trace_config(raw, field_map.keys())["fields"]
    except Exception as exc:
        return {**base, "status": "rejected", "reason": f"DAT1 provenance trace failed: {exc}"}
    rows = {a.get("path", "").replace("\\", "/").lower(): a for a in assets}
    links = []
    fields = config.get("decoded_fields")
    if not isinstance(fields, dict):
        return base
    for field, version in field_map.items():
        item = fields.get(field)
        if not isinstance(item, dict):
            return base
        traced = actual_trace.get(field)
        if not traced or any(item.get(k) != traced.get(k) for k in
                ("value", "field_reference_span", "field_name_span", "field_name_reference", "value_span")):
            return {**base, "status": "rejected", "reason": f"supplied provenance differs from DAT1 trace: {field}"}
        value = item.get("value")
        fs, vs = item.get("field_span", item.get("field_reference_span")), item.get("value_span")
        if not (isinstance(value, str) and isinstance(fs, (list, tuple)) and
                isinstance(vs, (list, tuple)) and len(fs) == 2 and len(vs) == 2):
            return base
        f0, f1 = fs; v0, v1 = vs
        if not (0 <= f0 < f1 <= len(raw) and 0 <= v0 < v1 <= len(raw)):
            return {**base, "status": "rejected", "reason": "invalid decoder byte span"}
        # Spans and bytes must be directly witnessed by source bytes. Field-name
        # hashes alone do not count: the decoder must bind them to the value span.
        if raw[v0:v1].rstrip(b"\0").decode("utf-8", "replace") != value:
            return {**base, "status": "rejected", "reason": f"value span mismatch: {field}"}
        name_span = item.get("field_name_span")
        ref = item.get("field_name_reference")
        if not (isinstance(name_span, (list, tuple)) and len(name_span) == 2 and
                raw[name_span[0]:name_span[1]] == field.encode("utf-8") and
                isinstance(ref, int) and raw[f0:f1] == struct.pack("<I", ref) and
                item.get("field", item.get("field_name")) == field):
            return {**base, "status": "rejected", "reason": f"field span not decoder-bound: {field}"}
        row = rows.get(value.replace("\\", "/").lower())
        if not row or not row.get("id") or not row.get("sha256"):
            return {**base, "status": "unsupported", "reason": f"unresolved indexed asset: {value}"}
        links.append({"field": field, "version": field_map.get(field, version), "path": row["path"],
                      "asset_id": row["id"], "sha256": row["sha256"],
                      "field_span": list(fs), "value_span": list(vs)})
    return {"status": "source_exact", "confidence": "high", "config_id": config.get("id"),
            "config_path": config.get("path"), "config_sha256": config["sha256"],
            "links": links, "reason": "decoder-bound field/value spans and exact index IDs"}

def resolve_source_linked_skin_pair(archive, selected_asset_id: str, expected: dict) -> dict:
    """Resolve a hash-pinned DefaultSkinModels/UpgradedSkinModels pair.

    ``expected`` pins the config and both model IDs/hashes. The selected asset
    must be the V1/default model; this proves config-authored assignment only.
    """
    if selected_asset_id != expected["v1_id"]:
        return {"status": "not_applicable", "reason": "selected asset is not the pinned DefaultSkinModels model"}
    discovery = discover_config_referrers(archive, [selected_asset_id])
    rows = discovery.get("configs", [])
    if discovery.get("status") != "source_exact" or len(rows) != 1:
        return {"status": "unsupported", "reason": "no unique source config", "discovery": discovery}
    cfg = rows[0]
    if cfg["id"] != expected["config_id"] or cfg["sha256"] != expected["config_sha256"]:
        return {"status": "unsupported", "reason": "config id/hash mismatch"}
    try:
        trace = trace_config(cfg["bytes"], ["DefaultSkinModels", "UpgradedSkinModels"])
    except (KeyError, ValueError, TypeError) as exc:
        return {"status": "unsupported", "reason": f"skin-field trace failed: {exc}"}
    models = {}
    for field, version in (("DefaultSkinModels", "v1"), ("UpgradedSkinModels", "v5")):
        asset_id = expected[f"{version}_id"]
        row = trace["fields"].get(field)
        if not row:
            return {"status": "unsupported", "reason": f"missing traced {field}"}
        if archive.resolve(row["value"]) != [asset_id]:
            return {"status": "unsupported", "reason": f"{field} path is not a unique exact index match"}
        decoded = archive.decode(asset_id)
        digest = sha256(decoded.read_bytes())
        if digest != expected[f"{version}_sha256"]:
            return {"status": "unsupported", "reason": f"{field} model hash mismatch"}
        models[field] = {"asset_id": asset_id, "path": archive.entries[asset_id]["path"],
                         "sha256": digest, "decoded_path": str(decoded), "field_trace": row}
    return {"status": "source_exact", "config": {"asset_id": cfg["id"], "path": cfg["path"],
            "sha256": cfg["sha256"], "edges": cfg["edges"]}, "models": models,
            "limitations": ["Actor/runtime selection is unproven", "External attachment transform is unproven",
                            "Cross-version geometry composition is unproven"]}


def resolve_pyrocitor_barrel_pair(archive, selected_asset_id: str) -> dict:
    """Resolve the validated Pyrocitor pair through the shared skin-pair resolver."""
    expected = {"config_id": "90561d5198fcaa6e7c73",
        "config_sha256": "502ee8a5f3243580447c7d49545eed354394744aeba6469b7ef91fc649dbcaa1",
        "v1_id": "7699113e93128f485d10",
        "v1_sha256": "20c14e7d499263522446a6245e7acacb73b7f410f0a050fe51469020b8976d09",
        "v5_id": "86f736dced71288e7503",
        "v5_sha256": "4474f86943c3249e2fb9bec46b9184d62b5fa6f6c3f6c7f65f825ef66bccae0b"}
    return resolve_source_linked_skin_pair(archive, selected_asset_id, expected)


def resolve_proton_drum_pad_pair(archive, selected_asset_id: str) -> dict:
    """Resolve the hash-pinned Proton Drum pad pair; no Blender assembly claim."""
    expected = {"config_id": "21ce3bd4cd7d9e0d26b1",
        "config_sha256": "f03b8878b6b42fd13680020cfc2cf8f1951a48add2987498e6bfb00f3fac778c",
        "v1_id": "d70fd1cb02acc9650b6b",
        "v1_sha256": "79c7fd3bfebfc3371318a3c2b23c3ea28e7e0593acf1691de0a1ae0b6b0bdad4",
        "v5_id": "7a795a33a3d2c78edd33",
        "v5_sha256": "7717b689b765b0a2a44afe2ba14670d42dff19cad39d75b9c20a3eb744bc6cbb"}
    return resolve_source_linked_skin_pair(archive, selected_asset_id, expected)
