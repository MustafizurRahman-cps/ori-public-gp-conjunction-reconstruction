#!/usr/bin/env python3
"""Secure staged Space-Track GP_HISTORY acquisition client for frozen G6.

This client is intentionally conservative:
- default behavior is impossible without an explicit mode;
- --offline-validate performs no network access;
- --acquire-main requires explicit authorization and limits new GP_HISTORY
  requests per invocation;
- automatic retries are prohibited;
- raw responses are retained only under data/raw/g6/;
- credentials and cookies are never persisted.

Predecessor acquisition is intentionally NOT implemented here. It is authorized
only after main-history acquisition is complete and a separate coverage audit
determines which objects actually require predecessor retrieval.
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
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]

PARENT_APPROVED_TAG = "g6-acquisition-utc-parser-amendment-approved"
PARENT_APPROVED_COMMIT = "5861b8cc947eb0424683e069b7d9ab98352c0bfe"
RUNTIME_APPROVAL_TAG = "g6-main-acquisition-scaling-approved"

FINAL_SAMPLE = ROOT / "docs/research/G6_Final_Confirmatory_Sample_Register.csv"
G5_ALLOCATION = ROOT / "docs/research/G5_V2_Calibration_Allocation_Register.csv"
BATCH_REGISTER = ROOT / "docs/research/G6_GPHistory_Production_Batch_Register.csv"
OFFLINE_VALIDATION = ROOT / "results/g6/G6_GPHistory_Acquisition_Offline_Validation.json"
THREE_BATCH_CHECKPOINT = ROOT / "results/g6/G6_Three_Batch_Transport_Checkpoint.json"

RAW_DIR = ROOT / "data/raw/g6"
PARTIAL_DIR = RAW_DIR / ".partial"
STATE_FILE = RAW_DIR / "G6_SpaceTrack_Acquisition_State.json"

EXPECTED_SAMPLE_SHA256 = "f8a103cbd268e7b6d3e95515e8a806da403511196145897997866adc1fab10ef"
EXPECTED_G5_ALLOCATION_SHA256 = "78ea7546d0cf679fc00fcb9414c9ba7c1e032b8ec9ab981b9bc9fdb06d975514"
EXPECTED_BATCH_REGISTER_SHA256 = "02883a71d797b661b83acf13d03f6756b79c44c02fdb30f9410c7d3313ea832e"
EXPECTED_THREE_BATCH_CHECKPOINT_SHA256 = (
    "77a13cc7d50af51c529138c2f29115bc586eb257dafe9f0e63108c0ebb37d2a2"
)

EXPECTED_EVENTS = 6927
EXPECTED_OBJECTS = 13854
EXPECTED_BATCHES = 1386
EXPECTED_STRATA = {
    "G6-T1": 1386,
    "G6-T2": 1386,
    "G6-T3": 1385,
    "G6-T4": 1385,
    "G6-T5": 1385,
}

PRODUCTION_BATCH_SIZE = 10
FINAL_PARTIAL_BATCH_SIZE = 4
DEFAULT_TIMEOUT_S = 120.0
MAX_NEW_MAIN_BATCHES = 25
MIN_REQUEST_START_SPACING_S = 15.0
CHUNK_BYTES = 1024 * 1024

LOCAL_CAUSAL_TRIM_RULE = "[object_reference_tca_utc - 180 days, object_reference_tca_utc)"
STABLE_TIE_RULE = "(CREATION_DATE, EPOCH, stable_tie_key); stable_tie_key=GP_ID when present, else canonical-record SHA-256"

BASE_URL = "https://www.space-track.org"
LOGIN_URL = f"{BASE_URL}/ajaxauth/login"

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

PROTECTED_FIELD_NAMES = {
    "min_range",
    "mdistance",
    "relative_speed",
    "pc",
    "reconstructed_miss_distance",
    "e_d",
    "e_t",
    "reference_state",
    "covariance",
}


class AcquisitionError(RuntimeError):
    pass


class AuthenticationFailure(AcquisitionError):
    pass


class ThrottledFailure(AcquisitionError):
    pass


class TransportFailure(AcquisitionError):
    pass


class SchemaFailure(AcquisitionError):
    pass


@dataclass(frozen=True)
class BatchSpec:
    sequence: int
    batch_id: str
    object_ids: tuple[str, ...]
    start_utc: datetime
    end_exclusive_utc: datetime
    final_path: Path

    @property
    def period_text(self) -> str:
        return f"[{_iso(self.start_utc)}, {_iso(self.end_exclusive_utc)})"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    """Parse Space-Track UTC timestamps without local-time reinterpretation."""
    text = value.strip()
    if not text:
        raise ValueError("empty UTC datetime")

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    parsed = datetime.fromisoformat(text)

    if parsed.tzinfo is None:
        # Space-Track timestamp fields are UTC even when the returned
        # representation contains no explicit UTC offset.
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)

    return parsed


def _verify_utc_parser_semantics() -> None:
    """Regression guard for naive, Z-suffixed, and offset-aware UTC forms."""
    expected = datetime(2025, 1, 7, 18, 7, 6, tzinfo=UTC)

    cases = {
        "timezone-less Space-Track UTC":
            "2025-01-07T18:07:06",
        "explicit Z":
            "2025-01-07T18:07:06Z",
        "equivalent explicit offset":
            "2025-01-07T11:07:06-07:00",
    }

    for label, value in cases.items():
        actual = _parse_utc(value)
        if actual != expected:
            raise AcquisitionError(
                f"UTC parser regression failed for {label}: "
                f"{value!r} -> {actual.isoformat()}, "
                f"expected {expected.isoformat()}"
            )


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _space_track_time_seconds(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _transport_bounds(spec: BatchSpec) -> tuple[datetime, datetime]:
    """Return whole-second [start, end) transport bounds enclosing exact union bounds."""
    start = spec.start_utc.replace(microsecond=0)
    end_floor = spec.end_exclusive_utc.replace(microsecond=0)
    end_exclusive = (
        end_floor + timedelta(seconds=1)
        if spec.end_exclusive_utc.microsecond
        else end_floor
    )
    if not start < end_exclusive:
        raise AcquisitionError(f"invalid transport interval for {spec.batch_id}")
    return start, end_exclusive


def _transport_period_text(spec: BatchSpec) -> str:
    start, end_exclusive = _transport_bounds(spec)
    return f"[{_iso(start)}, {_iso(end_exclusive)})"


def _required_request_start_wait_seconds(
    previous_start: float | None,
    current_time: float,
    minimum_spacing: float = MIN_REQUEST_START_SPACING_S,
) -> float:
    """Return a nonnegative wait that separates consecutive request start times."""
    if previous_start is None:
        return 0.0
    elapsed = max(0.0, current_time - previous_start)
    return max(0.0, minimum_spacing - elapsed)


def _wait_for_request_start_spacing(
    previous_start: float | None,
    *,
    now_fn=time.monotonic,
    sleep_fn=time.sleep,
) -> float:
    """Wait only as needed, then return the monotonic start time for the next request."""
    while True:
        current_time = now_fn()
        wait_seconds = _required_request_start_wait_seconds(previous_start, current_time)
        if wait_seconds <= 0.0:
            return current_time
        sleep_fn(wait_seconds)


def _verify_request_spacing_calculation() -> None:
    cases = (
        (None, 100.0, 0.0),
        (100.0, 115.0, 0.0),
        (100.0, 120.0, 0.0),
        (100.0, 105.0, 10.0),
        (100.0, 99.0, 15.0),
    )
    for previous_start, current_time, expected in cases:
        actual = _required_request_start_wait_seconds(previous_start, current_time)
        if actual != expected or actual < 0.0:
            raise AcquisitionError(
                "request-start spacing regression failed: "
                f"previous={previous_start}, current={current_time}, "
                f"actual={actual}, expected={expected}"
            )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    os.close(fd)
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


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


def _verify_parent_approved_boundary() -> None:
    tag_target = _git_rev_parse(f"{PARENT_APPROVED_TAG}^{{commit}}")
    if tag_target != PARENT_APPROVED_COMMIT:
        raise AcquisitionError(
            f"{PARENT_APPROVED_TAG} resolves to {tag_target}, "
            f"expected {PARENT_APPROVED_COMMIT}"
        )


def _verify_runtime_acquisition_authorization() -> None:
    tag_target = _git_tag_target_if_exists(RUNTIME_APPROVAL_TAG)
    if tag_target is None:
        raise AcquisitionError(
            f"runtime approval tag {RUNTIME_APPROVAL_TAG} is absent; acquisition is unauthorized"
        )
    head = _git_rev_parse("HEAD")
    if tag_target != head:
        raise AcquisitionError(
            f"runtime approval tag {RUNTIME_APPROVAL_TAG} resolves to {tag_target}, "
            f"but current HEAD is {head}"
        )
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AcquisitionError("cannot verify the tracked working-tree state") from exc
    if status.stdout.strip():
        raise AcquisitionError(
            "tracked working tree is not clean; acquisition is unauthorized"
        )


def _read_csv(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = tuple(reader.fieldnames or ())
        rows = list(reader)
    return fields, rows


def _load_sample() -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    actual = _sha256_file(FINAL_SAMPLE)
    if actual != EXPECTED_SAMPLE_SHA256:
        raise AcquisitionError(f"approved sample SHA-256 mismatch: {actual}")

    fields, rows = _read_csv(FINAL_SAMPLE)
    if len(rows) != EXPECTED_EVENTS:
        raise AcquisitionError(f"approved sample row count is {len(rows)}, expected {EXPECTED_EVENTS}")
    if len({row["conj_id"] for row in rows}) != EXPECTED_EVENTS:
        raise AcquisitionError("approved sample conjunction IDs are not unique")

    expected_indices = list(range(1, EXPECTED_EVENTS + 1))
    indices = [int(row["final_g6_sample_index"]) for row in rows]
    if indices != expected_indices:
        raise AcquisitionError("final G6 sample indices are not contiguous and deterministic")

    strata = Counter(row["g6_temporal_stratum"] for row in rows)
    if dict(strata) != EXPECTED_STRATA:
        raise AcquisitionError(f"unexpected G6 stratum counts: {dict(strata)}")

    if PROTECTED_FIELD_NAMES & {field.lower() for field in fields}:
        raise AcquisitionError("approved sample unexpectedly exposes a protected field name")

    objects: dict[str, dict[str, str]] = {}
    for row in rows:
        for role in ("obj1", "obj2"):
            object_id = row[role].strip()
            if object_id in objects:
                raise AcquisitionError(f"G6 object is reused in final sample: {object_id}")
            objects[object_id] = {
                "conj_id": row["conj_id"].strip(),
                "final_g6_sample_index": row["final_g6_sample_index"].strip(),
                "reserve_event_index": row["reserve_event_index"].strip(),
                "g6_temporal_stratum": row["g6_temporal_stratum"].strip(),
                "reference_tca_utc": row["reference_tca_utc"].strip(),
                "object_role": role,
            }

    if len(objects) != EXPECTED_OBJECTS:
        raise AcquisitionError(f"approved G6 object count is {len(objects)}, expected {EXPECTED_OBJECTS}")
    return rows, objects


def _verify_g5_overlap(sample_objects: dict[str, dict[str, str]]) -> None:
    actual = _sha256_file(G5_ALLOCATION)
    if actual != EXPECTED_G5_ALLOCATION_SHA256:
        raise AcquisitionError(f"frozen G5 allocation SHA-256 mismatch: {actual}")

    _, rows = _read_csv(G5_ALLOCATION)
    g5_objects = {
        object_id.strip()
        for row in rows
        for object_id in (row["obj1"], row["obj2"])
    }

    if len(g5_objects) != 354:
        raise AcquisitionError(
            f"frozen G5 development-object count is {len(g5_objects)}, expected 354"
        )

    overlap = set(sample_objects) & g5_objects
    if overlap:
        raise AcquisitionError(
            f"G5/G6 object overlap is {len(overlap)}, expected zero"
        )


def _load_batch_register(
    sample_objects: dict[str, dict[str, str]]
) -> tuple[list[BatchSpec], list[dict[str, str]]]:
    actual = _sha256_file(BATCH_REGISTER)
    if actual != EXPECTED_BATCH_REGISTER_SHA256:
        raise AcquisitionError(f"batch-register SHA-256 mismatch: {actual}")

    fields, rows = _read_csv(BATCH_REGISTER)
    if len(rows) != EXPECTED_OBJECTS:
        raise AcquisitionError(f"batch-register object rows are {len(rows)}, expected {EXPECTED_OBJECTS}")
    if PROTECTED_FIELD_NAMES & {field.lower() for field in fields}:
        raise AcquisitionError("batch register unexpectedly contains a protected field name")

    object_ids = [row["norad_cat_id"].strip() for row in rows]
    if len(set(object_ids)) != EXPECTED_OBJECTS:
        raise AcquisitionError("batch register contains duplicate object IDs")
    if set(object_ids) != set(sample_objects):
        missing = sorted(set(sample_objects) - set(object_ids), key=lambda x: (int(x), x))
        extra = sorted(set(object_ids) - set(sample_objects), key=lambda x: (int(x), x))
        raise AcquisitionError(
            f"batch-register membership differs from approved sample; missing={len(missing)}, extra={len(extra)}"
        )
    if object_ids != sorted(object_ids, key=lambda x: (int(x), x)):
        raise AcquisitionError("batch-register object order is not ascending numeric NORAD_CAT_ID")

    grouped: dict[int, list[dict[str, str]]] = {}
    for row in rows:
        seq = int(row["batch_sequence"])
        grouped.setdefault(seq, []).append(row)

        object_id = row["norad_cat_id"].strip()
        sample = sample_objects[object_id]
        for field in (
            "conj_id",
            "final_g6_sample_index",
            "reserve_event_index",
            "g6_temporal_stratum",
            "reference_tca_utc",
            "object_role",
        ):
            if row[field].strip() != sample[field]:
                raise AcquisitionError(
                    f"batch register/sample mismatch for {object_id} field {field}"
                )

        tref = _parse_utc(row["reference_tca_utc"])
        lower = _parse_utc(row["object_lower_bound_utc"])
        if lower != tref - timedelta(days=180):
            raise AcquisitionError(f"180-day lower-bound mismatch for {object_id}")
        if row["server_orderby"].strip().upper() != "FALSE":
            raise AcquisitionError(f"batch register enables server ORDER BY for {object_id}")
        if row["local_causal_trim_rule"].strip() != LOCAL_CAUSAL_TRIM_RULE:
            raise AcquisitionError(f"causal trim rule changed for {object_id}")

    if set(grouped) != set(range(1, EXPECTED_BATCHES + 1)):
        raise AcquisitionError("batch sequences are not exactly 1..1386")

    specs: list[BatchSpec] = []
    for seq in range(1, EXPECTED_BATCHES + 1):
        batch_rows = grouped[seq]
        expected_count = FINAL_PARTIAL_BATCH_SIZE if seq == EXPECTED_BATCHES else PRODUCTION_BATCH_SIZE
        if len(batch_rows) != expected_count:
            raise AcquisitionError(
                f"batch {seq} has {len(batch_rows)} object rows, expected {expected_count}"
            )

        batch_id_values = {row["batch_id"] for row in batch_rows}
        raw_paths = {row["raw_relative_path"] for row in batch_rows}
        start_values = {row["batch_request_start_utc"] for row in batch_rows}
        end_values = {row["batch_request_end_exclusive_utc"] for row in batch_rows}
        count_values = {row["batch_object_count"] for row in batch_rows}
        if not all(len(values) == 1 for values in (batch_id_values, raw_paths, start_values, end_values, count_values)):
            raise AcquisitionError(f"batch {seq} metadata is internally inconsistent")
        if int(next(iter(count_values))) != expected_count:
            raise AcquisitionError(f"batch {seq} object-count metadata changed")

        ids = tuple(row["norad_cat_id"].strip() for row in batch_rows)
        expected_identity = hashlib.sha256(",".join(ids).encode("ascii")).hexdigest()[:10]
        expected_batch_id = f"main-prod-b{seq:04d}-{expected_identity}"
        batch_id = next(iter(batch_id_values))
        if batch_id != expected_batch_id:
            raise AcquisitionError(
                f"batch {seq} identity is {batch_id}, expected {expected_batch_id}"
            )

        start = _parse_utc(next(iter(start_values)))
        end = _parse_utc(next(iter(end_values)))
        expected_start = min(_parse_utc(row["object_lower_bound_utc"]) for row in batch_rows)
        expected_end = max(_parse_utc(row["reference_tca_utc"]) for row in batch_rows)
        if start != expected_start or end != expected_end:
            raise AcquisitionError(f"batch {seq} request-union bounds changed")

        raw_relative = Path(next(iter(raw_paths)))
        final_path = (ROOT / raw_relative).resolve()
        try:
            final_path.relative_to(RAW_DIR.resolve())
        except ValueError:
            raise AcquisitionError(f"batch {seq} raw path escapes data/raw/g6") from None

        specs.append(
            BatchSpec(
                sequence=seq,
                batch_id=batch_id,
                object_ids=ids,
                start_utc=start,
                end_exclusive_utc=end,
                final_path=final_path,
            )
        )

    if len({spec.batch_id for spec in specs}) != EXPECTED_BATCHES:
        raise AcquisitionError("batch IDs are not unique")
    if len({spec.final_path for spec in specs}) != EXPECTED_BATCHES:
        raise AcquisitionError("raw output paths are not unique")
    return specs, rows


def build_query_url(spec: BatchSpec) -> str:
    ids = ",".join(spec.object_ids)
    transport_start, transport_end_exclusive = _transport_bounds(spec)
    inclusive_end = transport_end_exclusive - timedelta(seconds=1)
    creation_value = (
        f"{_space_track_time_seconds(transport_start)}--"
        f"{_space_track_time_seconds(inclusive_end)}"
    )
    segments = [
        "basicspacedata",
        "query",
        "class",
        "gp_history",
        "NORAD_CAT_ID",
        ids,
        "CREATION_DATE",
        creation_value,
        "format",
        "csv",
        "emptyresult",
        "show",
    ]
    encoded = "/".join(urllib.parse.quote(part, safe=",") for part in segments)
    return f"{BASE_URL}/{encoded}"


def non_secret_predicate(spec: BatchSpec) -> str:
    return (
        f"class=gp_history; NORAD_CAT_ID={','.join(spec.object_ids)}; "
        f"CREATION_DATE_transport={_transport_period_text(spec)}; "
        f"exact_batch_union={spec.period_text}; format=csv; server_orderby=false"
    )


def _looks_like_html(prefix: bytes, content_type: str) -> bool:
    lowered = prefix.lstrip().lower()
    return "text/html" in content_type.lower() or lowered.startswith(
        (b"<!doctype html", b"<html")
    )


def _classify_no_result(prefix: bytes, content_type: str) -> bool:
    if _looks_like_html(prefix, content_type):
        raise AuthenticationFailure("Space-Track returned HTML/login content instead of CSV")
    return prefix.lstrip().upper().startswith(b"NO RESULTS RETURNED")


def validate_main_csv(path: Path, spec: BatchSpec) -> dict:
    requested = set(spec.object_ids)
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = tuple(reader.fieldnames or ())
        missing_fields = sorted(REQUIRED_GP_FIELDS - set(fields))
        if missing_fields:
            raise SchemaFailure(f"CSV schema missing required fields: {','.join(missing_fields)}")

        observed: set[str] = set()
        row_count = 0
        creation_parse_failures = 0
        epoch_parse_failures = 0
        missing_gp_id_values = 0
        repeated_epoch_counts: Counter = Counter()
        creation_values: list[datetime] = []
        epoch_values: list[datetime] = []

        for row in reader:
            row_count += 1
            object_id = row.get("NORAD_CAT_ID", "").strip()
            if not object_id:
                raise SchemaFailure("CSV contains a missing NORAD_CAT_ID")
            if object_id not in requested:
                raise SchemaFailure(f"CSV contains unrequested NORAD_CAT_ID {object_id}")
            observed.add(object_id)

            creation_text = row.get("CREATION_DATE", "").strip()
            epoch_text = row.get("EPOCH", "").strip()
            try:
                creation = _parse_utc(creation_text)
                creation_values.append(creation)
                transport_start, transport_end_exclusive = _transport_bounds(spec)
                if not (transport_start <= creation < transport_end_exclusive):
                    raise SchemaFailure(
                        "returned CREATION_DATE lies outside whole-second transport "
                        f"envelope for {object_id}: {creation_text}"
                    )
            except SchemaFailure:
                raise
            except Exception:
                creation_parse_failures += 1

            try:
                epoch_values.append(_parse_utc(epoch_text))
            except Exception:
                epoch_parse_failures += 1

            if not row.get("GP_ID", "").strip():
                missing_gp_id_values += 1
            repeated_epoch_counts[(object_id, epoch_text)] += 1

    if creation_parse_failures or epoch_parse_failures:
        raise SchemaFailure(
            f"CSV timestamp parse failures: creation={creation_parse_failures}, epoch={epoch_parse_failures}"
        )

    return {
        "columns": list(fields),
        "row_count": row_count,
        "unique_norad_cat_ids": sorted(observed, key=lambda value: (int(value), value)),
        "requested_objects_with_zero_rows": sorted(
            requested - observed, key=lambda value: (int(value), value)
        ),
        "first_creation_date": _iso(min(creation_values)) if creation_values else None,
        "last_creation_date": _iso(max(creation_values)) if creation_values else None,
        "first_epoch": _iso(min(epoch_values)) if epoch_values else None,
        "last_epoch": _iso(max(epoch_values)) if epoch_values else None,
        "gp_id_missing_value_count": missing_gp_id_values,
        "repeated_object_epoch_groups": sum(
            count > 1 for count in repeated_epoch_counts.values()
        ),
    }


def _load_frozen_three_batch_checkpoint() -> dict:
    """Verify immutable historical checkpoint evidence without reading live state/raw files."""
    if not THREE_BATCH_CHECKPOINT.is_file():
        raise AcquisitionError("frozen three-batch checkpoint JSON is missing")
    actual_sha256 = _sha256_file(THREE_BATCH_CHECKPOINT)
    if actual_sha256 != EXPECTED_THREE_BATCH_CHECKPOINT_SHA256:
        raise AcquisitionError(
            f"three-batch checkpoint SHA-256 mismatch: {actual_sha256}"
        )
    with THREE_BATCH_CHECKPOINT.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)

    expected_scalars = {
        "status": "PASS",
        "checkpoint": "G6_THREE_BATCH_TRANSPORT_CHECKPOINT",
        "state_records_total": 5,
        "unauthorized_batch_state_records": 0,
        "successful_production_batches": 3,
        "total_raw_rows": 11156,
        "completed_raw_csv_count": 3,
        "residual_partial_file_count": 0,
        "network_requests": 0,
    }
    for key, expected in expected_scalars.items():
        if payload.get(key) != expected:
            raise AcquisitionError(
                f"frozen three-batch checkpoint field {key} changed: {payload.get(key)!r}"
            )
    if payload.get("protected_outcome_access") is not False:
        raise AcquisitionError("frozen checkpoint protected-outcome boundary changed")

    batches = payload.get("batches")
    if not isinstance(batches, list) or [row.get("batch_sequence") for row in batches] != [1, 2, 3]:
        raise AcquisitionError("frozen checkpoint batch sequence changed")
    expected_chains = [
        ["FAILED", "FAILED", "SUCCESS"],
        ["SUCCESS"],
        ["SUCCESS"],
    ]
    actual_chains = [
        [attempt.get("status") for attempt in row.get("attempt_chain", [])]
        for row in batches
    ]
    if actual_chains != expected_chains:
        raise AcquisitionError(f"frozen checkpoint attempt chains changed: {actual_chains}")
    return payload


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
            "User-Agent": "ORI-G6-local-client/1.0",
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
    failed_markers = (b"login failed", b"invalid username", b"invalid password")
    if "/auth/login" in final_url or any(marker in lowered for marker in failed_markers):
        raise AuthenticationFailure("Space-Track authentication was rejected")
    if not cookie_jar:
        raise AuthenticationFailure(
            "Space-Track authentication returned no in-memory session cookie"
        )
    if _looks_like_html(payload, content_type) and b"login to space-track" in lowered:
        raise AuthenticationFailure(
            "Space-Track returned the login page after authentication"
        )
    return opener, cookie_jar


def stream_main_request_once(
    opener: urllib.request.OpenerDirector,
    spec: BatchSpec,
    timeout_s: float,
    *,
    request_start_guard=None,
) -> dict:
    if spec.final_path.exists():
        raise AcquisitionError(f"refusing to overwrite existing raw file: {spec.final_path}")

    PARTIAL_DIR.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{spec.final_path.name}.", suffix=".partial", dir=PARTIAL_DIR
    )
    temporary = Path(temp_name)
    os.close(fd)

    digest = hashlib.sha256()
    byte_count = 0
    prefix = b""
    retrieval_utc = _utc_now()

    request = urllib.request.Request(
        build_query_url(spec),
        headers={"User-Agent": "ORI-G6-local-client/1.0"},
    )

    try:
        try:
            if request_start_guard is not None:
                request_start_guard()
            with opener.open(request, timeout=timeout_s) as response, temporary.open(
                "wb"
            ) as output:
                status = getattr(response, "status", response.getcode())
                final_url = response.geturl().lower()
                content_type = response.headers.get("Content-Type", "")
                if status != 200:
                    raise TransportFailure(
                        f"Space-Track data request returned HTTP {status}"
                    )
                if "/auth/login" in final_url:
                    raise AuthenticationFailure(
                        "Space-Track data request redirected to the login page"
                    )

                explicit_no_result = False
                for chunk in iter(lambda: response.read(CHUNK_BYTES), b""):
                    if not prefix:
                        prefix = chunk[:4096]
                        explicit_no_result = _classify_no_result(prefix, content_type)
                    output.write(chunk)
                    digest.update(chunk)
                    byte_count += len(chunk)

                output.flush()
                os.fsync(output.fileno())

        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise ThrottledFailure(
                    "Space-Track returned HTTP 429; no retry was attempted"
                ) from None
            if exc.code in (401, 403):
                raise AuthenticationFailure(
                    f"Space-Track data request returned HTTP {exc.code}"
                ) from None
            raise TransportFailure(
                f"Space-Track data request returned HTTP {exc.code}"
            ) from None
        except (TimeoutError, socket.timeout):
            raise TransportFailure(
                f"Space-Track response timed out after {byte_count} streamed bytes; no retry was attempted"
            ) from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise TransportFailure(
                    f"Space-Track response timed out after {byte_count} streamed bytes; no retry was attempted"
                ) from None
            raise TransportFailure(
                "Space-Track transport failed before a complete response was retained"
            ) from None

        if byte_count == 0:
            raise TransportFailure("Space-Track returned zero bytes")

        if explicit_no_result:
            # Retain no text "NO RESULTS RETURNED" file as raw CSV evidence.
            return {
                "status": "SUCCESS_EMPTY_RESULT",
                "retrieval_utc": retrieval_utc,
                "byte_count": byte_count,
                "row_count": 0,
                "sha256": None,
                "raw_path": None,
                "unique_norad_cat_ids": [],
                "requested_objects_with_zero_rows": list(spec.object_ids),
            }

        stats = validate_main_csv(temporary, spec)

        spec.final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, spec.final_path)
        return {
            "status": "SUCCESS" if stats["row_count"] > 0 else "SUCCESS_EMPTY_RESULT",
            "retrieval_utc": retrieval_utc,
            "byte_count": byte_count,
            "row_count": stats["row_count"],
            "sha256": digest.hexdigest(),
            "raw_path": spec.final_path.relative_to(ROOT).as_posix(),
            **stats,
        }
    finally:
        temporary.unlink(missing_ok=True)


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"schema_version": 1, "records": []}
    with STATE_FILE.open("r", encoding="utf-8") as stream:
        state = json.load(stream)
    if state.get("schema_version") != 1 or not isinstance(state.get("records"), list):
        raise AcquisitionError("G6 acquisition-state schema is invalid")
    return state


def _save_state(state: dict) -> None:
    _atomic_write_json(STATE_FILE, state)


def _latest_state_record(state: dict, batch_id: str) -> dict | None:
    matches = [row for row in state["records"] if row.get("batch_id") == batch_id]
    return matches[-1] if matches else None


def _verified_success_file(record: dict) -> Path | None:
    if record.get("status") != "SUCCESS":
        return None
    raw_filename = record.get("raw_filename")
    expected_sha = record.get("sha256")
    if not raw_filename or not expected_sha:
        return None
    path = (ROOT / raw_filename).resolve()
    try:
        path.relative_to(RAW_DIR.resolve())
    except ValueError:
        raise AcquisitionError("acquisition state references a path outside data/raw/g6") from None
    if not path.is_file():
        return None
    if _sha256_file(path) != expected_sha:
        return None
    return path


def _spec_requires_network(
    state: dict, spec: BatchSpec, retry_failed_batch: str | None = None
) -> bool:
    prior = _latest_state_record(state, spec.batch_id)
    if prior is None:
        if retry_failed_batch == spec.batch_id:
            raise AcquisitionError(
                f"--retry-failed-batch targets {spec.batch_id}, but no prior attempt exists"
            )
        return True
    if prior.get("status") == "SUCCESS":
        if _verified_success_file(prior) is None:
            raise AcquisitionError(
                f"successful batch {spec.batch_id} failed local raw-file SHA-256 verification"
            )
        if retry_failed_batch == spec.batch_id:
            raise AcquisitionError(
                f"--retry-failed-batch targets {spec.batch_id}, but latest attempt succeeded"
            )
        return False
    if prior.get("status") == "SUCCESS_EMPTY_RESULT":
        if retry_failed_batch == spec.batch_id:
            raise AcquisitionError(
                f"--retry-failed-batch targets {spec.batch_id}, but latest attempt was an accepted empty result"
            )
        return False
    if retry_failed_batch == spec.batch_id:
        return True
    raise AcquisitionError(
        f"batch {spec.batch_id} has a prior non-success attempt; automatic retry is prohibited"
    )


def _record_state(state: dict, spec: BatchSpec, result: dict) -> None:
    returned_ids = result.get("unique_norad_cat_ids")
    record = {
        "batch_sequence": spec.sequence,
        "batch_id": spec.batch_id,
        "kind": "main",
        "status": result.get("status"),
        "retrieval_utc": result.get("retrieval_utc", _utc_now()),
        "requested_object_ids": list(spec.object_ids),
        "requested_object_count": len(spec.object_ids),
        "query_period": spec.period_text,
        "transport_query_period": _transport_period_text(spec),
        "non_secret_query_predicate": non_secret_predicate(spec),
        "timeout_seconds": DEFAULT_TIMEOUT_S,
        "server_orderby": False,
        "local_per_object_causal_trim": LOCAL_CAUSAL_TRIM_RULE,
        "stable_tie_rule": STABLE_TIE_RULE,
        "row_count": result.get("row_count"),
        "byte_count": result.get("byte_count"),
        "sha256": result.get("sha256"),
        "raw_filename": result.get("raw_path"),
        "returned_ids": returned_ids,
        "requested_objects_with_zero_rows": result.get(
            "requested_objects_with_zero_rows"
        ),
        "first_creation_date": result.get("first_creation_date"),
        "last_creation_date": result.get("last_creation_date"),
        "first_epoch": result.get("first_epoch"),
        "last_epoch": result.get("last_epoch"),
        "gp_id_missing_value_count": result.get("gp_id_missing_value_count"),
        "repeated_object_epoch_groups": result.get("repeated_object_epoch_groups"),
        "failure_class": result.get("failure_class"),
        "detail": result.get("detail"),
    }
    state["records"].append(record)
    _save_state(state)


def _failure_payload(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, AuthenticationFailure):
        return "AUTHENTICATION_FAILURE", str(exc)
    if isinstance(exc, ThrottledFailure):
        return "THROTTLED_FAILURE", str(exc)
    if isinstance(exc, SchemaFailure):
        return "SCHEMA_FAILURE", str(exc)
    if isinstance(exc, TransportFailure):
        return "TRANSPORT_FAILURE", str(exc)
    return "ACQUISITION_FAILURE", str(exc)


def offline_validate() -> dict:
    _verify_utc_parser_semantics()
    _verify_request_spacing_calculation()
    _verify_parent_approved_boundary()
    runtime_tag_target = _git_tag_target_if_exists(RUNTIME_APPROVAL_TAG)
    untagged_runtime_rejection_verified = False
    if runtime_tag_target is None:
        try:
            _verify_runtime_acquisition_authorization()
        except AcquisitionError:
            untagged_runtime_rejection_verified = True
        else:
            raise AcquisitionError("absent runtime approval tag was not rejected")
    _, sample_objects = _load_sample()
    _verify_g5_overlap(sample_objects)
    specs, register_rows = _load_batch_register(sample_objects)

    first_url = build_query_url(specs[0])
    if "gp_history" not in first_url or "format/csv" not in first_url:
        raise AcquisitionError("main query URL construction failed")
    if "orderby" in urllib.parse.unquote(first_url).lower():
        raise AcquisitionError("main production URL unexpectedly enables server ORDER BY")

    for spec in specs:
        transport_start, transport_end_exclusive = _transport_bounds(spec)
        if transport_start > spec.start_utc:
            raise AcquisitionError("transport lower bound does not enclose exact batch union")
        if transport_end_exclusive < spec.end_exclusive_utc:
            raise AcquisitionError("transport upper bound does not enclose exact batch union")
        if spec.start_utc - transport_start >= timedelta(seconds=1):
            raise AcquisitionError("transport lower-bound expansion is >= 1 second")
        if transport_end_exclusive - spec.end_exclusive_utc >= timedelta(seconds=1):
            raise AcquisitionError("transport upper-bound expansion is >= 1 second")

    checkpoint = _load_frozen_three_batch_checkpoint()

    if any(path.is_file() for path in RAW_DIR.glob("gp_history_g6_main_*.csv")) if RAW_DIR.exists() else False:
        raw_state = "EXISTING_RAW_FILES_PRESENT"
    else:
        raw_state = "NO_MAIN_RAW_FILES_PRESENT"

    payload = {
        "status": "PASS",
        "mode": "offline_validate",
        "parent_approved_tag": PARENT_APPROVED_TAG,
        "parent_approved_commit": PARENT_APPROVED_COMMIT,
        "parent_approved_tag_verified": True,
        "required_runtime_approval_tag": RUNTIME_APPROVAL_TAG,
        "runtime_approval_required_for_acquire_main": True,
        "runtime_approval_tag_present": runtime_tag_target is not None,
        "untagged_runtime_authorization_rejection_verified": (
            untagged_runtime_rejection_verified
        ),
        "approved_sample_sha256": EXPECTED_SAMPLE_SHA256,
        "approved_sample_sha_verified": True,
        "g5_allocation_sha256": EXPECTED_G5_ALLOCATION_SHA256,
        "g5_allocation_sha_verified": True,
        "g5_object_overlap": 0,
        "stratum_counts": EXPECTED_STRATA,
        "batch_register_sha256": EXPECTED_BATCH_REGISTER_SHA256,
        "batch_register_sha_verified": True,
        "sample_events": EXPECTED_EVENTS,
        "distinct_objects": EXPECTED_OBJECTS,
        "production_batch_size": PRODUCTION_BATCH_SIZE,
        "production_batch_count": len(specs),
        "full_ten_object_batches": sum(len(spec.object_ids) == 10 for spec in specs),
        "final_partial_batch_size": len(specs[-1].object_ids),
        "batch_register_object_coverage": f"{len(register_rows)}/{EXPECTED_OBJECTS}",
        "duplicate_objects": 0,
        "unexpected_objects": 0,
        "internal_object_disjointness": "PASS",
        "server_orderby": False,
        "timeout_seconds": DEFAULT_TIMEOUT_S,
        "automatic_retry": False,
        "explicit_retry_requires_named_failed_batch": True,
        "transport_creation_date_resolution": "whole-second envelope around exact frozen union",
        "spacetrack_timezone_less_datetime_interpretation": "UTC",
        "utc_parser_regression_verified": True,
        "max_transport_expansion_each_boundary_seconds": "<1",
        "max_new_main_batches_per_invocation": MAX_NEW_MAIN_BATCHES,
        "minimum_request_start_spacing_seconds": MIN_REQUEST_START_SPACING_S,
        "request_spacing_clock": "monotonic",
        "request_spacing_regression_verified": True,
        "local_causal_trim": LOCAL_CAUSAL_TRIM_RULE,
        "stable_tie_rule": STABLE_TIE_RULE,
        "predecessor_acquisition_implemented": False,
        "predecessor_boundary": "MAIN_HISTORY_AND_COVERAGE_AUDIT_REQUIRED_FIRST",
        "protected_outcome_access": False,
        "network_requests": 0,
        "raw_main_file_state": raw_state,
        "three_batch_checkpoint_status": checkpoint["status"],
        "three_batch_checkpoint_path": THREE_BATCH_CHECKPOINT.relative_to(ROOT).as_posix(),
        "three_batch_checkpoint_sha256": EXPECTED_THREE_BATCH_CHECKPOINT_SHA256,
        "g6_acquisition_implementation_status": "DR0012_APPROVED_FOR_COMMIT_TAG_NOT_EFFECTIVE_UNTIL_RUNTIME_TAG",
    }
    return payload


def _get_credentials() -> tuple[str, str]:
    username = input("Space-Track username: ").strip()
    if not username:
        raise AuthenticationFailure("empty Space-Track username")
    password = getpass.getpass("Space-Track password: ")
    if not password:
        raise AuthenticationFailure("empty Space-Track password")
    return username, password


def run_acquire_main(
    max_new_batches: int, retry_failed_batch: str | None = None
) -> int:
    if not 1 <= max_new_batches <= MAX_NEW_MAIN_BATCHES:
        raise AcquisitionError(
            f"--max-new-batches must be between 1 and {MAX_NEW_MAIN_BATCHES}"
        )
    if retry_failed_batch is not None and max_new_batches != 1:
        raise AcquisitionError(
            "an explicit failed-batch retry requires --max-new-batches 1"
        )

    _verify_parent_approved_boundary()
    _verify_runtime_acquisition_authorization()
    _, sample_objects = _load_sample()
    _verify_g5_overlap(sample_objects)
    specs, _ = _load_batch_register(sample_objects)
    state = _load_state()

    if retry_failed_batch is not None:
        matching = [spec for spec in specs if spec.batch_id == retry_failed_batch]
        if len(matching) != 1:
            raise AcquisitionError(
                f"unknown --retry-failed-batch value: {retry_failed_batch}"
            )
        spec = matching[0]
        if not _spec_requires_network(state, spec, retry_failed_batch):
            raise AcquisitionError(f"retry target {retry_failed_batch} does not require network")
        planned = [spec]
    else:
        planned: list[BatchSpec] = []
        for spec in specs:
            if _spec_requires_network(state, spec):
                planned.append(spec)
                if len(planned) == max_new_batches:
                    break

    if not planned:
        print(
            json.dumps(
                {
                    "status": "NO_NEW_MAIN_BATCHES_REQUIRED",
                    "gp_history_requests": 0,
                    "network_execution": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    print(
        json.dumps(
            {
                "status": "PREFLIGHT_PASS",
                "mode": "acquire_main",
                "planned_new_batches": [
                    {
                        "batch_sequence": spec.sequence,
                        "batch_id": spec.batch_id,
                        "requested_object_count": len(spec.object_ids),
                        "query_period": spec.period_text,
                        "transport_query_period": _transport_period_text(spec),
                        "raw_path": spec.final_path.relative_to(ROOT).as_posix(),
                    }
                    for spec in planned
                ],
                "timeout_seconds": DEFAULT_TIMEOUT_S,
                "minimum_request_start_spacing_seconds": MIN_REQUEST_START_SPACING_S,
                "request_spacing_clock": "monotonic",
                "automatic_retry": False,
                "explicit_retry_failed_batch": retry_failed_batch,
                "server_orderby": False,
            },
            indent=2,
            sort_keys=True,
        )
    )

    username = password = ""
    cookie_jar: http.cookiejar.CookieJar | None = None
    try:
        username, password = _get_credentials()
        opener, cookie_jar = authenticate(username, password, DEFAULT_TIMEOUT_S)
        password = ""

        completed = []
        previous_request_start: float | None = None

        def request_start_guard() -> None:
            nonlocal previous_request_start
            previous_request_start = _wait_for_request_start_spacing(
                previous_request_start
            )

        for spec in planned:
            try:
                result = stream_main_request_once(
                    opener,
                    spec,
                    DEFAULT_TIMEOUT_S,
                    request_start_guard=request_start_guard,
                )
            except (
                AuthenticationFailure,
                ThrottledFailure,
                TransportFailure,
                SchemaFailure,
                AcquisitionError,
            ) as exc:
                failure_class, detail = _failure_payload(exc)
                failure = {
                    "status": "FAILED",
                    "retrieval_utc": _utc_now(),
                    "row_count": None,
                    "byte_count": None,
                    "sha256": None,
                    "raw_path": None,
                    "failure_class": failure_class,
                    "detail": detail,
                }
                _record_state(state, spec, failure)
                raise
            else:
                _record_state(state, spec, result)
                completed.append(
                    {
                        "batch_sequence": spec.sequence,
                        "batch_id": spec.batch_id,
                        "status": result["status"],
                        "row_count": result["row_count"],
                        "raw_path": result.get("raw_path"),
                        "sha256": result.get("sha256"),
                    }
                )

        print(
            json.dumps(
                {
                    "status": "STAGED_G6_MAIN_ACQUISITION_COMPLETE",
                    "new_gp_history_requests": len(completed),
                    "minimum_request_start_spacing_seconds": MIN_REQUEST_START_SPACING_S,
                    "completed_batches": completed,
                    "state_file": STATE_FILE.relative_to(ROOT).as_posix(),
                    "next_boundary": "INDEPENDENT_REVIEW_BEFORE_ANY_FURTHER_STAGE",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    finally:
        username = ""
        password = ""
        if cookie_jar is not None:
            cookie_jar.clear()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Secure staged GP_HISTORY acquisition for the frozen G6 confirmatory sample."
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--offline-validate",
        action="store_true",
        help="perform deterministic local checks with zero network access",
    )
    modes.add_argument(
        "--acquire-main",
        action="store_true",
        help="explicitly execute a staged G6 main-history acquisition",
    )
    parser.add_argument(
        "--max-new-batches",
        type=int,
        help=f"required with --acquire-main; must be 1..{MAX_NEW_MAIN_BATCHES}",
    )
    parser.add_argument(
        "--retry-failed-batch",
        help=(
            "explicitly retry exactly one named batch whose latest state is FAILED; "
            "requires --acquire-main --max-new-batches 1"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.offline_validate:
        if args.max_new_batches is not None:
            raise AcquisitionError("--max-new-batches is not allowed with --offline-validate")
        if args.retry_failed_batch is not None:
            raise AcquisitionError("--retry-failed-batch is not allowed with --offline-validate")
        payload = offline_validate()
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.acquire_main:
        if args.max_new_batches is None:
            raise AcquisitionError("--acquire-main requires --max-new-batches")
        return run_acquire_main(args.max_new_batches, args.retry_failed_batch)

    raise AcquisitionError("no execution mode selected")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AcquisitionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
