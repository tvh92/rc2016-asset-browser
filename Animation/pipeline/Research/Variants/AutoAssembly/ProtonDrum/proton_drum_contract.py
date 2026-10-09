"""Source-section, bind-subset, and receipt checks for Proton Drum assembly."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


VISIBLE = {
    "V1": {"main": {0, 1}, "v1": {0}, "v5": set()},
    "V5": {"main": {0, 1}, "v1": set(), "v5": {0}},
}
PAD_JOINT_NAMES = {
    "a_body", "LF_flap_base", "RT_flap_base", "CTR_flap_base",
    "LF_flap_tip", "RT_flap_tip", "CTR_flap_tip",
}
PROTON_RECEIPT_SCHEMA = "proton-drum-source-linked-assembly-v1"


def assert_visible_source_sections(state: str, actual: dict) -> None:
    if state not in VISIBLE:
        raise AssertionError(f"Unknown Proton Drum pad state: {state}")
    got = {key: set(actual.get(key, ())) for key in ("main", "v1", "v5")}
    if got != VISIBLE[state]:
        raise AssertionError(f"{state}: expected {VISIBLE[state]}, got {got}")


def assert_source_joint_subset(main_rows: list[dict], pad_rows: list[dict], tolerance: float = 1e-6) -> dict:
    """Require shared named joints to have identical source rest S/Q/T."""
    if not main_rows or not pad_rows or main_rows[0]["name"] != "a_body" or pad_rows[0]["name"] != "a_body":
        raise AssertionError("Both source rigs must have the a_body root")
    main = {row["name"]: row for row in main_rows}
    names = [row["name"] for row in pad_rows]
    if set(names) != PAD_JOINT_NAMES or len(names) != len(set(names)):
        raise AssertionError(f"Pad rig joint set differs from the pinned seven-joint source subset: {names}")
    mapping = []
    for pad_index, pad in enumerate(pad_rows):
        target = main.get(pad["name"])
        if not target:
            raise AssertionError(f"Pad joint absent from main rig: {pad['name']}")
        if (pad["parent"] >= 0) != (target["parent"] >= 0):
            raise AssertionError(f"Parent root relation differs for {pad['name']}")
        if pad["parent"] >= 0 and pad_rows[pad["parent"]]["name"] != main_rows[target["parent"]]["name"]:
            raise AssertionError(f"Parent identity differs for {pad['name']}")
        errors = {}
        for field in ("scale", "rotation_xyzw", "translation"):
            errors[field] = max(abs(float(a) - float(b)) for a, b in zip(pad[field], target[field]))
            if errors[field] > tolerance:
                raise AssertionError(f"Source rest {field} differs for {pad['name']}: {errors[field]}")
        mapping.append({"pad_index": pad_index, "main_index": main_rows.index(target),
                        "name": pad["name"], "max_component_error": max(errors.values())})
    root = main_rows[0]
    if (root["scale"] != [1.0, 1.0, 1.0] or root["translation"] != [0.0, 0.0, 0.0] or
            max(abs(float(x)) for x in root["rotation_xyzw"][:3]) > tolerance or
            abs(float(root["rotation_xyzw"][3]) - 1.0) > tolerance):
        raise AssertionError("Main source root is not identity in source-local coordinates")
    return mapping


def negative_completeness_control() -> bool:
    try:
        assert_visible_source_sections("V5", {"main": {0}, "v1": set(), "v5": {0}})
    except AssertionError:
        return True
    raise AssertionError("Incomplete main body/glass negative control passed")


def receipt_fingerprint(inputs: dict, blend_sha256: str, validation_sha256: str) -> str:
    payload = {"schema": PROTON_RECEIPT_SCHEMA, "inputs": inputs,
               "blend_sha256": blend_sha256, "validation_sha256": validation_sha256}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_receipt(receipt, expected_inputs: dict, blend_path, validation_path) -> dict:
    if not isinstance(receipt, dict) or receipt.get("schema") != PROTON_RECEIPT_SCHEMA:
        return {"valid": False, "reason": "Proton Drum assembly receipt missing or schema mismatch"}
    if receipt.get("status") != "passed" or receipt.get("inputs") != expected_inputs:
        return {"valid": False, "reason": "receipt status or source/implementation inputs differ"}
    blend_path, validation_path = Path(blend_path), Path(validation_path)
    if not blend_path.is_file() or not validation_path.is_file():
        return {"valid": False, "reason": "delivered blend or validation report is missing"}
    blend_hash = hashlib.sha256(blend_path.read_bytes()).hexdigest()
    validation_hash = hashlib.sha256(validation_path.read_bytes()).hexdigest()
    if receipt.get("blend_path") != str(blend_path.resolve()) or receipt.get("blend_sha256") != blend_hash:
        return {"valid": False, "reason": "delivered blend path/hash differs from receipt"}
    if receipt.get("validation_path") != str(validation_path.resolve()) or receipt.get("validation_sha256") != validation_hash:
        return {"valid": False, "reason": "validation report path/hash differs from receipt"}
    try:
        report = json.loads(validation_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"valid": False, "reason": f"validation report cannot be read: {exc}"}
    if (report.get("status") != "passed" or report.get("delivered_blend_sha256") != blend_hash or
            report.get("blend_sha256") != blend_hash or Path(report.get("delivered_blend", "")).resolve() != blend_path.resolve()):
        return {"valid": False, "reason": "reopened validation does not match the current delivered blend"}
    fingerprint = receipt_fingerprint(expected_inputs, blend_hash, validation_hash)
    if receipt.get("fingerprint") != fingerprint:
        return {"valid": False, "reason": "receipt fingerprint mismatch"}
    return {"valid": True, "reason": "source, implementation, delivery and reopen hashes match",
            "fingerprint": fingerprint}


def assert_clip_mode(clips: list[dict], experimental: bool) -> None:
    expected = {"Activate_wpn_Thumper", "Deactivate_wpn_Thumper"}
    counts = {name: sum(row.get("name") == name for row in clips) for name in expected}
    if any(count != 1 for count in counts.values()):
        raise AssertionError(f"Supported Proton Drum clip set is incomplete or duplicated: {counts}")
    ambiguous = {"Fire_wpn_Thumper", "Open_Idle_wpn_Thumper"}
    got = {row.get("name") for row in clips}
    wanted = expected | ambiguous
    if got != wanted:
        raise AssertionError(f"Experimental={experimental}: expected clip names {wanted}, got {got}")
    for row in clips:
        statuses = [status for _, status in row.get("actions", [])]
        if row["name"] in ambiguous:
            if experimental and (len(statuses) != 2 or set(statuses) != {"experimental_FULL", "experimental_SSC0"}):
                raise AssertionError(f"Ambiguous clip is not represented by labelled experimental candidates: {row}")
            if not experimental and statuses != ["default_SSC0"]:
                raise AssertionError(f"Inheritance-ambiguous clip lost its default SSC0 label: {row}")
        elif statuses != ["source_supported"]:
            raise AssertionError(f"Supported clip lost source-supported status: {row}")


def assert_unsupported_clip_mode(unsupported: list[dict], experimental: bool) -> None:
    names = {row.get("name") for row in unsupported}
    expected = set()  # inheritance-ambiguous clips are included (experimental pair or default SSC0)
    if names != expected or any(row.get("status") != "unsupported" for row in unsupported):
        raise AssertionError(f"Experimental={experimental}: unsupported source clips should be {expected}, got {unsupported}")
