"""Pure UI assertions and bounded polling over inspect_ui snapshots."""
from __future__ import annotations

import math
import time
from typing import Any

CONTROL_ROLES = {
    "AXButton", "AXCheckBox", "AXComboBox", "AXDisclosureTriangle", "AXIncrementor",
    "AXLink", "AXMenuButton", "AXMenuItem", "AXPopUpButton", "AXRadioButton",
    "AXSearchField", "AXSecureTextField", "AXSlider", "AXSwitch", "AXTab",
    "AXTextArea", "AXTextField", "AXToggleButton",
}


def _empty_evidence(basis: str = "") -> dict[str, Any]:
    return {
        "matched_node_ids": [],
        "matched_texts": [],
        "matches": [],
        "bounds": [],
        "enabled": None,
        "confidence": None,
        "basis": basis,
    }


def _query_reference(snapshot):
    if not isinstance(snapshot, dict):
        return None
    query = snapshot.get("query")
    if isinstance(query, dict):
        return query
    elements = snapshot.get("elements")
    return {
        "source": snapshot.get("source"),
        "window_id": snapshot.get("window_id"),
        "region": snapshot.get("region"),
        "complete": snapshot.get("complete"),
        "issues": snapshot.get("issues"),
        "element_count": len(elements) if isinstance(elements, list) else None,
    }


def _condition_args(*, text, text_absent, enabled, disabled, role, match, min_confidence):
    selected = [(name, value) for name, value in (
        ("text", text), ("text_absent", text_absent),
        ("enabled", enabled), ("disabled", disabled),
    ) if value is not None]
    if len(selected) != 1 or not isinstance(selected[0][1], str) or not selected[0][1].strip():
        raise ValueError("exactly one non-empty text, text_absent, enabled, or disabled condition is required")
    name, value = selected[0]
    if role is not None and (name not in {"enabled", "disabled"}
                             or not isinstance(role, str) or not role.strip()):
        raise ValueError("role is supported only as a non-empty exact role for enabled/disabled conditions")
    if match not in {"contains", "exact"}:
        raise ValueError("match must be 'contains' or 'exact'")
    try:
        confidence_is_finite = math.isfinite(min_confidence)
    except (OverflowError, TypeError):
        confidence_is_finite = False
    if (isinstance(min_confidence, bool) or not isinstance(min_confidence, (int, float))
            or not confidence_is_finite or not 0 <= min_confidence <= 1):
        raise ValueError("min_confidence must be a finite number between 0 and 1")
    condition = {name: value}
    if role is not None:
        condition["role"] = role
    if name in {"text", "text_absent"}:
        condition["match"] = match
        condition["min_confidence"] = float(min_confidence)
    return name, value, condition


def _result(status: str, *, source=None, window_id=None, condition=None, evidence=None,
            query=None, artifacts=None, error=None, reason=None) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "operation": "assert",
        "status": status,
        "source": source,
        "window_id": window_id,
        "condition": condition or {},
        "evidence": evidence or _empty_evidence(),
        "query": query,
        "artifacts": artifacts if isinstance(artifacts, dict) else {},
    }
    if error is not None:
        payload["error"] = {"code": error[0], "message": error[1]}
    if reason is not None:
        payload["reason"] = reason
    return payload


def _error(code: str, message: str, *, snapshot=None, condition=None, evidence=None) -> dict[str, Any]:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    source = snapshot.get("source") if isinstance(snapshot.get("source"), str) else None
    window_id = snapshot.get("window_id")
    query = _query_reference(snapshot)
    artifacts = snapshot.get("artifacts")
    if evidence is None:
        basis = {
            "invalid_arguments": "condition was not evaluated",
            "invalid_subprocess_output": "inspection output was not trustworthy",
            "insufficient_evidence": "inspection evidence was insufficient",
            "selector_ambiguous": "multiple controls matched the selector",
            "source_unsupported": "the selected source cannot provide this evidence",
        }.get(code, "inspection failed before assertion evaluation")
        evidence = _empty_evidence(basis)
    return _result("error", source=source, window_id=window_id, condition=condition,
                   evidence=evidence, query=query, artifacts=artifacts,
                   error=(code, message))


def _validate_snapshot(snapshot: Any):
    if not isinstance(snapshot, dict):
        raise ValueError("inspection result must be an object")
    if snapshot.get("schema_version") != 1 or snapshot.get("operation") != "inspect":
        raise ValueError("inspection result has an unsupported schema or operation")
    if snapshot.get("status") == "error":
        error = snapshot.get("error")
        if (not isinstance(error, dict) or not isinstance(error.get("code"), str)
                or not error["code"] or not isinstance(error.get("message"), str)):
            raise ValueError("inspection result contains an invalid error object")
        return None, (error["code"], error["message"])
    if snapshot.get("status") != "pass":
        raise ValueError("inspection result status must be 'pass'")
    source = snapshot.get("source")
    if source not in {"accessibility", "ocr"}:
        raise ValueError("inspection result source must be 'accessibility' or 'ocr'")
    window_id = snapshot.get("window_id")
    if isinstance(window_id, bool) or not isinstance(window_id, int) or window_id <= 0:
        raise ValueError("inspection result has an invalid window_id")
    if not isinstance(snapshot.get("complete"), bool):
        raise ValueError("inspection result complete flag must be boolean")
    issues = snapshot.get("issues")
    if not isinstance(issues, list):
        raise ValueError("inspection result issues must be a list")
    elements = snapshot.get("elements")
    if not isinstance(elements, list):
        raise ValueError("inspection result elements must be a list")
    if "query" in snapshot and not isinstance(snapshot["query"], dict):
        raise ValueError("inspection result query must be an object")
    if "artifacts" in snapshot and not isinstance(snapshot["artifacts"], dict):
        raise ValueError("inspection result artifacts must be an object")
    region = snapshot.get("region")
    if region is not None:
        if not isinstance(region, dict) or not {"x", "y", "width", "height"}.issubset(region):
            raise ValueError("inspection result has invalid region bounds")
        region_values = [region[key] for key in ("x", "y", "width", "height")]
        if (any(isinstance(item, bool) or not isinstance(item, (int, float))
                or not math.isfinite(item) for item in region_values)
                or region["width"] <= 0 or region["height"] <= 0):
            raise ValueError("inspection result has invalid region bounds")

    clean_elements = []
    required = {"id", "role", "name", "value", "bounds", "enabled", "confidence"}
    for index, element in enumerate(elements):
        if not isinstance(element, dict) or not required.issubset(element):
            raise ValueError(f"inspection element {index} is missing required fields")
        node_id = element["id"]
        if (isinstance(node_id, bool) or not isinstance(node_id, (str, int))
                or (isinstance(node_id, str) and not node_id.strip())):
            raise ValueError(f"inspection element {index} has an invalid id")
        for field in ("role", "name"):
            if element[field] is not None and not isinstance(element[field], str):
                raise ValueError(f"inspection element {index} has an invalid {field}")
        value = element["value"]
        if value is not None and not isinstance(value, (str, bool, int, float)):
            raise ValueError(f"inspection element {index} has an invalid value")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"inspection element {index} has an invalid value")
        enabled_value = element["enabled"]
        if enabled_value is not None and not isinstance(enabled_value, bool):
            raise ValueError(f"inspection element {index} has an invalid enabled value")
        confidence = element["confidence"]
        if confidence is not None and (isinstance(confidence, bool)
                or not isinstance(confidence, (int, float)) or not math.isfinite(confidence)
                or not 0 <= confidence <= 1):
            raise ValueError(f"inspection element {index} has an invalid confidence")
        bounds = element["bounds"]
        if bounds is not None:
            if not isinstance(bounds, dict) or not {"x", "y", "width", "height"}.issubset(bounds):
                raise ValueError(f"inspection element {index} has invalid bounds")
            coords = [bounds[key] for key in ("x", "y", "width", "height")]
            if any(isinstance(item, bool) or not isinstance(item, (int, float))
                   or not math.isfinite(item) for item in coords) or bounds["width"] <= 0 or bounds["height"] <= 0:
                raise ValueError(f"inspection element {index} has invalid bounds")
        if source == "ocr" and bounds is None:
            raise ValueError(f"OCR inspection element {index} has no bounds")
        clean_elements.append(element)
    if snapshot["complete"] is not True or issues:
        return (source, window_id, clean_elements), ("insufficient_evidence",
                "inspection was incomplete or reported evidence limitations")
    return (source, window_id, clean_elements), None


def _meaningful_text(element):
    return [value for value in (element["name"], element["value"])
            if isinstance(value, str) and value.strip()]


def _matches(candidate: str, expected: str, match: str) -> bool:
    return candidate == expected if match == "exact" else expected in candidate


def _matched_evidence(matching, *, basis, enabled=None):
    node_ids = list(dict.fromkeys(element["id"] for element, _text in matching))
    bounds = []
    seen_bounds = set()
    for element, _text in matching:
        if element["bounds"] is not None and element["id"] not in seen_bounds:
            seen_bounds.add(element["id"])
            bounds.append({"node_id": element["id"], "bounds": element["bounds"]})
    return {
        "matched_node_ids": node_ids,
        "matched_texts": [actual for _element, actual in matching],
        "matches": [{"node_id": element["id"], "text": actual,
                     "enabled": element["enabled"], "confidence": element["confidence"],
                     "bounds": element["bounds"]}
                    for element, actual in matching],
        "bounds": bounds,
        "enabled": enabled,
        "confidence": max((element["confidence"] for element, _text in matching
                           if element["confidence"] is not None), default=None),
        "basis": basis,
    }


def evaluate(snapshot, *, text=None, text_absent=None, enabled=None, disabled=None,
             role=None, match="contains", min_confidence=0.8) -> dict:
    """Evaluate exactly one assertion against a completed inspect snapshot."""
    raw_condition = {key: value for key, value in (
        ("text", text), ("text_absent", text_absent), ("enabled", enabled),
        ("disabled", disabled), ("role", role),
    ) if value is not None}
    try:
        condition_name, expected, condition = _condition_args(
            text=text, text_absent=text_absent, enabled=enabled, disabled=disabled,
            role=role, match=match, min_confidence=min_confidence)
    except (OverflowError, TypeError, ValueError) as exc:
        return _error("invalid_arguments", str(exc), snapshot=snapshot, condition=raw_condition)

    try:
        validated, operational_error = _validate_snapshot(snapshot)
    except (OverflowError, TypeError, ValueError) as exc:
        return _error("invalid_subprocess_output", str(exc), snapshot=snapshot, condition=condition)
    if operational_error is not None:
        return _error(*operational_error, snapshot=snapshot, condition=condition)
    source, window_id, elements = validated
    evidence = _empty_evidence("accessibility_tree" if source == "accessibility" else "ocr_text")
    if source == "ocr" and condition_name in {"enabled", "disabled"}:
        return _error("source_unsupported", "control state assertions require accessibility inspection",
                      snapshot=snapshot, condition=condition, evidence=evidence)

    if condition_name in {"text", "text_absent"}:
        meaningful = [(element, value) for element in elements for value in _meaningful_text(element)]
        matching = [(element, value) for element, value in meaningful
                    if _matches(value, expected, match)]
        if source == "accessibility":
            if not meaningful:
                evidence["basis"] = "accessibility tree contained no meaningful text observations"
                return _error("insufficient_evidence", "accessibility query returned no meaningful text",
                              snapshot=snapshot, condition=condition, evidence=evidence)
            evidence = _matched_evidence(matching, basis="accessibility_tree_text_fields")
            if condition_name == "text":
                if matching:
                    status, reason = "pass", None
                else:
                    status, reason = "fail", "text_not_found"
            elif matching:
                status, reason = "fail", "text_present"
            else:
                status, reason = "pass", None
        else:
            evidence = _matched_evidence(matching, basis="ocr_recognized_text_with_confidence")
            confident = [(element, value) for element, value in matching
                         if element["confidence"] is not None
                         and element["confidence"] >= min_confidence]
            if condition_name == "text_absent":
                if confident:
                    evidence = _matched_evidence(confident,
                                                 basis="ocr_confident_match_proves_presence_only")
                    status, reason = "fail", "text_present"
                else:
                    evidence["basis"] = "OCR cannot prove text absence"
                    return _error("insufficient_evidence",
                                  "OCR cannot prove that text is absent",
                                  snapshot=snapshot, condition=condition, evidence=evidence)
            elif confident:
                evidence = _matched_evidence(confident,
                                             basis="ocr_text_match_at_or_above_min_confidence")
                status, reason = "pass", None
            elif matching:
                evidence["basis"] = "ocr_matching_text_below_min_confidence"
                return _error("insufficient_evidence",
                              "matching OCR text did not meet min_confidence",
                              snapshot=snapshot, condition=condition, evidence=evidence)
            elif not meaningful:
                evidence["basis"] = "OCR yielded no text observations"
                return _error("insufficient_evidence", "OCR query returned no text observations",
                              snapshot=snapshot, condition=condition, evidence=evidence)
            else:
                evidence["basis"] = "ocr_no_confident_text_match"
                status, reason = "fail", "text_not_found"
        return _result(status, source=source, window_id=window_id, condition=condition,
                       evidence=evidence, query=_query_reference(snapshot),
                       artifacts=snapshot.get("artifacts"), reason=reason)

    named = [element for element in elements
             if element["name"] == expected
             and (role is None or element["role"] == role)]
    controls = [element for element in named if element["role"] in CONTROL_ROLES]
    if named and not controls:
        evidence = _matched_evidence([(element, element["name"]) for element in named],
                                     basis="exact name matched only non-control or unsupported roles")
        return _error("insufficient_evidence", "matching element role cannot establish a control state",
                      snapshot=snapshot, condition=condition, evidence=evidence)
    if not controls:
        informative = any((element["name"] and element["name"].strip()) or
                          (element["role"] and element["role"].strip()) for element in elements)
        if not informative:
            return _error("insufficient_evidence", "accessibility query returned no informative controls",
                          snapshot=snapshot, condition=condition, evidence=evidence)
        evidence["basis"] = "accessibility_tree_exact_control_name"
        return _result("fail", source=source, window_id=window_id, condition=condition,
                       evidence=evidence, query=_query_reference(snapshot),
                       artifacts=snapshot.get("artifacts"), reason="control_not_found")
    if len(controls) > 1:
        matching = [(element, element["name"]) for element in controls]
        evidence = _matched_evidence(matching, basis="accessibility_tree_ambiguous_control_name")
        return _error("selector_ambiguous", "multiple controls match the exact name and role",
                      snapshot=snapshot, condition=condition, evidence=evidence)
    element = controls[0]
    matching = [(element, element["name"])]
    evidence = _matched_evidence(matching, basis="accessibility_tree_exact_control_name",
                                  enabled=element["enabled"])
    if element["enabled"] is None:
        return _error("insufficient_evidence", "control enabled state is unknown",
                      snapshot=snapshot, condition=condition, evidence=evidence)
    expected_enabled = condition_name == "enabled"
    status = "pass" if element["enabled"] is expected_enabled else "fail"
    return _result(status, source=source, window_id=window_id, condition=condition,
                   evidence=evidence, query=_query_reference(snapshot),
                   artifacts=snapshot.get("artifacts"),
                   reason=None if status == "pass" else ("control_disabled" if expected_enabled else "control_enabled"))


def _load_inspector():
    # Imported only for wait calls so evaluate remains a pure snapshot function.
    from ui_inspect import inspect_ui
    return inspect_ui


def _inspection_failure(exc):
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        details = {}
    if isinstance(code, str) and code and isinstance(message, str):
        return code, message, details
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return "dependency_missing", str(exc) or "UI inspection backend is unavailable", details
    return "subprocess_failed", str(exc) or "UI inspection failed", details


def wait_for_condition(window_id, *, timeout, interval, source="ax", region=None,
                       text=None, text_absent=None, enabled=None, disabled=None,
                       role=None, match="contains", min_confidence=0.8):
    """Poll inspect_ui until one assertion passes, errors, or the budget expires."""
    raw_condition = {key: value for key, value in (
        ("text", text), ("text_absent", text_absent), ("enabled", enabled),
        ("disabled", disabled), ("role", role),
    ) if value is not None}
    try:
        condition_name, _expected, condition = _condition_args(
            text=text, text_absent=text_absent, enabled=enabled, disabled=disabled,
            role=role, match=match, min_confidence=min_confidence)
        if isinstance(window_id, bool) or not isinstance(window_id, int) or window_id <= 0:
            raise ValueError("window_id must be a positive integer")
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("timeout must be a finite number greater than zero")
        if (isinstance(interval, bool) or not isinstance(interval, (int, float))
                or not math.isfinite(interval) or interval <= 0):
            raise ValueError("interval must be a finite number greater than zero")
        if source not in {"ax", "accessibility", "ocr"}:
            raise ValueError("source must be 'ax', 'accessibility', or 'ocr'")
    except (OverflowError, TypeError, ValueError) as exc:
        payload = _result("error", source=source if isinstance(source, str) else None,
                          window_id=window_id if isinstance(window_id, int) else None,
                          condition=raw_condition, evidence=_empty_evidence(),
                          error=("invalid_arguments", str(exc)))
        return payload, 2

    started = time.monotonic()
    deadline = started + float(timeout)
    samples = 0
    last_assertion = None
    last_source = None
    requested_source = "ax" if source == "accessibility" else source

    def result(status, *, reason=None, error=None, query_failure=None):
        elapsed = max(0.0, time.monotonic() - started)
        basis = None
        query = None
        artifacts = {}
        evidence = _empty_evidence()
        if last_assertion is not None:
            evidence = last_assertion.get("evidence", evidence)
            basis = evidence.get("basis") if isinstance(evidence, dict) else None
            query = last_assertion.get("query")
            artifacts = last_assertion.get("artifacts", {})
        payload = {
            "schema_version": 1,
            "operation": "wait",
            "status": status,
            "window_id": window_id,
            "source": last_source or ("accessibility" if requested_source == "ax" else requested_source),
            "condition": condition,
            "region": region,
            "samples": samples,
            "elapsed_seconds": round(elapsed, 3),
            "last_assertion": last_assertion,
            "evidence": evidence,
            "matched_basis": basis,
            "query": query,
            "artifacts": artifacts if isinstance(artifacts, dict) else {},
        }
        if reason is not None:
            payload["reason"] = reason
        if error is not None:
            payload["error"] = {"code": error[0], "message": error[1]}
        if query_failure is not None:
            payload["query_failure"] = query_failure
            details = query_failure.get("details", {})
            payload["failed_query_artifacts"] = (details.get("artifacts", {})
                                                  if isinstance(details, dict)
                                                  and isinstance(details.get("artifacts", {}), dict)
                                                  else {})
        return payload

    try:
        inspect_fn = _load_inspector()
    except Exception as exc:
        code, message, details = _inspection_failure(exc)
        failure = {"sample": samples, "error": {"code": code, "message": message},
                   "details": details}
        return result("error", error=(code, message), query_failure=failure), 2

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return result("fail", reason="timeout"), 1
        samples += 1
        try:
            snapshot = inspect_fn(window_id, source=requested_source,
                                  region=region, timeout=remaining)
        except Exception as exc:
            # A read timeout is an inspection error; only exhausted polling after
            # valid non-matches is an ordinary condition timeout.
            code, message, details = _inspection_failure(exc)
            failure = {"sample": samples, "error": {"code": code, "message": message},
                       "details": details}
            return result("error", error=(code, message), query_failure=failure), 2
        expected_snapshot_source = "ocr" if requested_source == "ocr" else "accessibility"
        if (isinstance(snapshot, dict) and snapshot.get("status") == "pass"
                and (snapshot.get("window_id") != window_id
                     or snapshot.get("source") != expected_snapshot_source)):
            last_assertion = _error("invalid_subprocess_output",
                                    "inspection result does not match the requested window and source",
                                    snapshot=snapshot, condition=condition)
        else:
            last_assertion = evaluate(snapshot, text=text, text_absent=text_absent,
                                      enabled=enabled, disabled=disabled, role=role,
                                      match=match, min_confidence=min_confidence)
        last_source = last_assertion.get("source") or last_source
        if last_assertion["status"] == "pass":
            return result("pass", reason="condition_met"), 0
        if last_assertion["status"] == "error":
            error = last_assertion.get("error", {})
            return result("error", error=(error.get("code", "invalid_subprocess_output"),
                                           error.get("message", "assertion evaluation failed"))), 2
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return result("fail", reason="timeout"), 1
        time.sleep(min(float(interval), remaining))
