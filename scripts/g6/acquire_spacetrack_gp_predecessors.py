#!/usr/bin/env python3
"""Offline-validated G6 Space-Track GP_HISTORY predecessor pilot client.

Network execution is unavailable unless a separate pilot-approval tag targets
the current clean tracked HEAD.  The default and currently authorized workflow
is ``--offline-validate``, which performs no authentication and no network I/O.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parents[2]

COVERAGE_FREEZE_COMMIT = "eac3c89f2dba9fca39af3558c2b56f4e3fce25b5"
MAIN_ACQUISITION_APPROVAL_TAG = "g6-main-acquisition-scaling-approved"
MAIN_ACQUISITION_APPROVAL_COMMIT = "5cbe644a8efcbe7fe32a223079f7c10d270d68c2"
PILOT_APPROVAL_TAG = "g6-predecessor-pilot-approved"

REQUEST_REGISTER = ROOT / "docs/research/G6_Predecessor_Request_Register.csv"
COVERAGE_AUDIT = ROOT / "results/g6/G6_Main_History_Coverage_Audit.csv"
COVERAGE_SUMMARY = ROOT / "results/g6/G6_Main_History_Coverage_Summary.json"
COVERAGE_AUDIT_SCRIPT = ROOT / "scripts/g6/audit_main_history_coverage.py"
MAIN_ACQUISITION_CLIENT = ROOT / "scripts/g6/acquire_spacetrack_gp_history.py"
OFFLINE_VALIDATION = ROOT / "results/g6/G6_Predecessor_Acquisition_Offline_Validation.json"

RAW_DIR = ROOT / "data/raw/g6"
PARTIAL_DIR = RAW_DIR / ".predecessor_partial"
PREDECESSOR_STATE_FILE = RAW_DIR / "G6_SpaceTrack_Predecessor_Acquisition_State.json"
MAIN_STATE_FILE = RAW_DIR / "G6_SpaceTrack_Acquisition_State.json"

EXPECTED_HASHES = {
    "predecessor_request_register": "fde612f6bea808babc5352002a2f91b86dc83e92f3a27a8e9210e2e296082a3b",
    "main_history_coverage_audit": "8458dd3d783a29a298c129688ea67dd031ff2a4fc56a7fb37df66d2f755fa3fb",
    "main_history_coverage_summary": "9ca84a635740fb6c2ebe73b72a6265d9a6efb6b11a11e826831095009832b620",
    "main_history_coverage_audit_script": "14e1577ce7933561243df739e8c81938ea7203605ef9fa7d9a67b0fe13e21f28",
    "main_history_acquisition_client": "09234c71fdce429d4b04931ce2cc73308be79689f84b08df434745fb751de441",
}
EXPECTED_REQUEST_SET_SHA256 = "976628343077b14cb7017493a08757ce93def34b5966f4b87688d6b106e46bee"
EXPECTED_REQUEST_COUNT = 6_347
EXPECTED_CAPTURED_COUNT = 7_507
EXPECTED_MAIN_WINDOW_EMPTY = 0

PILOT_MAX_REQUESTS = 3
TIMEOUT_SECONDS = 120.0
MIN_REQUEST_START_SPACING_SECONDS = 15.0
AUTOMATIC_RETRY = False

BASE_URL = "https://www.space-track.org"
LOGIN_URL = f"{BASE_URL}/ajaxauth/login"
CHUNK_BYTES = 1024 * 1024
STABLE_TIE_RULE = (
    "(CREATION_DATE, EPOCH, stable_tie_key); stable_tie_key=GP_ID when present, "
    "else canonical-record SHA-256"
)

REQUIRED_GP_FIELDS = {
    "NORAD_CAT_ID",
    "CREATION_DATE",
    "EPOCH",
    "GP_ID",
    "MEAN_MOTION",
    "ECCENTRICITY",
    "INCLINATION",
    "RA_OF_ASC_NODE",
    "ARG_OF_PERICENTER",
    "MEAN_ANOMALY",
    "EPHEMERIS_TYPE",
    "CLASSIFICATION_TYPE",
    "ELEMENT_SET_NO",
    "REV_AT_EPOCH",
    "BSTAR",
    "MEAN_MOTION_DOT",
    "MEAN_MOTION_DDOT",
    "OBJECT_ID",
    "REF_FRAME",
    "TIME_SYSTEM",
    "MEAN_ELEMENT_THEORY",
}


class AcquisitionError(RuntimeError):
    pass


class AuthorizationError(AcquisitionError):
    pass


class AuthenticationFailure(AcquisitionError):
    pass


class TransportFailure(AcquisitionError):
    pass


class SchemaFailure(AcquisitionError):
    def __init__(
        self,
        message: str,
        *,
        retrieval_utc: str | None = None,
        byte_count: int | None = None,
        sha256: str | None = None,
        raw_path: str | None = None,
        row_count: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retrieval_utc = retrieval_utc
        self.byte_count = byte_count
        self.sha256 = sha256
        self.raw_path = raw_path
        self.row_count = row_count


@dataclass(frozen=True)
class PredecessorSpec:
    request_sequence: int
    request_id: str
    object_id: str
    reference_tca_utc: datetime
    exact_scientific_cutoff_utc: datetime
    transport_cutoff_utc: datetime
    final_path: Path


def _parse_utc(value: str) -> datetime:
    text = value.strip()
    if not text:
        raise ValueError("empty UTC timestamp")
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _space_track_seconds(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _floor_second(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(microsecond=0)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_sha(path: Path, expected: str, label: str) -> None:
    try:
        actual = _sha256_file(path)
    except OSError as exc:
        raise AcquisitionError(f"cannot read frozen {label}: {path}") from exc
    if actual != expected:
        raise AcquisitionError(f"frozen {label} SHA-256 mismatch: {actual}")


def _git_rev_parse(name: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", name],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AcquisitionError(f"cannot resolve git reference {name}") from exc
    return completed.stdout.strip()


def _git_tag_target_if_exists(tag: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise AcquisitionError(f"cannot inspect git tag {tag}") from exc
    return completed.stdout.strip() if completed.returncode == 0 else None


def _verify_commit_ancestor(commit: str) -> None:
    try:
        completed = subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise AcquisitionError("cannot verify coverage-freeze ancestry") from exc
    if completed.returncode == 1:
        raise AcquisitionError(f"coverage-freeze commit {commit} is not an ancestor of HEAD")
    if completed.returncode != 0:
        raise AcquisitionError(
            "git merge-base failed: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )


def _verify_frozen_boundary() -> None:
    _verify_commit_ancestor(COVERAGE_FREEZE_COMMIT)
    tag_target = _git_rev_parse(f"refs/tags/{MAIN_ACQUISITION_APPROVAL_TAG}^{{commit}}")
    if tag_target != MAIN_ACQUISITION_APPROVAL_COMMIT:
        raise AcquisitionError(
            f"{MAIN_ACQUISITION_APPROVAL_TAG} resolves to {tag_target}, "
            f"expected {MAIN_ACQUISITION_APPROVAL_COMMIT}"
        )
    paths = {
        "predecessor_request_register": REQUEST_REGISTER,
        "main_history_coverage_audit": COVERAGE_AUDIT,
        "main_history_coverage_summary": COVERAGE_SUMMARY,
        "main_history_coverage_audit_script": COVERAGE_AUDIT_SCRIPT,
        "main_history_acquisition_client": MAIN_ACQUISITION_CLIENT,
    }
    for label, path in paths.items():
        _verify_sha(path, EXPECTED_HASHES[label], label)


def _read_csv(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        return tuple(reader.fieldnames or ()), list(reader)


def _load_coverage_summary() -> dict:
    with COVERAGE_SUMMARY.open("r", encoding="utf-8") as stream:
        summary = json.load(stream)
    required = {
        "integrity_status": "PASS",
        "objects_with_predecessor_captured_in_batch_union": EXPECTED_CAPTURED_COUNT,
        "objects_requiring_dedicated_predecessor_query": EXPECTED_REQUEST_COUNT,
        "MAIN_WINDOW_EMPTY_count": EXPECTED_MAIN_WINDOW_EMPTY,
        "network_requests": 0,
        "protected_outcome_access": False,
        "predecessor_request_id_set_sha256": EXPECTED_REQUEST_SET_SHA256,
    }
    for key, expected in required.items():
        if summary.get(key) != expected:
            raise AcquisitionError(
                f"frozen coverage-summary field {key} is {summary.get(key)!r}, "
                f"expected {expected!r}"
            )
    return summary


def _load_predecessor_specs() -> tuple[list[PredecessorSpec], dict]:
    _verify_frozen_boundary()
    summary = _load_coverage_summary()
    request_fields, request_rows = _read_csv(REQUEST_REGISTER)
    required_request_fields = {
        "request_sequence",
        "request_identity",
        "NORAD_CAT_ID",
        "reference_tca_utc",
        "lower_bound_utc",
        "predecessor_status",
        "selection_rule",
        "network_retrieval_authorized",
    }
    if not required_request_fields.issubset(request_fields):
        raise AcquisitionError("predecessor request register schema changed")
    if len(request_rows) != EXPECTED_REQUEST_COUNT:
        raise AcquisitionError(
            f"predecessor request count is {len(request_rows)}, expected {EXPECTED_REQUEST_COUNT}"
        )

    audit_fields, audit_rows = _read_csv(COVERAGE_AUDIT)
    required_audit_fields = {
        "NORAD_CAT_ID",
        "reference_tca_utc",
        "lower_bound_utc",
        "rows_before_lower",
        "predecessor_status",
        "main_window_status",
    }
    if not required_audit_fields.issubset(audit_fields):
        raise AcquisitionError("coverage audit schema changed")
    audit_by_object = {row["NORAD_CAT_ID"].strip(): row for row in audit_rows}
    if len(audit_by_object) != 13_854:
        raise AcquisitionError("coverage audit does not contain 13,854 unique objects")

    ids = [row["NORAD_CAT_ID"].strip() for row in request_rows]
    expected_ids = sorted(ids, key=lambda value: (int(value), value))
    if ids != expected_ids or len(set(ids)) != EXPECTED_REQUEST_COUNT:
        raise AcquisitionError("predecessor NORAD IDs are not unique ascending numeric values")
    payload = ("\n".join(ids) + "\n").encode("ascii")
    request_set_sha = hashlib.sha256(payload).hexdigest()
    if request_set_sha != EXPECTED_REQUEST_SET_SHA256:
        raise AcquisitionError(f"predecessor request-set SHA-256 mismatch: {request_set_sha}")

    specs: list[PredecessorSpec] = []
    max_delta = 0.0
    for expected_sequence, row in enumerate(request_rows, start=1):
        object_id = row["NORAD_CAT_ID"].strip()
        if int(row["request_sequence"]) != expected_sequence:
            raise AcquisitionError("predecessor request_sequence is not exactly 1..6347")
        if row["request_identity"].strip() != f"predecessor-{object_id}":
            raise AcquisitionError(f"request identity changed for {object_id}")
        if row["network_retrieval_authorized"].strip().upper() != "FALSE":
            raise AcquisitionError(f"network retrieval is not frozen FALSE for {object_id}")
        if row["predecessor_status"].strip() != "REQUIRES_DEDICATED_PREDECESSOR_QUERY":
            raise AcquisitionError(f"predecessor status changed for {object_id}")
        if row["selection_rule"].strip() != STABLE_TIE_RULE:
            raise AcquisitionError(f"predecessor selection rule changed for {object_id}")

        tca = _parse_utc(row["reference_tca_utc"])
        exact_cutoff = _parse_utc(row["lower_bound_utc"])
        transport_cutoff = _floor_second(exact_cutoff)
        delta = (exact_cutoff - transport_cutoff).total_seconds()
        if not (transport_cutoff <= exact_cutoff and 0.0 <= delta < 1.0):
            raise AcquisitionError(f"invalid exact/transport cutoff relation for {object_id}")
        max_delta = max(max_delta, delta)

        audit = audit_by_object.get(object_id)
        if audit is None:
            raise AcquisitionError(f"request object {object_id} is absent from coverage audit")
        if (
            audit["reference_tca_utc"].strip() != _iso(tca)
            or audit["lower_bound_utc"].strip() != _iso(exact_cutoff)
            or int(audit["rows_before_lower"]) != 0
            or audit["predecessor_status"].strip()
            != "REQUIRES_DEDICATED_PREDECESSOR_QUERY"
            or audit["main_window_status"].strip() != "MAIN_WINDOW_PRESENT"
        ):
            raise AcquisitionError(f"coverage-audit cutoff proof failed for {object_id}")

        specs.append(
            PredecessorSpec(
                request_sequence=expected_sequence,
                request_id=f"predecessor-{object_id}",
                object_id=object_id,
                reference_tca_utc=tca,
                exact_scientific_cutoff_utc=exact_cutoff,
                transport_cutoff_utc=transport_cutoff,
                final_path=RAW_DIR / f"gp_history_g6_predecessor_{object_id}.csv",
            )
        )

    proof = {
        "request_count": len(specs),
        "request_set_sha256": request_set_sha,
        "request_id_serialization": "ascending_numeric;LF;terminal_newline",
        "cutoff_equivalence_proof_count": len(specs),
        "maximum_exact_minus_transport_cutoff_seconds": max_delta,
    }
    return specs, proof


def build_query_url(spec: PredecessorSpec) -> str:
    creation_value = "<" + _space_track_seconds(spec.transport_cutoff_utc)
    ordering = "CREATION_DATE desc,EPOCH desc,GP_ID desc"
    segments = [
        "basicspacedata",
        "query",
        "class",
        "gp_history",
        "NORAD_CAT_ID",
        spec.object_id,
        "CREATION_DATE",
        creation_value,
        "orderby",
        ordering,
        "limit",
        "1",
        "format",
        "csv",
        "emptyresult",
        "show",
    ]
    encoded = "/".join(urllib.parse.quote(part, safe=",") for part in segments)
    return f"{BASE_URL}/{encoded}"


def non_secret_predicate(spec: PredecessorSpec) -> str:
    return (
        f"class=gp_history; NORAD_CAT_ID={spec.object_id}; "
        f"CREATION_DATE < {_iso(spec.transport_cutoff_utc)}; "
        "orderby=CREATION_DATE desc,EPOCH desc,GP_ID desc; limit=1; format=csv; "
        f"exact_scientific_cutoff={_iso(spec.exact_scientific_cutoff_utc)}"
    )


def _text_no_result_path(spec: PredecessorSpec) -> Path:
    return spec.final_path.with_name(f"{spec.final_path.stem}_no_results.txt")


def _invalid_response_path(spec: PredecessorSpec) -> Path:
    return spec.final_path.with_name(
        f"{spec.final_path.stem}_invalid_response.bin"
    )


def _looks_like_html(prefix: bytes, content_type: str) -> bool:
    lowered = prefix.lstrip().lower()
    return "text/html" in content_type.lower() or lowered.startswith(
        (b"<!doctype html", b"<html")
    )


def _classify_response_prefix(prefix: bytes, content_type: str) -> str | None:
    if _looks_like_html(prefix, content_type):
        raise AuthenticationFailure("Space-Track returned HTML/login content instead of CSV")
    if prefix.lstrip().upper().startswith(b"NO RESULTS RETURNED"):
        return "LEFT_CENSORED_NO_PREDECESSOR_RETURNED"
    return None


def validate_predecessor_csv(path: Path, spec: PredecessorSpec) -> dict:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = tuple(reader.fieldnames or ())
        missing_fields = sorted(REQUIRED_GP_FIELDS - set(fields))
        if missing_fields:
            raise SchemaFailure(
                f"CSV schema missing required fields: {','.join(missing_fields)}"
            )
        rows = list(reader)

    if len(rows) > 1:
        raise SchemaFailure(f"predecessor response contained {len(rows)} rows; maximum is 1")
    if not rows:
        return {
            "status": "LEFT_CENSORED_NO_PREDECESSOR_RETURNED",
            "row_count": 0,
            "NORAD_CAT_ID": None,
            "GP_ID": None,
            "CREATION_DATE": None,
            "EPOCH": None,
        }

    row = rows[0]
    object_id = (row.get("NORAD_CAT_ID") or "").strip()
    if object_id != spec.object_id:
        raise SchemaFailure(
            f"predecessor response object is {object_id!r}, expected {spec.object_id}"
        )
    try:
        creation = _parse_utc(row.get("CREATION_DATE") or "")
    except (TypeError, ValueError) as exc:
        raise SchemaFailure("predecessor CREATION_DATE is not parseable UTC") from exc
    try:
        epoch = _parse_utc(row.get("EPOCH") or "")
    except (TypeError, ValueError) as exc:
        raise SchemaFailure("predecessor EPOCH is not parseable UTC") from exc
    if creation >= spec.exact_scientific_cutoff_utc:
        raise SchemaFailure(
            "predecessor CREATION_DATE is not strictly before the exact scientific cutoff"
        )

    gp_id = (row.get("GP_ID") or "").strip()
    status = "SUCCESS"
    if not gp_id:
        status = "HOLD_MISSING_GP_ID_FALLBACK_REQUIRED"
    return {
        "status": status,
        "row_count": 1,
        "NORAD_CAT_ID": object_id,
        "GP_ID": gp_id or None,
        "CREATION_DATE": _iso(creation),
        "EPOCH": _iso(epoch),
    }


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    os.close(fd)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_state(path: Path = PREDECESSOR_STATE_FILE) -> dict:
    if not path.exists():
        return {"schema_version": 1, "records": []}
    with path.open("r", encoding="utf-8") as stream:
        state = json.load(stream)
    if state.get("schema_version") != 1 or not isinstance(state.get("records"), list):
        raise AcquisitionError("G6 predecessor acquisition-state schema is invalid")
    return state


def _save_state(state: dict, path: Path = PREDECESSOR_STATE_FILE) -> None:
    _atomic_write_json(path, state)


def _latest_state_record(state: dict, request_id: str) -> dict | None:
    matches = [row for row in state["records"] if row.get("request_id") == request_id]
    return matches[-1] if matches else None


def _verified_success_file(
    record: dict,
    *,
    root: Path = ROOT,
    raw_dir: Path = RAW_DIR,
) -> Path | None:
    if record.get("status") not in {
        "SUCCESS",
        "LEFT_CENSORED_NO_PREDECESSOR_RETURNED",
        "HOLD_MISSING_GP_ID_FALLBACK_REQUIRED",
    }:
        return None
    raw_filename = record.get("raw_filename")
    expected_sha = record.get("sha256")
    if not raw_filename or not expected_sha:
        return None
    path = (root / raw_filename).resolve()
    try:
        path.relative_to(raw_dir.resolve())
    except ValueError:
        raise AcquisitionError("predecessor state references a path outside data/raw/g6") from None
    if not path.is_file() or _sha256_file(path) != expected_sha:
        return None
    return path


def _spec_requires_network(
    state: dict,
    spec: PredecessorSpec,
    *,
    verification_root: Path = ROOT,
    verification_raw_dir: Path = RAW_DIR,
) -> bool:
    prior = _latest_state_record(state, spec.request_id)
    if prior is None:
        return True
    status = prior.get("status")
    if status == "SUCCESS":
        if _verified_success_file(
            prior, root=verification_root, raw_dir=verification_raw_dir
        ) is None:
            raise AcquisitionError(
                f"successful predecessor {spec.request_id} failed raw SHA-256 verification"
            )
        return False
    if status == "LEFT_CENSORED_NO_PREDECESSOR_RETURNED":
        if _verified_success_file(
            prior, root=verification_root, raw_dir=verification_raw_dir
        ) is None:
            raise AcquisitionError(
                f"left-censored predecessor {spec.request_id} failed retained-response "
                "SHA-256 verification"
            )
        return False
    if status == "HOLD_MISSING_GP_ID_FALLBACK_REQUIRED":
        if _verified_success_file(
            prior, root=verification_root, raw_dir=verification_raw_dir
        ) is None:
            raise AcquisitionError(
                f"held predecessor {spec.request_id} failed raw SHA-256 verification"
            )
        return False
    raise AcquisitionError(
        f"predecessor {spec.request_id} has a prior non-success attempt; "
        "automatic retry is prohibited"
    )


def _record_state(
    state: dict,
    spec: PredecessorSpec,
    result: dict,
    *,
    path: Path = PREDECESSOR_STATE_FILE,
) -> None:
    state["records"].append(
        {
            "request_sequence": spec.request_sequence,
            "request_id": spec.request_id,
            "kind": "predecessor",
            "NORAD_CAT_ID": spec.object_id,
            "status": result.get("status"),
            "retrieval_utc": result.get("retrieval_utc", _utc_now()),
            "exact_scientific_cutoff_utc": _iso(spec.exact_scientific_cutoff_utc),
            "transport_cutoff_utc": _iso(spec.transport_cutoff_utc),
            "query": non_secret_predicate(spec),
            "server_orderby": "CREATION_DATE desc,EPOCH desc,GP_ID desc",
            "limit": 1,
            "timeout_seconds": TIMEOUT_SECONDS,
            "automatic_retry": False,
            "row_count": result.get("row_count"),
            "byte_count": result.get("byte_count"),
            "sha256": result.get("sha256"),
            "raw_filename": result.get("raw_path"),
            "GP_ID": result.get("GP_ID"),
            "CREATION_DATE": result.get("CREATION_DATE"),
            "EPOCH": result.get("EPOCH"),
            "failure_class": result.get("failure_class"),
            "detail": result.get("detail"),
        }
    )
    _save_state(state, path)


def _tracked_tree_is_clean() -> bool:
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AuthorizationError(
            "cannot verify clean tracked working tree"
        ) from exc
    return not status.stdout.strip()


def _evaluate_pilot_authorization(
    tag_target: str | None,
    head: str,
    tracked_tree_clean: bool,
) -> str:
    if tag_target is None:
        raise AuthorizationError(
            f"pilot approval tag {PILOT_APPROVAL_TAG} is absent; "
            "network execution is unauthorized"
        )
    if tag_target != head:
        raise AuthorizationError(
            f"pilot approval tag resolves to {tag_target}, "
            f"but current HEAD is {head}"
        )
    if not tracked_tree_clean:
        raise AuthorizationError(
            "tracked working tree is not clean; pilot is unauthorized"
        )
    return "ACTIVE"


def _pilot_authorization_snapshot() -> dict:
    head = _git_rev_parse("HEAD")
    tag_target = _git_tag_target_if_exists(PILOT_APPROVAL_TAG)
    tracked_tree_clean = _tracked_tree_is_clean()

    if tag_target is None:
        state = "INACTIVE_TAG_ABSENT"
        matches_head = False
    else:
        state = _evaluate_pilot_authorization(
            tag_target,
            head,
            tracked_tree_clean,
        )
        matches_head = True

    return {
        "approval_tag_present": tag_target is not None,
        "approval_tag_target": tag_target,
        "approval_tag_matches_head": matches_head,
        "tracked_tree_clean": tracked_tree_clean,
        "network_authorization_state": state,
    }


def _verify_pilot_authorization() -> None:
    snapshot = _pilot_authorization_snapshot()

    if snapshot["network_authorization_state"] != "ACTIVE":
        raise AuthorizationError(
            f"pilot approval tag {PILOT_APPROVAL_TAG} is absent; "
            "network execution is unauthorized"
        )


def _get_credentials() -> tuple[str, str]:
    username = input("Space-Track username: ").strip()
    if not username:
        raise AuthenticationFailure("Space-Track username was empty")
    password = getpass.getpass("Space-Track password (hidden): ")
    if not password:
        raise AuthenticationFailure("Space-Track password was empty")
    return username, password


def authenticate(
    username: str, password: str, timeout_s: float
) -> tuple[urllib.request.OpenerDirector, http.cookiejar.CookieJar]:
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    body = urllib.parse.urlencode({"identity": username, "password": password}).encode("utf-8")
    request = urllib.request.Request(
        LOGIN_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "ORI-G6-predecessor-pilot/1.0",
        },
    )
    try:
        with opener.open(request, timeout=timeout_s) as response:
            payload = response.read(65536)
            final_url = response.geturl().lower()
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raise AuthenticationFailure(
            f"Space-Track authentication returned HTTP {exc.code}"
        ) from None
    except (urllib.error.URLError, TimeoutError, socket.timeout):
        raise AuthenticationFailure(
            "Space-Track authentication did not complete within the bounded timeout"
        ) from None
    lowered = payload.lower()
    if (
        "/auth/login" in final_url
        or b"login failed" in lowered
        or b"invalid username" in lowered
        or b"invalid password" in lowered
    ):
        raise AuthenticationFailure("Space-Track authentication was rejected")
    if not cookie_jar:
        raise AuthenticationFailure("authentication returned no in-memory session cookie")
    if _looks_like_html(payload, content_type) and b"login to space-track" in lowered:
        raise AuthenticationFailure("Space-Track returned the login page after authentication")
    return opener, cookie_jar


def stream_predecessor_request_once(
    opener: urllib.request.OpenerDirector,
    spec: PredecessorSpec,
    timeout_s: float,
    *,
    partial_dir: Path = PARTIAL_DIR,
    root: Path = ROOT,
) -> dict:
    text_no_result_path = _text_no_result_path(spec)
    invalid_response_path = _invalid_response_path(spec)

    existing = [
        path
        for path in (
            spec.final_path,
            text_no_result_path,
            invalid_response_path,
        )
        if path.exists()
    ]
    if existing:
        raise AcquisitionError(
            f"refusing to overwrite existing predecessor response evidence: {existing[0]}"
        )
    partial_dir.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{spec.final_path.name}.", suffix=".partial", dir=partial_dir
    )
    temporary = Path(temporary_name)
    os.close(fd)
    digest = hashlib.sha256()
    byte_count = 0
    prefix = b""
    prefix_status: str | None = None
    retrieval_utc = _utc_now()
    request = urllib.request.Request(
        build_query_url(spec), headers={"User-Agent": "ORI-G6-predecessor-pilot/1.0"}
    )
    try:
        try:
            with opener.open(request, timeout=timeout_s) as response, temporary.open(
                "wb"
            ) as output:
                status = getattr(response, "status", response.getcode())
                final_url = response.geturl().lower()
                content_type = response.headers.get("Content-Type", "")
                if status != 200:
                    raise TransportFailure(f"Space-Track data request returned HTTP {status}")
                if "/auth/login" in final_url:
                    raise AuthenticationFailure("data request redirected to the login page")
                for chunk in iter(lambda: response.read(CHUNK_BYTES), b""):
                    if not prefix:
                        prefix = chunk[:4096]
                        prefix_status = _classify_response_prefix(prefix, content_type)
                    output.write(chunk)
                    digest.update(chunk)
                    byte_count += len(chunk)
                output.flush()
                os.fsync(output.fileno())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise AuthenticationFailure(
                    f"Space-Track data request returned HTTP {exc.code}"
                ) from None
            raise TransportFailure(
                f"Space-Track data request returned HTTP {exc.code}; no retry was attempted"
            ) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            raise TransportFailure(
                f"Space-Track transport failed after {byte_count} streamed bytes; "
                "no retry was attempted"
            ) from None

        if byte_count == 0:
            raise TransportFailure("Space-Track returned zero bytes; not classified as no predecessor")
        response_sha = digest.hexdigest()
        if prefix_status == "LEFT_CENSORED_NO_PREDECESSOR_RETURNED":
            text_no_result_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, text_no_result_path)
            return {
                "status": prefix_status,
                "retrieval_utc": retrieval_utc,
                "byte_count": byte_count,
                "row_count": 0,
                "sha256": response_sha,
                "raw_path": text_no_result_path.relative_to(root).as_posix(),
            }

        try:
            stats = validate_predecessor_csv(temporary, spec)
        except SchemaFailure as exc:
            invalid_response_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            os.replace(
                temporary,
                invalid_response_path,
            )

            raise SchemaFailure(
                str(exc),
                retrieval_utc=retrieval_utc,
                byte_count=byte_count,
                sha256=response_sha,
                raw_path=invalid_response_path.relative_to(
                    root
                ).as_posix(),
                row_count=None,
            ) from exc

        if stats["status"] == "LEFT_CENSORED_NO_PREDECESSOR_RETURNED":
            spec.final_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, spec.final_path)
            return {
                **stats,
                "retrieval_utc": retrieval_utc,
                "byte_count": byte_count,
                "sha256": response_sha,
                "raw_path": spec.final_path.relative_to(root).as_posix(),
            }

        spec.final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, spec.final_path)
        return {
            **stats,
            "retrieval_utc": retrieval_utc,
            "byte_count": byte_count,
            "sha256": response_sha,
            "raw_path": spec.final_path.relative_to(root).as_posix(),
        }
    finally:
        temporary.unlink(missing_ok=True)


def _enforce_request_spacing(
    previous_start: float | None,
    *,
    now_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> float:
    if previous_start is None:
        return now_fn()
    while True:
        now = now_fn()
        remaining = MIN_REQUEST_START_SPACING_SECONDS - (now - previous_start)
        if remaining <= 0:
            return now
        sleep_fn(remaining)


def _failure_result(exc: Exception) -> dict:
    if isinstance(exc, AuthorizationError):
        failure_class = "AUTHORIZATION_FAILURE"
    elif isinstance(exc, AuthenticationFailure):
        failure_class = "AUTHENTICATION_FAILURE"
    elif isinstance(exc, SchemaFailure):
        failure_class = "SCHEMA_FAILURE"
    elif isinstance(exc, TransportFailure):
        failure_class = "TRANSPORT_FAILURE"
    else:
        failure_class = "ACQUISITION_FAILURE"
    return {
        "status": "FAILED",
        "failure_class": failure_class,
        "detail": str(exc),
        "retrieval_utc":
            getattr(exc, "retrieval_utc", None)
            or _utc_now(),
        "row_count":
            getattr(exc, "row_count", None),
        "byte_count":
            getattr(exc, "byte_count", None),
        "sha256":
            getattr(exc, "sha256", None),
        "raw_path":
            getattr(exc, "raw_path", None),
    }


def run_predecessor_pilot(
    *,
    authorization_fn: Callable[[], None] = _verify_pilot_authorization,
    credentials_fn: Callable[[], tuple[str, str]] = _get_credentials,
    authenticate_fn=authenticate,
    stream_fn=stream_predecessor_request_once,
) -> int:
    authorization_fn()  # Must precede credentials, authentication, and all requests.
    specs, _ = _load_predecessor_specs()
    state = _load_state()
    planned: list[PredecessorSpec] = []
    for spec in specs:
        if _spec_requires_network(state, spec):
            planned.append(spec)
            if len(planned) == PILOT_MAX_REQUESTS:
                break
    if not planned:
        print(json.dumps({"status": "NO_PENDING_PILOT_REQUESTS"}, sort_keys=True))
        return 0

    print(
        json.dumps(
            {
                "status": "PILOT_PREFLIGHT_PASS",
                "planned_request_count": len(planned),
                "pilot_max_requests": PILOT_MAX_REQUESTS,
                "timeout_seconds": TIMEOUT_SECONDS,
                "minimum_request_start_spacing_seconds": MIN_REQUEST_START_SPACING_SECONDS,
                "automatic_retry": False,
                "planned": [
                    {
                        "request_id": spec.request_id,
                        "NORAD_CAT_ID": spec.object_id,
                        "exact_scientific_cutoff_utc": _iso(
                            spec.exact_scientific_cutoff_utc
                        ),
                        "transport_cutoff_utc": _iso(spec.transport_cutoff_utc),
                        "query": non_secret_predicate(spec),
                    }
                    for spec in planned
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )

    username = password = ""
    cookie_jar: http.cookiejar.CookieJar | None = None
    last_request_started: float | None = None
    try:
        username, password = credentials_fn()
        opener, cookie_jar = authenticate_fn(username, password, TIMEOUT_SECONDS)
        password = ""
        for spec in planned:
            last_request_started = _enforce_request_spacing(last_request_started)
            try:
                result = stream_fn(opener, spec, TIMEOUT_SECONDS)
            except Exception as exc:
                _record_state(state, spec, _failure_result(exc))
                raise
            _record_state(state, spec, result)
            if result["status"] == "HOLD_MISSING_GP_ID_FALLBACK_REQUIRED":
                raise AcquisitionError(
                    f"{spec.request_id} requires explicit fallback review; pilot stopped"
                )
        print(
            json.dumps(
                {
                    "status": "PILOT_COMPLETE",
                    "new_requests": len(planned),
                    "state_file": PREDECESSOR_STATE_FILE.relative_to(ROOT).as_posix(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    finally:
        password = ""
        username = ""
        if cookie_jar is not None:
            cookie_jar.clear()


def _sample_gp_row(
    spec: PredecessorSpec,
    *,
    object_id: str | None = None,
    gp_id: str = "1",
    creation: datetime | None = None,
) -> dict[str, str]:
    creation_value = creation or (spec.transport_cutoff_utc - timedelta(seconds=1))
    values = {field: "1" for field in REQUIRED_GP_FIELDS}
    values.update(
        {
            "NORAD_CAT_ID": object_id or spec.object_id,
            "GP_ID": gp_id,
            "CREATION_DATE": _iso(creation_value),
            "EPOCH": _iso(creation_value),
            "OBJECT_ID": "1998-067A",
            "REF_FRAME": "TEME",
            "TIME_SYSTEM": "UTC",
            "MEAN_ELEMENT_THEORY": "SGP4",
        }
    )
    return values


def _write_test_csv(path: Path, rows: list[dict[str, str]], fields: Sequence[str] | None = None) -> None:
    fieldnames = list(fields or sorted(REQUIRED_GP_FIELDS))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _run_offline_tests(specs: list[PredecessorSpec]) -> dict:
    tests: dict[str, str] = {}
    expected_utc = datetime(2025, 1, 7, 18, 7, 6, tzinfo=UTC)
    for value in (
        "2025-01-07T18:07:06",
        "2025-01-07T18:07:06Z",
        "2025-01-07T11:07:06-07:00",
    ):
        if _parse_utc(value) != expected_utc:
            raise AcquisitionError(f"UTC parser regression failed for {value}")
    tests["utc_parser_regression"] = "PASS"

    first = specs[0]
    url = urllib.parse.unquote(build_query_url(first))
    if (
        "/class/gp_history/" not in url
        or f"/NORAD_CAT_ID/{first.object_id}/" not in url
        or "/orderby/CREATION_DATE desc,EPOCH desc,GP_ID desc/" not in url
        or "/limit/1/format/csv/emptyresult/show" not in url
        or ("/CREATION_DATE/<" + _space_track_seconds(first.transport_cutoff_utc)) not in url
    ):
        raise AcquisitionError("predecessor URL construction regression failed")
    predicate = non_secret_predicate(first)
    if _iso(first.exact_scientific_cutoff_utc) not in predicate:
        raise AcquisitionError("non-secret predicate omits exact scientific cutoff")
    tests["url_and_predicate_construction"] = "PASS"

    class OfflineResponse:
        def __init__(self, payload: bytes, content_type: str):
            self.payload = payload
            self.content_type = content_type
            self.offset = 0
            self.status = 200
            self.headers = {"Content-Type": content_type}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def getcode(self) -> int:
            return self.status

        def geturl(self) -> str:
            return f"{BASE_URL}/basicspacedata/query/class/gp_history"

        def read(self, size: int = -1) -> bytes:
            if self.offset >= len(self.payload):
                return b""
            if size < 0:
                size = len(self.payload) - self.offset
            result = self.payload[self.offset : self.offset + size]
            self.offset += len(result)
            return result

    class OfflineOpener:
        def __init__(self, payload: bytes, content_type: str):
            self.payload = payload
            self.content_type = content_type

        def open(self, *_args, **_kwargs):
            return OfflineResponse(self.payload, self.content_type)

    with tempfile.TemporaryDirectory(prefix="g6-pred-offline-") as temporary_name:
        temporary = Path(temporary_name)
        valid = temporary / "valid.csv"
        _write_test_csv(valid, [_sample_gp_row(first)])
        if validate_predecessor_csv(valid, first)["status"] != "SUCCESS":
            raise AcquisitionError("valid one-row predecessor test failed")
        tests["valid_one_row_response"] = "PASS"

        wrong = temporary / "wrong.csv"
        _write_test_csv(wrong, [_sample_gp_row(first, object_id="999999")])
        try:
            validate_predecessor_csv(wrong, first)
            raise AcquisitionError("wrong NORAD response was not rejected")
        except SchemaFailure:
            tests["wrong_norad_rejection"] = "PASS"

        late = temporary / "late.csv"
        _write_test_csv(
            late,
            [_sample_gp_row(first, creation=first.exact_scientific_cutoff_utc)],
        )
        try:
            validate_predecessor_csv(late, first)
            raise AcquisitionError("noncausal predecessor was not rejected")
        except SchemaFailure:
            tests["exact_cutoff_rejection"] = "PASS"

        malformed = temporary / "malformed.csv"
        _write_test_csv(malformed, [{"NORAD_CAT_ID": first.object_id}], ["NORAD_CAT_ID"])
        try:
            validate_predecessor_csv(malformed, first)
            raise AcquisitionError("schema-invalid predecessor was not rejected")
        except SchemaFailure:
            tests["schema_rejection"] = "PASS"

        try:
            _classify_response_prefix(b"NO RESULTS RETURNED<html>login</html>", "text/html")
            raise AcquisitionError("HTML no-result-like response was classified as left-censored")
        except AuthenticationFailure:
            tests["html_before_no_result_classification"] = "PASS"

        if (
            _classify_response_prefix(b"NO RESULTS RETURNED", "text/plain")
            != "LEFT_CENSORED_NO_PREDECESSOR_RETURNED"
        ):
            raise AcquisitionError("explicit no-result classification failed")
        tests["explicit_no_result_classification"] = "PASS"

        header_only = temporary / "header.csv"
        _write_test_csv(header_only, [])
        if (
            validate_predecessor_csv(header_only, first)["status"]
            != "LEFT_CENSORED_NO_PREDECESSOR_RETURNED"
        ):
            raise AcquisitionError("header-only predecessor classification failed")
        tests["header_only_classification"] = "PASS"

        test_root = temporary / "repo"
        test_raw = test_root / "data/raw/g6"
        test_raw.mkdir(parents=True)

        explicit_spec = PredecessorSpec(
            request_sequence=first.request_sequence,
            request_id=first.request_id,
            object_id=first.object_id,
            reference_tca_utc=first.reference_tca_utc,
            exact_scientific_cutoff_utc=first.exact_scientific_cutoff_utc,
            transport_cutoff_utc=first.transport_cutoff_utc,
            final_path=test_raw / first.final_path.name,
        )
        explicit_payload = b"NO RESULTS RETURNED\n"
        explicit_result = stream_predecessor_request_once(
            OfflineOpener(explicit_payload, "text/plain"),
            explicit_spec,
            TIMEOUT_SECONDS,
            partial_dir=temporary / "explicit_partial",
            root=test_root,
        )
        explicit_path = test_root / explicit_result["raw_path"]
        if (
            explicit_result["status"] != "LEFT_CENSORED_NO_PREDECESSOR_RETURNED"
            or explicit_result["row_count"] != 0
            or explicit_result["byte_count"] != len(explicit_payload)
            or explicit_result["sha256"] != hashlib.sha256(explicit_payload).hexdigest()
            or explicit_path.suffix != ".txt"
            or explicit_path.read_bytes() != explicit_payload
        ):
            raise AcquisitionError("explicit no-result evidence was not retained exactly")
        explicit_state_path = temporary / "explicit_state.json"
        explicit_state = {"schema_version": 1, "records": []}
        _record_state(
            explicit_state,
            explicit_spec,
            explicit_result,
            path=explicit_state_path,
        )
        explicit_state = _load_state(explicit_state_path)
        if _spec_requires_network(
            explicit_state,
            explicit_spec,
            verification_root=test_root,
            verification_raw_dir=test_raw,
        ):
            raise AcquisitionError("verified textual no-result evidence was not resume-skipped")
        tests["explicit_no_result_evidence_retained_and_verified"] = "PASS"

        second = specs[1]
        header_spec = PredecessorSpec(
            request_sequence=second.request_sequence,
            request_id=second.request_id,
            object_id=second.object_id,
            reference_tca_utc=second.reference_tca_utc,
            exact_scientific_cutoff_utc=second.exact_scientific_cutoff_utc,
            transport_cutoff_utc=second.transport_cutoff_utc,
            final_path=test_raw / second.final_path.name,
        )
        header_payload = header_only.read_bytes()
        header_result = stream_predecessor_request_once(
            OfflineOpener(header_payload, "text/csv"),
            header_spec,
            TIMEOUT_SECONDS,
            partial_dir=temporary / "header_partial",
            root=test_root,
        )
        header_path = test_root / header_result["raw_path"]
        if (
            header_result["status"] != "LEFT_CENSORED_NO_PREDECESSOR_RETURNED"
            or header_result["sha256"] != hashlib.sha256(header_payload).hexdigest()
            or header_path.suffix != ".csv"
            or header_path.read_bytes() != header_payload
        ):
            raise AcquisitionError("header-only CSV evidence was not retained exactly")
        header_state_path = temporary / "header_state.json"
        header_state = {"schema_version": 1, "records": []}
        _record_state(header_state, header_spec, header_result, path=header_state_path)
        header_state = _load_state(header_state_path)
        if _spec_requires_network(
            header_state,
            header_spec,
            verification_root=test_root,
            verification_raw_dir=test_raw,
        ):
            raise AcquisitionError("verified header-only evidence was not resume-skipped")
        tests["header_only_evidence_retained_and_verified"] = "PASS"
        tests["left_censored_resume_requires_verified_evidence"] = "PASS"

        explicit_path.write_bytes(explicit_payload + b"tampered")
        try:
            _spec_requires_network(
                explicit_state,
                explicit_spec,
                verification_root=test_root,
                verification_raw_dir=test_raw,
            )
            raise AcquisitionError("tampered no-result evidence was resume-skipped")
        except AcquisitionError as exc:
            if "failed retained-response SHA-256 verification" not in str(exc):
                raise
        explicit_path.write_bytes(explicit_payload)
        explicit_path.unlink()
        try:
            _spec_requires_network(
                explicit_state,
                explicit_spec,
                verification_root=test_root,
                verification_raw_dir=test_raw,
            )
            raise AcquisitionError("missing no-result evidence was resume-skipped")
        except AcquisitionError as exc:
            if "failed retained-response SHA-256 verification" not in str(exc):
                raise
        tests["left_censored_tampered_or_missing_evidence_rejected"] = "PASS"

        try:
            stream_predecessor_request_once(
                OfflineOpener(header_payload, "text/csv"),
                header_spec,
                TIMEOUT_SECONDS,
                partial_dir=temporary / "overwrite_partial",
                root=test_root,
            )
            raise AcquisitionError("existing header-only response could be overwritten")
        except AcquisitionError as exc:
            if "refusing to overwrite" not in str(exc):
                raise
        tests["no_overwrite_for_retained_response"] = "PASS"

        third = specs[2]

        invalid_spec = PredecessorSpec(
            request_sequence=third.request_sequence,
            request_id=third.request_id,
            object_id=third.object_id,
            reference_tca_utc=third.reference_tca_utc,
            exact_scientific_cutoff_utc=
                third.exact_scientific_cutoff_utc,
            transport_cutoff_utc=
                third.transport_cutoff_utc,
            final_path=
                test_raw / third.final_path.name,
        )

        invalid_payload = (
            b"NORAD_CAT_ID\n"
            + third.object_id.encode("ascii")
            + b"\n"
        )

        try:
            stream_predecessor_request_once(
                OfflineOpener(
                    invalid_payload,
                    "text/csv",
                ),
                invalid_spec,
                TIMEOUT_SECONDS,
                partial_dir=
                    temporary / "invalid_partial",
                root=test_root,
            )

            raise AcquisitionError(
                "schema-invalid HTTP-200 response "
                "was not rejected"
            )

        except SchemaFailure as exc:
            invalid_result = _failure_result(exc)

        invalid_path = (
            test_root
            / invalid_result["raw_path"]
        )

        if (
            invalid_result["status"] != "FAILED"
            or
            invalid_result["failure_class"]
            != "SCHEMA_FAILURE"
            or
            invalid_result["byte_count"]
            != len(invalid_payload)
            or
            invalid_result["sha256"]
            != hashlib.sha256(
                invalid_payload
            ).hexdigest()
            or
            invalid_path.suffix != ".bin"
            or
            invalid_path.read_bytes()
            != invalid_payload
        ):
            raise AcquisitionError(
                "schema-invalid response evidence "
                "was not retained exactly"
            )

        tests[
            "schema_invalid_http200_evidence_retained"
        ] = "PASS"

        invalid_state_path = (
            temporary / "invalid_state.json"
        )

        invalid_state = {
            "schema_version": 1,
            "records": [],
        }

        _record_state(
            invalid_state,
            invalid_spec,
            invalid_result,
            path=invalid_state_path,
        )

        invalid_state = _load_state(
            invalid_state_path
        )

        preserved_invalid = (
            invalid_state["records"][-1]
        )

        if (
            preserved_invalid.get("status")
            != "FAILED"
            or
            preserved_invalid.get(
                "failure_class"
            )
            != "SCHEMA_FAILURE"
            or
            preserved_invalid.get("sha256")
            != invalid_result["sha256"]
            or
            preserved_invalid.get("byte_count")
            != invalid_result["byte_count"]
            or
            preserved_invalid.get("raw_filename")
            != invalid_result["raw_path"]
        ):
            raise AcquisitionError(
                "schema-invalid failure provenance "
                "was not preserved"
            )

        tests[
            "schema_invalid_failure_state_preserved"
        ] = "PASS"

        try:
            stream_predecessor_request_once(
                OfflineOpener(
                    invalid_payload,
                    "text/csv",
                ),
                invalid_spec,
                TIMEOUT_SECONDS,
                partial_dir=
                    temporary
                    / "invalid_overwrite_partial",
                root=test_root,
            )

            raise AcquisitionError(
                "existing schema-invalid evidence "
                "could be overwritten"
            )

        except AcquisitionError as exc:
            if "refusing to overwrite" not in str(exc):
                raise

        tests[
            "schema_invalid_evidence_no_overwrite"
        ] = "PASS"

        try:
            _spec_requires_network(
                invalid_state,
                invalid_spec,
                verification_root=test_root,
                verification_raw_dir=test_raw,
            )

            raise AcquisitionError(
                "schema-failed state permitted "
                "automatic retry"
            )

        except AcquisitionError as exc:
            if (
                "automatic retry is prohibited"
                not in str(exc)
            ):
                raise

        tests[
            "schema_failed_state_blocks_automatic_retry"
        ] = "PASS"

        missing_gp = temporary / "missing_gp.csv"
        _write_test_csv(missing_gp, [_sample_gp_row(first, gp_id="")])
        if (
            validate_predecessor_csv(missing_gp, first)["status"]
            != "HOLD_MISSING_GP_ID_FALLBACK_REQUIRED"
        ):
            raise AcquisitionError("missing-GP_ID predecessor did not produce safe HOLD")
        tests["missing_gp_id_hold"] = "PASS"

        retained = test_raw / first.final_path.name
        retained.write_bytes(valid.read_bytes())
        success_record = {
            "request_id": first.request_id,
            "status": "SUCCESS",
            "raw_filename": retained.relative_to(test_root).as_posix(),
            "sha256": _sha256_file(retained),
        }
        success_state = {"schema_version": 1, "records": [success_record]}
        if _spec_requires_network(
            success_state,
            first,
            verification_root=test_root,
            verification_raw_dir=test_raw,
        ):
            raise AcquisitionError("verified success was not resume-skipped")
        test_spec = PredecessorSpec(
            request_sequence=first.request_sequence,
            request_id=first.request_id,
            object_id=first.object_id,
            reference_tca_utc=first.reference_tca_utc,
            exact_scientific_cutoff_utc=first.exact_scientific_cutoff_utc,
            transport_cutoff_utc=first.transport_cutoff_utc,
            final_path=retained,
        )
        try:
            stream_predecessor_request_once(
                None, test_spec, TIMEOUT_SECONDS, partial_dir=temporary / "partial", root=test_root
            )
            raise AcquisitionError("verified predecessor raw file could be overwritten")
        except AcquisitionError as exc:
            if "refusing to overwrite" not in str(exc):
                raise
        tests["resume_and_no_overwrite"] = "PASS"

        failed_state_path = temporary / "failed_state.json"
        failed_state = {"schema_version": 1, "records": []}
        _record_state(
            failed_state,
            first,
            {
                "status": "FAILED",
                "failure_class": "TRANSPORT_FAILURE",
                "detail": "synthetic offline failure",
            },
            path=failed_state_path,
        )
        preserved = _load_state(failed_state_path)
        if len(preserved["records"]) != 1:
            raise AcquisitionError("failed predecessor attempt was not preserved")
        try:
            _spec_requires_network(preserved, first)
            raise AcquisitionError("failed predecessor attempt permitted automatic retry")
        except AcquisitionError as exc:
            if "automatic retry is prohibited" not in str(exc):
                raise
        tests["failed_attempt_preserved_no_automatic_retry"] = "PASS"

    if PREDECESSOR_STATE_FILE == MAIN_STATE_FILE:
        raise AcquisitionError("predecessor state is not isolated from main acquisition state")
    tests["main_state_isolation"] = "PASS"

    calls = {
        "credentials": 0,
        "authenticate": 0,
        "network": 0,
    }

    def credentials_probe() -> tuple[str, str]:
        calls["credentials"] += 1
        return "should-not", "run"

    def authenticate_probe(*_args):
        calls["authenticate"] += 1
        raise AssertionError(
            "authentication must not run"
        )

    def network_probe(*_args):
        calls["network"] += 1
        raise AssertionError(
            "network must not run"
        )

    def synthetic_missing_tag_authorization():
        _evaluate_pilot_authorization(
            None,
            "synthetic-head",
            True,
        )

    try:
        run_predecessor_pilot(
            authorization_fn=
                synthetic_missing_tag_authorization,
            credentials_fn=credentials_probe,
            authenticate_fn=authenticate_probe,
            stream_fn=network_probe,
        )

        raise AcquisitionError(
            "synthetic absent pilot tag "
            "did not reject network mode"
        )

    except AuthorizationError:
        pass

    if calls != {
        "credentials": 0,
        "authenticate": 0,
        "network": 0,
    }:
        raise AcquisitionError(
            f"authorization-first guard failed: {calls}"
        )

    tests[
        "absent_tag_rejects_before_authentication_and_network"
    ] = "PASS"

    if (
        _evaluate_pilot_authorization(
            "synthetic-head",
            "synthetic-head",
            True,
        )
        != "ACTIVE"
    ):
        raise AcquisitionError(
            "synthetic valid pilot tag "
            "did not produce ACTIVE authorization"
        )

    tests[
        "valid_tag_state_offline_evaluation"
    ] = "PASS"

    for bad_target, bad_head, clean in (
        ("wrong", "synthetic-head", True),
        ("synthetic-head", "synthetic-head", False),
    ):
        try:
            _evaluate_pilot_authorization(
                bad_target,
                bad_head,
                clean,
            )

            raise AcquisitionError(
                "invalid synthetic authorization "
                "state was accepted"
            )

        except AuthorizationError:
            pass

    tests[
        "wrong_tag_or_dirty_tree_rejected"
    ] = "PASS"

    return tests


def offline_validate() -> dict:
    specs, proof = _load_predecessor_specs()

    authorization = (
        _pilot_authorization_snapshot()
    )

    tests = _run_offline_tests(specs)

    payload = {
        "status": "PASS",
        "network_requests": 0,
        "protected_outcome_access": False,
        "request_count":
            proof["request_count"],
        "request_set_sha256":
            proof["request_set_sha256"],
        "request_id_serialization":
            proof["request_id_serialization"],
        "coverage_freeze_commit":
            COVERAGE_FREEZE_COMMIT,
        "coverage_freeze_commit_is_ancestor":
            True,
        "main_acquisition_approval_tag":
            MAIN_ACQUISITION_APPROVAL_TAG,
        "main_acquisition_approval_commit":
            MAIN_ACQUISITION_APPROVAL_COMMIT,
        "frozen_input_sha256":
            EXPECTED_HASHES,
        "predecessor_state_path":
            PREDECESSOR_STATE_FILE
            .relative_to(ROOT)
            .as_posix(),
        "main_state_path":
            MAIN_STATE_FILE
            .relative_to(ROOT)
            .as_posix(),
        "main_state_isolation_check":
            "PASS",
        "cutoff_equivalence_proof_count":
            proof[
                "cutoff_equivalence_proof_count"
            ],
        "maximum_exact_minus_transport_cutoff_seconds":
            proof[
                "maximum_exact_minus_transport_cutoff_seconds"
            ],
        "missing_GP_ID_policy":
            "HOLD_MISSING_GP_ID_FALLBACK_REQUIRED",
        "authenticated_empty_policy":
            "LEFT_CENSORED_NO_PREDECESSOR_RETURNED",
        "automatic_retry":
            AUTOMATIC_RETRY,
        "pilot_max_requests":
            PILOT_MAX_REQUESTS,
        "timeout_seconds":
            TIMEOUT_SECONDS,
        "minimum_request_start_spacing_seconds":
            MIN_REQUEST_START_SPACING_SECONDS,
        "request_spacing_clock":
            "monotonic",
        "approval_tag_required":
            PILOT_APPROVAL_TAG,
        **authorization,
        "unauthorized_network_rejection_verified":
            True,
        "scaled_acquisition_implemented":
            False,
        "offline_tests":
            tests,
    }

    if not authorization[
        "approval_tag_present"
    ]:
        payload[
            "offline_validation_artifact_write"
        ] = "WRITTEN_PREAUTHORIZATION"

        _atomic_write_json(
            OFFLINE_VALIDATION,
            payload,
        )

    else:
        payload[
            "offline_validation_artifact_write"
        ] = (
            "SKIPPED_TAGGED_RUNTIME_TO_"
            "PRESERVE_CLEAN_TRACKED_TREE"
        )

    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--offline-validate",
        action="store_true",
        help="run deterministic local validation; performs zero network requests",
    )
    modes.add_argument(
        "--acquire-predecessor-pilot",
        action="store_true",
        help="future tag-gated pilot of at most three predecessor requests",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.offline_validate:
            print(json.dumps(offline_validate(), indent=2, sort_keys=True))
            return 0
        if args.acquire_predecessor_pilot:
            return run_predecessor_pilot()
        raise AcquisitionError("no mode selected")
    except (AcquisitionError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
