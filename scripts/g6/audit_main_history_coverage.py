#!/usr/bin/env python3
"""Offline integrity and causal-coverage audit for retained G6 main histories.

Each retained main-history CSV is opened exactly once.  During that single
streaming pass the script hashes the preserved bytes, validates the CSV/state
provenance, and computes the exact per-object causal-window coverage.

This script performs no network access and does not implement predecessor
retrieval.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FINAL_SAMPLE = ROOT / "docs/research/G6_Final_Confirmatory_Sample_Register.csv"
BATCH_REGISTER = ROOT / "docs/research/G6_GPHistory_Production_Batch_Register.csv"
STATE_FILE = ROOT / "data/raw/g6/G6_SpaceTrack_Acquisition_State.json"
RAW_DIR = ROOT / "data/raw/g6"
ACQUISITION_CLIENT = ROOT / "scripts/g6/acquire_spacetrack_gp_history.py"

OUTPUT_AUDIT = ROOT / "results/g6/G6_Main_History_Coverage_Audit.csv"
OUTPUT_SUMMARY = ROOT / "results/g6/G6_Main_History_Coverage_Summary.json"
OUTPUT_REQUESTS = ROOT / "docs/research/G6_Predecessor_Request_Register.csv"

APPROVED_RUNTIME_COMMIT = "5cbe644a8efcbe7fe32a223079f7c10d270d68c2"
APPROVED_RUNTIME_TAG = "g6-main-acquisition-scaling-approved"
EXPECTED_SAMPLE_SHA256 = "f8a103cbd268e7b6d3e95515e8a806da403511196145897997866adc1fab10ef"
EXPECTED_BATCH_REGISTER_SHA256 = "02883a71d797b661b83acf13d03f6756b79c44c02fdb30f9410c7d3313ea832e"
EXPECTED_ACQUISITION_CLIENT_SHA256 = (
    "09234c71fdce429d4b04931ce2cc73308be79689f84b08df434745fb751de441"
)
EXPECTED_REQUEST_ID_SET_SHA256 = "976628343077b14cb7017493a08757ce93def34b5966f4b87688d6b106e46bee"

EXPECTED_EVENTS = 6_927
EXPECTED_OBJECTS = 13_854
EXPECTED_BATCHES = 1_386
EXPECTED_TOTAL_RAW_ROWS = 5_245_230
EXPECTED_MAIN_NONEMPTY = 13_854
EXPECTED_MAIN_EMPTY = 0
EXPECTED_CAPTURED_PREDECESSORS = 7_507
EXPECTED_REQUIRED_PREDECESSOR_QUERIES = 6_347
EXPECTED_ROWS_BEFORE_LOWER = 26_064
EXPECTED_ROWS_IN_MAIN_WINDOW = 5_153_040
EXPECTED_ROWS_AT_OR_AFTER_UPPER = 66_126
EXPECTED_PRELOWER_MISSING_GP_ID = 0

LOCAL_CAUSAL_TRIM_RULE = "[object_reference_tca_utc - 180 days, object_reference_tca_utc)"
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

AUDIT_FIELDS = (
    "NORAD_CAT_ID",
    "reference_tca_utc",
    "lower_bound_utc",
    "raw_rows",
    "rows_before_lower",
    "main_window_rows",
    "rows_at_or_after_upper",
    "earliest_creation_date_in_main_window",
    "latest_creation_date_in_main_window",
    "predecessor_status",
    "selected_predecessor_GP_ID",
    "selected_predecessor_CREATION_DATE",
    "selected_predecessor_EPOCH",
    "selected_predecessor_source_raw_path",
    "selected_predecessor_source_raw_sha256",
    "main_window_status",
    "hold_status",
)

REQUEST_FIELDS = (
    "request_sequence",
    "request_identity",
    "NORAD_CAT_ID",
    "reference_tca_utc",
    "lower_bound_utc",
    "predecessor_status",
    "selection_rule",
    "network_retrieval_authorized",
)


class AuditError(RuntimeError):
    """Raised when frozen evidence or a required census invariant fails."""


@dataclass(frozen=True)
class BatchExpectation:
    sequence: int
    batch_id: str
    object_ids: tuple[str, ...]
    start_utc: datetime
    end_exclusive_utc: datetime
    raw_path: Path


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


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _verified_bytes(path: Path, expected_sha256: str) -> bytes:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise AuditError(f"cannot read required frozen evidence: {path}") from exc
    actual = _sha256_bytes(payload)
    if actual != expected_sha256:
        raise AuditError(f"SHA-256 mismatch for {path}: {actual}")
    return payload


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
        raise AuditError(f"cannot resolve git reference {name}") from exc
    return completed.stdout.strip()


def _verify_approved_boundary() -> None:
    tag_target = _git_rev_parse(
        f"refs/tags/{APPROVED_RUNTIME_TAG}^{{commit}}"
    )
    if tag_target != APPROVED_RUNTIME_COMMIT:
        raise AuditError(
            f"approved runtime tag resolves to {tag_target}, "
            f"expected {APPROVED_RUNTIME_COMMIT}"
        )
    try:
        completed = subprocess.run(
            ["git", "merge-base", "--is-ancestor", APPROVED_RUNTIME_COMMIT, "HEAD"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise AuditError("cannot verify the approved runtime commit ancestry") from exc
    if completed.returncode == 1:
        raise AuditError("approved runtime commit is not an ancestor of current HEAD")
    if completed.returncode != 0:
        raise AuditError(
            "git merge-base ancestry verification failed: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )


def _load_sample() -> dict[str, datetime]:
    payload = _verified_bytes(FINAL_SAMPLE, EXPECTED_SAMPLE_SHA256)
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""))
    fields = tuple(reader.fieldnames or ())
    if PROTECTED_FIELD_NAMES & {field.lower() for field in fields}:
        raise AuditError("frozen sample unexpectedly exposes a protected outcome field")

    object_tca: dict[str, datetime] = {}
    event_ids: set[str] = set()
    row_count = 0
    for row in reader:
        row_count += 1
        event_id = row["conj_id"].strip()
        if not event_id or event_id in event_ids:
            raise AuditError("frozen sample conjunction identifiers are not unique")
        event_ids.add(event_id)
        tca = _parse_utc(row["reference_tca_utc"])
        for role in ("obj1", "obj2"):
            object_id = row[role].strip()
            if not object_id.isdigit() or object_id in object_tca:
                raise AuditError(f"invalid or reused frozen object identifier: {object_id!r}")
            object_tca[object_id] = tca

    if row_count != EXPECTED_EVENTS or len(object_tca) != EXPECTED_OBJECTS:
        raise AuditError(
            f"frozen sample has {row_count} events and {len(object_tca)} objects"
        )
    return object_tca


def _load_batch_register(object_tca: dict[str, datetime]) -> list[BatchExpectation]:
    payload = _verified_bytes(BATCH_REGISTER, EXPECTED_BATCH_REGISTER_SHA256)
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""))
    fields = tuple(reader.fieldnames or ())
    if PROTECTED_FIELD_NAMES & {field.lower() for field in fields}:
        raise AuditError("batch register unexpectedly exposes a protected outcome field")
    rows = list(reader)
    if len(rows) != EXPECTED_OBJECTS:
        raise AuditError(f"batch register has {len(rows)} rows instead of {EXPECTED_OBJECTS}")

    register_ids = [row["norad_cat_id"].strip() for row in rows]
    if register_ids != sorted(register_ids, key=lambda value: (int(value), value)):
        raise AuditError("batch register object order is not ascending numeric NORAD_CAT_ID")
    if len(set(register_ids)) != EXPECTED_OBJECTS or set(register_ids) != set(object_tca):
        raise AuditError("batch-register membership differs from the frozen sample")

    grouped: dict[int, list[dict[str, str]]] = {}
    for row in rows:
        object_id = row["norad_cat_id"].strip()
        tca = _parse_utc(row["reference_tca_utc"])
        lower = _parse_utc(row["object_lower_bound_utc"])
        if tca != object_tca[object_id] or lower != tca - timedelta(days=180):
            raise AuditError(f"frozen TCA/lower-bound mismatch for object {object_id}")
        if row["server_orderby"].strip().upper() != "FALSE":
            raise AuditError(f"server ORDER BY changed for object {object_id}")
        if row["local_causal_trim_rule"].strip() != LOCAL_CAUSAL_TRIM_RULE:
            raise AuditError(f"causal trim rule changed for object {object_id}")
        if row["planned_status"].strip() != "PLANNED_NOT_ACQUIRED":
            raise AuditError(f"frozen planned status changed for object {object_id}")
        grouped.setdefault(int(row["batch_sequence"]), []).append(row)

    if set(grouped) != set(range(1, EXPECTED_BATCHES + 1)):
        raise AuditError("batch sequences are not exactly 1..1386")

    batches: list[BatchExpectation] = []
    assigned: list[str] = []
    for sequence in range(1, EXPECTED_BATCHES + 1):
        batch_rows = grouped[sequence]
        expected_count = 4 if sequence == EXPECTED_BATCHES else 10
        if len(batch_rows) != expected_count:
            raise AuditError(f"batch {sequence} object count is {len(batch_rows)}")

        ids = tuple(row["norad_cat_id"].strip() for row in batch_rows)
        assigned.extend(ids)
        identity = hashlib.sha256(",".join(ids).encode("ascii")).hexdigest()[:10]
        expected_batch_id = f"main-prod-b{sequence:04d}-{identity}"
        expected_start = min(object_tca[object_id] - timedelta(days=180) for object_id in ids)
        expected_end = max(object_tca[object_id] for object_id in ids)
        expected_raw = f"data/raw/g6/gp_history_g6_main_b{sequence:04d}_{ids[0]}_{ids[-1]}.csv"

        invariant_sets = {
            "batch_id": {row["batch_id"].strip() for row in batch_rows},
            "batch_object_count": {row["batch_object_count"].strip() for row in batch_rows},
            "batch_request_start_utc": {
                row["batch_request_start_utc"].strip() for row in batch_rows
            },
            "batch_request_end_exclusive_utc": {
                row["batch_request_end_exclusive_utc"].strip() for row in batch_rows
            },
            "raw_relative_path": {row["raw_relative_path"].strip() for row in batch_rows},
        }
        if any(len(values) != 1 for values in invariant_sets.values()):
            raise AuditError(f"batch {sequence} register metadata is internally inconsistent")
        if (
            invariant_sets["batch_id"] != {expected_batch_id}
            or invariant_sets["batch_object_count"] != {str(expected_count)}
            or invariant_sets["batch_request_start_utc"] != {_iso(expected_start)}
            or invariant_sets["batch_request_end_exclusive_utc"] != {_iso(expected_end)}
            or invariant_sets["raw_relative_path"] != {expected_raw}
        ):
            raise AuditError(f"batch {sequence} frozen identity or request bounds changed")

        raw_path = (ROOT / expected_raw).resolve()
        try:
            raw_path.relative_to(RAW_DIR.resolve())
        except ValueError:
            raise AuditError(f"batch {sequence} raw path escapes data/raw/g6") from None
        batches.append(
            BatchExpectation(
                sequence=sequence,
                batch_id=expected_batch_id,
                object_ids=ids,
                start_utc=expected_start,
                end_exclusive_utc=expected_end,
                raw_path=raw_path,
            )
        )

    if len(assigned) != EXPECTED_OBJECTS or len(set(assigned)) != EXPECTED_OBJECTS:
        raise AuditError("production batches do not partition the frozen objects exactly once")
    return batches


def _load_state(batches: list[BatchExpectation]) -> tuple[dict[str, dict], str, int]:
    try:
        payload = STATE_FILE.read_bytes()
        state = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditError("cannot load the retained G6 acquisition state") from exc
    records = state.get("records")
    if state.get("schema_version") != 1 or not isinstance(records, list):
        raise AuditError("G6 acquisition-state schema is invalid")

    by_id = {batch.batch_id: batch for batch in batches}
    successes: list[dict] = []
    records_by_batch: dict[str, list[dict]] = {}
    for record in records:
        batch_id = record.get("batch_id")
        batch = by_id.get(batch_id)
        if batch is None or record.get("kind") != "main":
            raise AuditError(f"unauthorized or unknown acquisition-state record: {batch_id!r}")
        if record.get("batch_sequence") != batch.sequence:
            raise AuditError(f"state sequence mismatch for {batch_id}")
        if tuple(record.get("requested_object_ids") or ()) != batch.object_ids:
            raise AuditError(f"state requested-object membership mismatch for {batch_id}")
        if record.get("status") not in {"FAILED", "SUCCESS"}:
            raise AuditError(f"unexpected state status for {batch_id}: {record.get('status')}")
        records_by_batch.setdefault(batch_id, []).append(record)
        if record.get("status") == "SUCCESS":
            successes.append(record)

    counts = Counter(record["batch_id"] for record in successes)
    if len(successes) != EXPECTED_BATCHES or set(counts) != set(by_id):
        raise AuditError("state does not contain exactly one success for every production batch")
    if any(count != 1 for count in counts.values()):
        raise AuditError("state contains duplicate successful production-batch records")
    for batch_id, attempts in records_by_batch.items():
        if attempts[-1].get("status") != "SUCCESS":
            raise AuditError(f"latest acquisition-state record is not successful for {batch_id}")
        for attempt in attempts:
            if attempt.get("status") == "FAILED" and attempt.get("raw_filename"):
                raise AuditError(f"failed state record unexpectedly names retained raw evidence: {batch_id}")

    return ({record["batch_id"]: record for record in successes}, _sha256_bytes(payload), len(records))


def _verify_raw_file_set(batches: list[BatchExpectation]) -> None:
    expected = {batch.raw_path.name for batch in batches}
    actual = {
        path.name
        for path in RAW_DIR.glob("gp_history_g6_main_b*.csv")
        if path.is_file()
    }
    if actual != expected or len(actual) != EXPECTED_BATCHES:
        raise AuditError(
            "retained main-history file set mismatch: "
            f"missing={len(expected - actual)}, unexpected={len(actual - expected)}"
        )


def _new_object_stats() -> dict:
    return {
        "raw_rows": 0,
        "rows_before_lower": 0,
        "main_window_rows": 0,
        "rows_at_or_after_upper": 0,
        "earliest_main": None,
        "latest_main": None,
        "predecessor_key": None,
        "predecessor_gp_id": "",
        "predecessor_creation": None,
        "predecessor_epoch": None,
        "predecessor_source_path": "",
        "source_files": set(),
    }


def _canonical_record_sha256(row: dict[str, str], fieldnames: tuple[str, ...]) -> str:
    canonical = json.dumps(
        {field: row.get(field, "") for field in fieldnames},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _match_frozen_request_set_hash(numeric_ids: list[str]) -> tuple[str, str]:
    expected_order = sorted(numeric_ids, key=lambda value: (int(value), value))
    if numeric_ids != expected_order:
        raise AuditError("predecessor request IDs are not in ascending numeric order")
    payload = ("\n".join(numeric_ids) + "\n").encode("ascii")
    digest = hashlib.sha256(payload).hexdigest()
    if digest != EXPECTED_REQUEST_ID_SET_SHA256:
        raise AuditError(
            f"frozen predecessor request-set SHA-256 mismatch: {digest}"
        )
    return digest, "ascending_numeric;LF;terminal_newline"


def _stream_raw_once(
    batch: BatchExpectation,
    state_record: dict,
    object_tca: dict[str, datetime],
    object_stats: dict[str, dict],
    failures: dict[str, int],
) -> tuple[int, int, str, int]:
    if not batch.raw_path.is_file():
        raise AuditError(f"missing retained raw file: {batch.raw_path}")
    digest = hashlib.sha256()
    byte_count = 0

    def decoded_lines(stream):
        nonlocal byte_count
        first = True
        for line in stream:
            digest.update(line)
            byte_count += len(line)
            if first:
                first = False
                yield line.decode("utf-8-sig")
            else:
                yield line.decode("utf-8")

    row_count = 0
    gp_id_missing_count = 0
    observed_ids: set[str] = set()
    expected_ids = set(batch.object_ids)
    relative_path = batch.raw_path.relative_to(ROOT).as_posix()
    with batch.raw_path.open("rb") as raw_stream:
        reader = csv.DictReader(decoded_lines(raw_stream))
        fieldnames = tuple(reader.fieldnames or ())
        missing_fields = REQUIRED_GP_FIELDS - set(fieldnames)
        if missing_fields:
            failures["required_schema_missing_fields"] += len(missing_fields)
            raise AuditError(
                f"{batch.raw_path.name} lacks fields: {','.join(sorted(missing_fields))}"
            )
        if PROTECTED_FIELD_NAMES & {field.lower() for field in fieldnames}:
            raise AuditError(f"{batch.raw_path.name} exposes a protected outcome field")

        for row in reader:
            row_count += 1
            object_id = (row.get("NORAD_CAT_ID") or "").strip()
            if object_id not in expected_ids:
                raise AuditError(
                    f"{batch.raw_path.name} contains unexpected object {object_id!r}"
                )
            observed_ids.add(object_id)
            stats = object_stats[object_id]
            stats["raw_rows"] += 1
            stats["source_files"].add(relative_path)

            creation_text = (row.get("CREATION_DATE") or "").strip()
            epoch_text = (row.get("EPOCH") or "").strip()
            gp_id = (row.get("GP_ID") or "").strip()
            try:
                creation = _parse_utc(creation_text)
            except (TypeError, ValueError):
                failures["creation_date_parse_failures"] += 1
                continue
            try:
                epoch = _parse_utc(epoch_text)
            except (TypeError, ValueError):
                failures["epoch_parse_failures"] += 1
                continue
            if not gp_id:
                gp_id_missing_count += 1
                failures["gp_id_missing_values"] += 1

            upper = object_tca[object_id]
            lower = upper - timedelta(days=180)
            if creation < lower:
                stats["rows_before_lower"] += 1
                if not gp_id:
                    failures["prelower_rows_with_missing_gp_id"] += 1
                stable_tie_key = gp_id or _canonical_record_sha256(row, fieldnames)
                candidate = (creation, epoch, stable_tie_key)
                if stats["predecessor_key"] is None or candidate > stats["predecessor_key"]:
                    stats["predecessor_key"] = candidate
                    stats["predecessor_gp_id"] = gp_id
                    stats["predecessor_creation"] = creation
                    stats["predecessor_epoch"] = epoch
                    stats["predecessor_source_path"] = relative_path
            elif creation < upper:
                stats["main_window_rows"] += 1
                if stats["earliest_main"] is None or creation < stats["earliest_main"]:
                    stats["earliest_main"] = creation
                if stats["latest_main"] is None or creation > stats["latest_main"]:
                    stats["latest_main"] = creation
            else:
                stats["rows_at_or_after_upper"] += 1

    if observed_ids != expected_ids:
        raise AuditError(
            f"{batch.raw_path.name} object coverage mismatch; "
            f"missing={sorted(expected_ids - observed_ids, key=int)}"
        )

    actual_sha = digest.hexdigest()
    if (
        state_record.get("raw_filename") != relative_path
        or state_record.get("sha256") != actual_sha
        or state_record.get("byte_count") != byte_count
        or state_record.get("row_count") != row_count
        or tuple(state_record.get("requested_object_ids") or ()) != batch.object_ids
        or set(state_record.get("returned_ids") or ()) != expected_ids
        or state_record.get("requested_objects_with_zero_rows") != []
        or state_record.get("gp_id_missing_value_count") != gp_id_missing_count
        or state_record.get("server_orderby") is not False
        or state_record.get("local_per_object_causal_trim") != LOCAL_CAUSAL_TRIM_RULE
        or state_record.get("stable_tie_rule") != STABLE_TIE_RULE
    ):
        raise AuditError(f"raw/state provenance mismatch for {batch.batch_id}")
    return row_count, byte_count, actual_sha, gp_id_missing_count


def _atomic_write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def audit() -> dict:
    _verify_approved_boundary()
    _verified_bytes(ACQUISITION_CLIENT, EXPECTED_ACQUISITION_CLIENT_SHA256)
    object_tca = _load_sample()
    batches = _load_batch_register(object_tca)
    _verify_raw_file_set(batches)
    state_by_batch, state_sha256, state_records_total = _load_state(batches)

    object_stats = {object_id: _new_object_stats() for object_id in object_tca}
    failures = {
        "required_schema_missing_fields": 0,
        "creation_date_parse_failures": 0,
        "epoch_parse_failures": 0,
        "gp_id_missing_values": 0,
        "prelower_rows_with_missing_gp_id": 0,
    }
    raw_sha_by_path: dict[str, str] = {}
    total_raw_rows = 0
    total_raw_bytes = 0
    for batch in batches:
        rows, byte_count, raw_sha, _ = _stream_raw_once(
            batch,
            state_by_batch[batch.batch_id],
            object_tca,
            object_stats,
            failures,
        )
        total_raw_rows += rows
        total_raw_bytes += byte_count
        raw_sha_by_path[batch.raw_path.relative_to(ROOT).as_posix()] = raw_sha

    if failures["required_schema_missing_fields"]:
        raise AuditError(f"required-schema failures: {failures}")
    if failures["creation_date_parse_failures"] or failures["epoch_parse_failures"]:
        raise AuditError(f"timestamp parse failures: {failures}")
    if any(len(stats["source_files"]) != 1 for stats in object_stats.values()):
        raise AuditError("one or more frozen objects are not represented in exactly one raw file")

    audit_rows: list[dict] = []
    request_rows: list[dict] = []
    before_total = main_total = after_total = 0
    main_nonempty = main_empty = captured = requires_query = 0
    for object_id in sorted(object_tca, key=lambda value: (int(value), value)):
        tca = object_tca[object_id]
        lower = tca - timedelta(days=180)
        stats = object_stats[object_id]
        before_total += stats["rows_before_lower"]
        main_total += stats["main_window_rows"]
        after_total += stats["rows_at_or_after_upper"]

        if stats["rows_before_lower"]:
            predecessor_status = "CAPTURED_IN_BATCH_UNION"
            captured += 1
        else:
            predecessor_status = "REQUIRES_DEDICATED_PREDECESSOR_QUERY"
            requires_query += 1
            request_rows.append(
                {
                    "request_sequence": requires_query,
                    "request_identity": f"predecessor-{object_id}",
                    "NORAD_CAT_ID": object_id,
                    "reference_tca_utc": _iso(tca),
                    "lower_bound_utc": _iso(lower),
                    "predecessor_status": predecessor_status,
                    "selection_rule": STABLE_TIE_RULE,
                    "network_retrieval_authorized": "FALSE",
                }
            )

        if stats["main_window_rows"]:
            main_window_status = "MAIN_WINDOW_PRESENT"
            hold_status = ""
            main_nonempty += 1
        else:
            main_window_status = "MAIN_WINDOW_EMPTY"
            hold_status = "HOLD"
            main_empty += 1

        predecessor_source_path = stats["predecessor_source_path"]
        audit_rows.append(
            {
                "NORAD_CAT_ID": object_id,
                "reference_tca_utc": _iso(tca),
                "lower_bound_utc": _iso(lower),
                "raw_rows": stats["raw_rows"],
                "rows_before_lower": stats["rows_before_lower"],
                "main_window_rows": stats["main_window_rows"],
                "rows_at_or_after_upper": stats["rows_at_or_after_upper"],
                "earliest_creation_date_in_main_window": (
                    _iso(stats["earliest_main"]) if stats["earliest_main"] else ""
                ),
                "latest_creation_date_in_main_window": (
                    _iso(stats["latest_main"]) if stats["latest_main"] else ""
                ),
                "predecessor_status": predecessor_status,
                "selected_predecessor_GP_ID": stats["predecessor_gp_id"],
                "selected_predecessor_CREATION_DATE": (
                    _iso(stats["predecessor_creation"])
                    if stats["predecessor_creation"]
                    else ""
                ),
                "selected_predecessor_EPOCH": (
                    _iso(stats["predecessor_epoch"]) if stats["predecessor_epoch"] else ""
                ),
                "selected_predecessor_source_raw_path": predecessor_source_path,
                "selected_predecessor_source_raw_sha256": (
                    raw_sha_by_path[predecessor_source_path]
                    if predecessor_source_path
                    else ""
                ),
                "main_window_status": main_window_status,
                "hold_status": hold_status,
            }
        )

    request_ids = [row["NORAD_CAT_ID"] for row in request_rows]
    request_id_set_sha256, request_id_hash_serialization = _match_frozen_request_set_hash(
        request_ids
    )
    actual_invariants = {
        "objects": len(audit_rows),
        "total_raw_rows": total_raw_rows,
        "main_nonempty": main_nonempty,
        "main_empty": main_empty,
        "captured": captured,
        "requires_query": requires_query,
        "rows_before_lower": before_total,
        "main_window_rows": main_total,
        "rows_at_or_after_upper": after_total,
        "prelower_missing_gp_id": failures["prelower_rows_with_missing_gp_id"],
        "request_id_set_sha256": request_id_set_sha256,
    }
    expected_invariants = {
        "objects": EXPECTED_OBJECTS,
        "total_raw_rows": EXPECTED_TOTAL_RAW_ROWS,
        "main_nonempty": EXPECTED_MAIN_NONEMPTY,
        "main_empty": EXPECTED_MAIN_EMPTY,
        "captured": EXPECTED_CAPTURED_PREDECESSORS,
        "requires_query": EXPECTED_REQUIRED_PREDECESSOR_QUERIES,
        "rows_before_lower": EXPECTED_ROWS_BEFORE_LOWER,
        "main_window_rows": EXPECTED_ROWS_IN_MAIN_WINDOW,
        "rows_at_or_after_upper": EXPECTED_ROWS_AT_OR_AFTER_UPPER,
        "prelower_missing_gp_id": EXPECTED_PRELOWER_MISSING_GP_ID,
        "request_id_set_sha256": EXPECTED_REQUEST_ID_SET_SHA256,
    }
    if actual_invariants != expected_invariants:
        raise AuditError(
            "frozen predecessor census mismatch: "
            f"actual={actual_invariants}, expected={expected_invariants}"
        )

    summary = {
        "integrity_status": "PASS",
        "approved_runtime_commit": APPROVED_RUNTIME_COMMIT,
        "approved_runtime_tag": APPROVED_RUNTIME_TAG,
        "final_sample_sha256": EXPECTED_SAMPLE_SHA256,
        "batch_register_sha256": EXPECTED_BATCH_REGISTER_SHA256,
        "acquisition_state_sha256": state_sha256,
        "acquisition_state_records_total": state_records_total,
        "expected_raw_files": EXPECTED_BATCHES,
        "verified_raw_files": len(raw_sha_by_path),
        "verified_successful_state_records": len(state_by_batch),
        "unique_objects": len(audit_rows),
        "total_raw_rows": total_raw_rows,
        "total_raw_bytes": total_raw_bytes,
        "MAIN_WINDOW_NONEMPTY_count": main_nonempty,
        "MAIN_WINDOW_EMPTY_count": main_empty,
        "objects_with_predecessor_captured_in_batch_union": captured,
        "objects_requiring_dedicated_predecessor_query": requires_query,
        "rows_before_object_lower_bound": before_total,
        "rows_inside_exact_main_windows": main_total,
        "rows_at_or_after_object_upper_bound": after_total,
        "prelower_rows_with_missing_GP_ID": failures["prelower_rows_with_missing_gp_id"],
        "creation_date_parse_failures": failures["creation_date_parse_failures"],
        "epoch_parse_failures": failures["epoch_parse_failures"],
        "GP_ID_missing_values": failures["gp_id_missing_values"],
        "predecessor_request_register_rows": len(request_rows),
        "predecessor_request_id_set_sha256": request_id_set_sha256,
        "predecessor_request_id_hash_serialization": request_id_hash_serialization,
        "raw_file_scan_count": len(raw_sha_by_path),
        "raw_file_scan_policy": "EXACTLY_ONCE_STREAMING_HASH_AND_AUDIT",
        "network_requests": 0,
        "protected_outcome_access": False,
        "predecessor_network_retrieval_implemented": False,
    }

    _atomic_write_csv(OUTPUT_AUDIT, AUDIT_FIELDS, audit_rows)
    _atomic_write_csv(OUTPUT_REQUESTS, REQUEST_FIELDS, request_rows)
    _atomic_write_json(OUTPUT_SUMMARY, summary)
    return summary


def main() -> int:
    try:
        summary = audit()
    except (AuditError, KeyError, ValueError) as exc:
        print(f"INTEGRITY FAIL: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
