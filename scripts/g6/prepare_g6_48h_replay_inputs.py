#!/usr/bin/env python3
"""Prepare frozen G6 publication-driven 48 h replay inputs.

Modes
-----
--offline-validate
    Zero network, zero Space-Track raw reads, zero protected-outcome reads,
    zero propagation, zero reconstruction.

--prepare-inputs
    Future explicitly authorized mode. It will:
      1. stream/hash all 1,386 frozen main GP_HISTORY files;
      2. select each object's current GP at t_ref - 48 h;
      3. define event replay time as the later selected CREATION_DATE;
      4. reselect both objects at replay time in a second deterministic pass;
      5. require unchanged GP identities;
      6. write a compact local-only TLE cache plus a non-protected selection
         manifest.

This module deliberately performs no orbital propagation or G6 outcome
evaluation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

EXPECTED_HEAD = (
    "8b108a2585f37d35c33b4f38ee3e18ed81c8256b"
)

FREEZE_TAG = (
    "g6-causal-history-manifest-frozen"
)

LSTAR_HOURS = 48

EXPECTED_EVENTS = 6_927
EXPECTED_OBJECTS = 13_854
EXPECTED_MAIN_FILES = 1_386

EXPECTED_MAIN_WINDOW_ROWS = 5_153_040

EXPECTED_CAPTURED_PREDECESSORS = 7_507
EXPECTED_QUERY_PREDECESSORS = 5_032
EXPECTED_LEFT_CENSORED = 1_315

EXPECTED_G4_SETTINGS = {
    "coarse_step_s": 10.0,
    "time_tolerance_s": 0.01,
    "dedup_time_tolerance_s": 0.01,
    "dedup_distance_tolerance_km": 1.0e-6,
    "delta_assoc_min": 5.0,
}

EXPECTED_PACKAGES = {
    "numpy": "2.3.5",
    "scipy": "1.18.1",
    "sgp4": "2.27",
}

FROZEN_SMALL_INPUTS = {
    ROOT / "docs/research/G6_Final_Confirmatory_Sample_Register.csv":
        "f8a103cbd268e7b6d3e95515e8a806da403511196145897997866adc1fab10ef",

    ROOT / "results/g6/G6_Final_Causal_History_Audit.csv":
        "41802759162ef4889b071aa6f15391d48a7a96cce7ededdd99c8e1d8a3ebc924",

    ROOT / "results/g6/G6_Final_Causal_History_Summary.json":
        "49665cc82e109d0d65e85140c172f3245c2cca4c3c7f2c1ed5c6c21dcb436767",

    ROOT / "results/g6/G6_Causal_History_Manifest_Review.json":
        "25765151b142c6740052b9e94df426400a609f6c8d86f3e45761dd19d1c28f53",

    ROOT / "data/raw/g6/G6_SpaceTrack_Acquisition_State.json":
        "e108db07530c58464fca223cdd2c5a54d850d23110edfd120e7b440f90004149",

    ROOT / "data/raw/g6/G6_SpaceTrack_Predecessor_Acquisition_State.json":
        "0c56f69f0fd3d396c5bddcb2ca863af52e7c34694b1ba81185d17feffebf709e",

    ROOT / "docs/research/G6_PreOutcome_Confirmatory_Lock.md":
        "21fc2d4eb83b8d00d7bbb632986fcdc33ec437dce9ca3e69020b222d382adb2e",

    ROOT / "scripts/g4/numerics.py":
        "dc8de410b89e05ffd829563b4996b06703c31236e9a10fb56bd7f4e690048406",

    ROOT / "results/g4/G4_Run_Summary.json":
        "d0a0a4f159a9a7eeb9763263f7a66c14b1c76bf07a2149027d17c5916ce22756",

    ROOT / "scripts/g5/select_lstar_controlflow_amended.py":
        "553578f4e90b044c6a783209a756ea3accc009ecbb7a09853e8cae46503f33c2",

    ROOT / "scripts/g5/run_g5_calibration_v2.py":
        "2ceae52cd0e4ffa6c1e9dea2df38c49d2c4bb983e7dfe7f8cdbc8dbeaf202c0d",

    ROOT / "docs/research/G5_LStar_ReplayTime_Amendment.md":
        "c23dad07a9458eb40720e9d63bd277b528cb3f7c106272460f0d17a31dc66f34",

    ROOT / "docs/research/G5_LStar_Final_PreCalibration_Closure.md":
        "424b1a6fd83d591049ec982e0c744e2fb91d34b48dc63221f68a2f377b9f9a42",

    ROOT / "results/g5/G5_LStar_ControlFlow_Equivalence_Audit.txt":
        "046d5c6a114f2022ca1ea2bafe4f095b87cf31451abeddb4f1dc198c17a7e5e4",

    ROOT / "docs/research/G5_LStar_Numerical_ControlFlow_Amendment.md":
        "3e1c80ad906816bdc704a76c7c4ebdf2b5003c82f994399412bd71eb7889c50a",
}

SAMPLE = (
    ROOT
    / "docs/research/G6_Final_Confirmatory_Sample_Register.csv"
)

MANIFEST = (
    ROOT
    / "results/g6/G6_Final_Causal_History_Audit.csv"
)

MAIN_STATE = (
    ROOT
    / "data/raw/g6/G6_SpaceTrack_Acquisition_State.json"
)

PRED_STATE = (
    ROOT
    / "data/raw/g6/G6_SpaceTrack_Predecessor_Acquisition_State.json"
)

G4_SUMMARY = (
    ROOT
    / "results/g4/G4_Run_Summary.json"
)

RAW_DIR = (
    ROOT
    / "data/raw/g6"
)

# Local-only cache contains selected TLE material and remains under raw/.
LOCAL_CACHE = (
    RAW_DIR
    / "G6_48H_Replay_Selected_GP_Cache.jsonl"
)

# Tracked/public-safe metadata: no TLE lines and no protected outcomes.
SELECTION_OUT = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Selection.csv"
)

SUMMARY_OUT = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Summary.json"
)

VALIDATION_OUT = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Implementation_Offline_Validation.json"
)


SELECTION_FIELDS = (
    "final_g6_sample_index",
    "reserve_event_index",
    "g6_temporal_stratum",
    "conj_id",
    "NORAD_CAT_ID",
    "object_role",
    "reference_tca_utc",
    "nominal_cutoff_utc",
    "replay_time_utc",
    "selection_status",
    "cutoff_GP_ID",
    "cutoff_stable_tie_key",
    "cutoff_CREATION_DATE",
    "cutoff_EPOCH",
    "replay_GP_ID",
    "replay_stable_tie_key",
    "replay_CREATION_DATE",
    "replay_EPOCH",
    "identity_reselection_status",
    "selected_source_class",
)


REQUIRED_RAW_FIELDS = {
    "NORAD_CAT_ID",
    "CREATION_DATE",
    "EPOCH",
    "GP_ID",
    "TLE_LINE1",
    "TLE_LINE2",
}


class ReplayInputError(RuntimeError):
    pass


@dataclass(frozen=True)
class SelectedGP:
    object_id: str
    creation_utc: datetime
    epoch_utc: datetime
    stable_tie_key: str
    gp_id: str
    tle_line1: str
    tle_line2: str
    source_class: str


def sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def parse_utc(value: str) -> datetime:
    text = value.strip()

    if not text:
        raise ReplayInputError(
            "empty UTC timestamp"
        )

    text = text.replace(
        " ",
        "T",
    )

    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(
            text
        )
    except ValueError as exc:
        raise ReplayInputError(
            f"malformed UTC timestamp: {value!r}"
        ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=UTC
        )
    else:
        parsed = parsed.astimezone(
            UTC
        )

    return parsed


def iso_utc(value: datetime) -> str:
    return (
        value.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def read_csv(
    path: Path,
) -> list[dict[str, str]]:

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:
        return list(
            csv.DictReader(stream)
        )


def canonical_row_sha(
    row: dict[str, str],
    fieldnames: tuple[str, ...],
) -> str:

    canonical = json.dumps(
        {
            field:
                row.get(field, "")
            for field
            in fieldnames
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(
        canonical
    ).hexdigest()


def normalize_id(
    value,
) -> str:

    text = str(value).strip()

    if (
        not text
        or not text.isdigit()
    ):
        raise ReplayInputError(
            f"invalid NORAD_CAT_ID: {value!r}"
        )

    return str(
        int(text)
    )


def record_key(
    record: SelectedGP,
):
    return (
        record.creation_utc,
        record.epoch_utc,
        record.stable_tie_key,
    )


def choose_later(
    current: SelectedGP | None,
    candidate: SelectedGP,
) -> SelectedGP:

    if (
        current is None
        or record_key(candidate)
        > record_key(current)
    ):
        return candidate

    return current


def selected_identity(
    record: SelectedGP,
):
    return (
        record.creation_utc,
        record.epoch_utc,
        record.stable_tie_key,
    )


def make_record(
    row: dict[str, str],
    fieldnames: tuple[str, ...],
    source_class: str,
) -> SelectedGP:

    object_id = normalize_id(
        row.get("NORAD_CAT_ID", "")
    )

    creation = parse_utc(
        row.get("CREATION_DATE", "")
    )

    epoch = parse_utc(
        row.get("EPOCH", "")
    )

    gp_id = (
        row.get("GP_ID", "")
        or ""
    ).strip()

    stable = (
        gp_id
        if gp_id
        else canonical_row_sha(
            row,
            fieldnames,
        )
    )

    return SelectedGP(
        object_id=object_id,
        creation_utc=creation,
        epoch_utc=epoch,
        stable_tie_key=stable,
        gp_id=gp_id,
        tle_line1=(
            row.get("TLE_LINE1", "")
            or ""
        ).strip(),
        tle_line2=(
            row.get("TLE_LINE2", "")
            or ""
        ).strip(),
        source_class=source_class,
    )


def atomic_json(
    path: Path,
    payload: dict,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )

    temp = Path(
        temp_name
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as stream:

            json.dump(
                payload,
                stream,
                indent=2,
                sort_keys=True,
            )

            stream.write("\n")
            stream.flush()
            os.fsync(
                stream.fileno()
            )

        os.replace(
            temp,
            path,
        )

    finally:
        temp.unlink(
            missing_ok=True
        )


def atomic_csv(
    path: Path,
    fields,
    rows,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )

    temp = Path(
        temp_name
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
            newline="",
        ) as stream:

            writer = csv.DictWriter(
                stream,
                fieldnames=fields,
                lineterminator="\n",
            )

            writer.writeheader()
            writer.writerows(
                rows
            )

            stream.flush()
            os.fsync(
                stream.fileno()
            )

        os.replace(
            temp,
            path,
        )

    finally:
        temp.unlink(
            missing_ok=True
        )


def verify_repository_boundary() -> None:

    head = git(
        "rev-parse",
        "HEAD",
    )

    if head != EXPECTED_HEAD:
        raise ReplayInputError(
            "unexpected repository HEAD: "
            + head
        )

    tag_target = git(
        "rev-parse",
        f"refs/tags/{FREEZE_TAG}^{{commit}}",
    )

    if tag_target != EXPECTED_HEAD:
        raise ReplayInputError(
            "causal-history freeze tag changed"
        )

    if git(
        "status",
        "--porcelain",
        "--untracked-files=no",
    ):
        raise ReplayInputError(
            "tracked Git tree is not clean"
        )


def verify_small_inputs() -> dict[str, str]:

    actual = {}

    for path, expected in (
        FROZEN_SMALL_INPUTS.items()
    ):

        if not path.is_file():
            raise ReplayInputError(
                f"missing frozen input: {path}"
            )

        digest = sha256(
            path
        )

        if digest != expected:
            raise ReplayInputError(
                "frozen SHA mismatch\n"
                f"path={path}\n"
                f"actual={digest}\n"
                f"expected={expected}"
            )

        actual[
            path.relative_to(
                ROOT
            ).as_posix()
        ] = digest

    return actual


def verify_environment() -> dict:

    packages = {
        name:
            importlib.metadata.version(
                name
            )
        for name
        in EXPECTED_PACKAGES
    }

    if packages != EXPECTED_PACKAGES:
        raise ReplayInputError(
            "runtime package versions changed: "
            f"{packages}"
        )

    if (
        sys.version_info[:3]
        != (3, 12, 14)
    ):
        raise ReplayInputError(
            "Python runtime changed: "
            + platform.python_version()
        )

    return {
        "python":
            platform.python_version(),

        "platform":
            platform.platform(),

        "packages":
            packages,
    }


def validate_frozen_metadata() -> dict:

    sample = read_csv(
        SAMPLE
    )

    manifest = read_csv(
        MANIFEST
    )

    if len(sample) != EXPECTED_EVENTS:
        raise ReplayInputError(
            "sample count changed"
        )

    if len(manifest) != EXPECTED_OBJECTS:
        raise ReplayInputError(
            "manifest object count changed"
        )

    sample_objects = []

    for row in sample:
        sample_objects.extend(
            (
                normalize_id(
                    row["obj1"]
                ),
                normalize_id(
                    row["obj2"]
                ),
            )
        )

    if (
        len(sample_objects)
        != EXPECTED_OBJECTS
        or len(
            set(sample_objects)
        )
        != EXPECTED_OBJECTS
    ):
        raise ReplayInputError(
            "G6 object-disjointness changed"
        )

    class_counts = Counter(
        row[
            "final_predecessor_class"
        ].strip()
        for row in manifest
    )

    expected_classes = Counter(
        {
            "PREDECESSOR_CAPTURED_IN_BATCH_UNION":
                EXPECTED_CAPTURED_PREDECESSORS,

            "PREDECESSOR_ACQUIRED_BY_QUERY":
                EXPECTED_QUERY_PREDECESSORS,

            "LEFT_CENSORED_NO_PREDECESSOR_RETURNED":
                EXPECTED_LEFT_CENSORED,
        }
    )

    if class_counts != expected_classes:
        raise ReplayInputError(
            "manifest predecessor classes changed"
        )

    total_main = sum(
        int(
            row[
                "main_window_rows"
            ]
        )
        for row in manifest
    )

    if (
        total_main
        != EXPECTED_MAIN_WINDOW_ROWS
    ):
        raise ReplayInputError(
            "main-window accounting changed"
        )

    main_paths = {
        row[
            "main_raw_path"
        ].strip()
        for row in manifest
    }

    if len(main_paths) != EXPECTED_MAIN_FILES:
        raise ReplayInputError(
            "main raw path count changed"
        )

    return {
        "events":
            len(sample),

        "objects":
            len(manifest),

        "main_raw_files":
            len(main_paths),

        "main_window_rows":
            total_main,

        "predecessor_class_counts":
            dict(
                sorted(
                    class_counts.items()
                )
            ),
    }


def offline_validate() -> dict:

    if VALIDATION_OUT.exists():
        raise ReplayInputError(
            "offline-validation artifact already exists"
        )

    verify_repository_boundary()

    frozen_hashes = (
        verify_small_inputs()
    )

    environment = (
        verify_environment()
    )

    metadata = (
        validate_frozen_metadata()
    )

    g4_summary = json.loads(
        G4_SUMMARY.read_text(
            encoding="utf-8"
        )
    )

    if (
        g4_summary.get(
            "status"
        )
        != "PASS"
        or g4_summary.get(
            "selected"
        )
        != EXPECTED_G4_SETTINGS
    ):
        raise ReplayInputError(
            "G4 frozen numerical settings changed"
        )

    if (
        g4_summary
        .get(
            "sgp4_reference",
            {},
        )
        .get(
            "gravity_constants"
        )
        != "WGS-72"
    ):
        raise ReplayInputError(
            "G4 gravity constants changed"
        )

    result = {
        "status":
            "G6_48H_REPLAY_INPUT_IMPLEMENTATION_OFFLINE_VALIDATION_PASS",

        "network_requests":
            0,

        "SpaceTrack_raw_files_opened":
            0,

        "TraCSS_reference_rows_opened":
            0,

        "protected_outcome_access":
            False,

        "propagation_performed":
            False,

        "reconstruction_performed":
            False,

        "replay_input_selection_executed":
            False,

        "selected_L_star_hours":
            LSTAR_HOURS,

        "nominal_cutoff":
            "reference_tca_utc - 48 h",

        "selection_order":
            "(CREATION_DATE, EPOCH, stable_tie_key)",

        "stable_tie_rule":
            "GP_ID when present; otherwise canonical preserved-record SHA-256",

        "replay_time_rule":
            "later CREATION_DATE of the two nominal-cutoff selections",

        "reselection_rule":
            "reselect both objects at replay time",

        "identity_guard":
            "replay selection identity must equal nominal-cutoff selection identity",

        "main_raw_integrity_strategy":
            "SHA-256 reverified inline during first streaming read",

        "main_raw_reselection_strategy":
            "second deterministic streaming pass; no outcome access",

        "selected_TLE_cache":
            "LOCAL_ONLY_UNDER_DATA_RAW_G6",

        "selected_TLE_cache_committable":
            False,

        "tracked_selection_manifest_contains_TLE_lines":
            False,

        "frozen_metadata":
            metadata,

        "frozen_input_sha256":
            frozen_hashes,

        "environment":
            environment,

        "G4_settings":
            EXPECTED_G4_SETTINGS,

        "G4_primary_horizon_seconds":
            432000.0,

        "gravity_constants":
            "WGS-72",

        "future_prepare_mode_authorized":
            False,

        "next_safe_action":
            "INDEPENDENT_REVIEW_AND_FREEZE_IMPLEMENTATION_BEFORE_PREPARE_INPUTS",
    }

    result[
        "implementation_sha256"
    ] = sha256(
        Path(__file__)
    )

    atomic_json(
        VALIDATION_OUT,
        result,
    )

    print(
        json.dumps(
            {
                **result,
                "offline_validation_sha256":
                    sha256(
                        VALIDATION_OUT
                    ),
            },
            indent=2,
            sort_keys=True,
        )
    )

    return result


# ---------------------------------------------------------------------
# FUTURE RAW EXECUTION SUPPORT
# ---------------------------------------------------------------------

def load_execution_inputs():

    sample_rows = read_csv(
        SAMPLE
    )

    manifest_rows = read_csv(
        MANIFEST
    )

    manifest_by_object = {
        normalize_id(
            row[
                "NORAD_CAT_ID"
            ]
        ):
            row
        for row in manifest_rows
    }

    event_by_object = {}

    for row in sample_rows:

        for role in (
            "obj1",
            "obj2",
        ):

            object_id = normalize_id(
                row[role]
            )

            event_by_object[
                object_id
            ] = {
                "final_g6_sample_index":
                    row[
                        "final_g6_sample_index"
                    ].strip(),

                "reserve_event_index":
                    row[
                        "reserve_event_index"
                    ].strip(),

                "g6_temporal_stratum":
                    row[
                        "g6_temporal_stratum"
                    ].strip(),

                "conj_id":
                    row[
                        "conj_id"
                    ].strip(),

                "object_role":
                    role,

                "reference_tca_utc":
                    parse_utc(
                        row[
                            "reference_tca_utc"
                        ]
                    ),
            }

    if set(
        event_by_object
    ) != set(
        manifest_by_object
    ):
        raise ReplayInputError(
            "sample/manifest object sets differ"
        )

    return (
        sample_rows,
        manifest_by_object,
        event_by_object,
    )


def load_main_state_by_path():
    state = json.loads(
        MAIN_STATE.read_text(
            encoding="utf-8"
        )
    )

    successes = [
        record
        for record in state.get(
            "records",
            [],
        )
        if (
            record.get("kind")
            == "main"
            and record.get(
                "status"
            )
            == "SUCCESS"
        )
    ]

    if (
        len(successes)
        != EXPECTED_MAIN_FILES
    ):
        raise ReplayInputError(
            "main state success count changed"
        )

    result = {}

    for record in successes:

        path = str(
            record[
                "raw_filename"
            ]
        )

        if path in result:
            raise ReplayInputError(
                "duplicate successful main raw path"
            )

        result[path] = record

    return result


def stream_main_first_pass(
    manifest_by_object,
    event_by_object,
):
    """Hash/verify every main file and select current GP at nominal 48 h cutoff."""

    state_by_path = (
        load_main_state_by_path()
    )

    objects_by_path = defaultdict(
        list
    )

    for object_id, row in (
        manifest_by_object.items()
    ):
        objects_by_path[
            row[
                "main_raw_path"
            ].strip()
        ].append(
            object_id
        )

    if (
        len(objects_by_path)
        != EXPECTED_MAIN_FILES
    ):
        raise ReplayInputError(
            "expected 1386 grouped main raw files"
        )

    cutoff_selected = {
        object_id:
            None
        for object_id
        in manifest_by_object
    }

    predecessor_records = {}

    main_window_counts = Counter()

    files_verified = 0
    bytes_verified = 0
    rows_verified = 0

    for relative_path in sorted(
        objects_by_path,
    ):

        expected_objects = set(
            objects_by_path[
                relative_path
            ]
        )

        state_record = (
            state_by_path.get(
                relative_path
            )
        )

        if state_record is None:
            raise ReplayInputError(
                "main raw path missing from acquisition state: "
                + relative_path
            )

        manifest_sha = {
            manifest_by_object[
                object_id
            ][
                "main_raw_SHA256"
            ].strip()
            for object_id
            in expected_objects
        }

        if len(
            manifest_sha
        ) != 1:
            raise ReplayInputError(
                "objects sharing one main raw file "
                "do not share one frozen SHA"
            )

        expected_sha = next(
            iter(
                manifest_sha
            )
        )

        if (
            state_record.get(
                "sha256"
            )
            != expected_sha
        ):
            raise ReplayInputError(
                "main manifest/state SHA mismatch: "
                + relative_path
            )

        state_requested = {
            normalize_id(
                value
            )
            for value in (
                state_record.get(
                    "requested_object_ids"
                )
                or []
            )
        }

        if (
            state_requested
            != expected_objects
        ):
            raise ReplayInputError(
                "main state object membership mismatch: "
                + relative_path
            )

        path = ROOT / relative_path

        if not path.is_file():
            raise ReplayInputError(
                "main raw file missing: "
                + relative_path
            )

        digest = hashlib.sha256()
        byte_count = 0

        def decoded_lines(stream):
            nonlocal byte_count

            for raw in stream:
                digest.update(
                    raw
                )

                byte_count += len(
                    raw
                )

                yield raw.decode(
                    "utf-8-sig"
                )

        row_count = 0
        observed_objects = set()

        with path.open(
            "rb"
        ) as stream:

            reader = csv.DictReader(
                decoded_lines(
                    stream
                )
            )

            fieldnames = tuple(
                reader.fieldnames
                or ()
            )

            missing = (
                REQUIRED_RAW_FIELDS
                - set(fieldnames)
            )

            if missing:
                raise ReplayInputError(
                    "main raw schema missing fields "
                    f"in {relative_path}: "
                    + repr(
                        sorted(missing)
                    )
                )

            for row in reader:

                row_count += 1

                object_id = normalize_id(
                    row.get(
                        "NORAD_CAT_ID",
                        "",
                    )
                )

                if (
                    object_id
                    not in expected_objects
                ):
                    raise ReplayInputError(
                        "unexpected object in main raw: "
                        f"{object_id} / {relative_path}"
                    )

                observed_objects.add(
                    object_id
                )

                record = make_record(
                    row,
                    fieldnames,
                    "MAIN_WINDOW",
                )

                manifest = (
                    manifest_by_object[
                        object_id
                    ]
                )

                lower = parse_utc(
                    manifest[
                        "lower_bound_utc"
                    ]
                )

                upper = (
                    event_by_object[
                        object_id
                    ][
                        "reference_tca_utc"
                    ]
                )

                cutoff = (
                    upper
                    - timedelta(
                        hours=LSTAR_HOURS
                    )
                )

                creation = (
                    record.creation_utc
                )

                if (
                    lower
                    <= creation
                    < upper
                ):
                    main_window_counts[
                        object_id
                    ] += 1

                    if (
                        creation
                        <= cutoff
                    ):
                        cutoff_selected[
                            object_id
                        ] = choose_later(
                            cutoff_selected[
                                object_id
                            ],
                            record,
                        )

                elif creation < lower:

                    if (
                        manifest[
                            "final_predecessor_class"
                        ].strip()
                        != "PREDECESSOR_CAPTURED_IN_BATCH_UNION"
                    ):
                        continue

                    expected_gp = (
                        manifest[
                            "predecessor_GP_ID"
                        ].strip()
                    )

                    expected_creation = parse_utc(
                        manifest[
                            "predecessor_CREATION_DATE"
                        ]
                    )

                    expected_epoch = parse_utc(
                        manifest[
                            "predecessor_EPOCH"
                        ]
                    )

                    expected_source = (
                        manifest[
                            "predecessor_raw_path"
                        ].strip()
                    )

                    if (
                        relative_path
                        != expected_source
                    ):
                        raise ReplayInputError(
                            "captured predecessor source mismatch "
                            "for "
                            + object_id
                        )

                    if (
                        record.gp_id
                        == expected_gp
                        and record.creation_utc
                        == expected_creation
                        and record.epoch_utc
                        == expected_epoch
                    ):
                        if (
                            object_id
                            in predecessor_records
                        ):
                            raise ReplayInputError(
                                "captured predecessor matched "
                                "more than once for "
                                + object_id
                            )

                        predecessor_records[
                            object_id
                        ] = SelectedGP(
                            object_id=
                                record.object_id,

                            creation_utc=
                                record.creation_utc,

                            epoch_utc=
                                record.epoch_utc,

                            stable_tie_key=
                                record.stable_tie_key,

                            gp_id=
                                record.gp_id,

                            tle_line1=
                                record.tle_line1,

                            tle_line2=
                                record.tle_line2,

                            source_class=
                                "CAPTURED_PREDECESSOR",
                        )

        if (
            observed_objects
            != expected_objects
        ):
            raise ReplayInputError(
                "main raw object coverage mismatch: "
                + relative_path
            )

        actual_sha = (
            digest.hexdigest()
        )

        if (
            actual_sha
            != expected_sha
        ):
            raise ReplayInputError(
                "main raw SHA mismatch: "
                + relative_path
            )

        if (
            row_count
            != int(
                state_record[
                    "row_count"
                ]
            )
        ):
            raise ReplayInputError(
                "main raw row-count mismatch: "
                + relative_path
            )

        if (
            byte_count
            != int(
                state_record[
                    "byte_count"
                ]
            )
        ):
            raise ReplayInputError(
                "main raw byte-count mismatch: "
                + relative_path
            )

        files_verified += 1
        bytes_verified += byte_count
        rows_verified += row_count

    for object_id, manifest in (
        manifest_by_object.items()
    ):

        if (
            main_window_counts[
                object_id
            ]
            != int(
                manifest[
                    "main_window_rows"
                ]
            )
        ):
            raise ReplayInputError(
                "main-window row-count mismatch for "
                + object_id
            )

        if (
            manifest[
                "final_predecessor_class"
            ].strip()
            == "PREDECESSOR_CAPTURED_IN_BATCH_UNION"
            and object_id
            not in predecessor_records
        ):
            raise ReplayInputError(
                "captured predecessor not found for "
                + object_id
            )

    return {
        "cutoff_selected":
            cutoff_selected,

        "predecessor_records":
            predecessor_records,

        "main_files_verified":
            files_verified,

        "main_bytes_verified":
            bytes_verified,

        "main_rows_verified":
            rows_verified,
    }


def add_queried_predecessors(
    manifest_by_object,
    predecessor_records,
):

    files_verified = 0

    for object_id, manifest in (
        manifest_by_object.items()
    ):

        if (
            manifest[
                "final_predecessor_class"
            ].strip()
            != "PREDECESSOR_ACQUIRED_BY_QUERY"
        ):
            continue

        relative_path = (
            manifest[
                "predecessor_raw_path"
            ].strip()
        )

        expected_sha = (
            manifest[
                "predecessor_raw_SHA256"
            ].strip()
        )

        path = (
            ROOT
            / relative_path
        )

        if (
            not path.is_file()
            or sha256(path)
            != expected_sha
        ):
            raise ReplayInputError(
                "queried predecessor integrity failure for "
                + object_id
            )

        rows = read_csv(
            path
        )

        if len(rows) != 1:
            raise ReplayInputError(
                "queried predecessor is not exactly one row for "
                + object_id
            )

        fieldnames = tuple(
            rows[0].keys()
        )

        record = make_record(
            rows[0],
            fieldnames,
            "QUERIED_PREDECESSOR",
        )

        if (
            record.object_id
            != object_id
            or record.gp_id
            != manifest[
                "predecessor_GP_ID"
            ].strip()
            or record.creation_utc
            != parse_utc(
                manifest[
                    "predecessor_CREATION_DATE"
                ]
            )
            or record.epoch_utc
            != parse_utc(
                manifest[
                    "predecessor_EPOCH"
                ]
            )
        ):
            raise ReplayInputError(
                "queried predecessor metadata mismatch for "
                + object_id
            )

        predecessor_records[
            object_id
        ] = record

        files_verified += 1

    if (
        files_verified
        != EXPECTED_QUERY_PREDECESSORS
    ):
        raise ReplayInputError(
            "queried predecessor success count changed"
        )

    return files_verified


def derive_cutoff_and_replay_times(
    sample_rows,
    event_by_object,
    cutoff_selected,
    predecessor_records,
):

    for object_id, predecessor in (
        predecessor_records.items()
    ):

        cutoff = (
            event_by_object[
                object_id
            ][
                "reference_tca_utc"
            ]
            - timedelta(
                hours=LSTAR_HOURS
            )
        )

        if (
            predecessor.creation_utc
            <= cutoff
        ):
            cutoff_selected[
                object_id
            ] = choose_later(
                cutoff_selected[
                    object_id
                ],
                predecessor,
            )

    replay_by_object = {}
    event_replay = {}
    availability = Counter()

    for row in sample_rows:

        object1 = normalize_id(
            row[
                "obj1"
            ]
        )

        object2 = normalize_id(
            row[
                "obj2"
            ]
        )

        first = cutoff_selected[
            object1
        ]

        second = cutoff_selected[
            object2
        ]

        if (
            first is None
            and second is None
        ):
            availability[
                "NO_CAUSAL_GP_BOTH"
            ] += 1

            continue

        if first is None:
            availability[
                "NO_CAUSAL_GP_OBJECT_1"
            ] += 1

            continue

        if second is None:
            availability[
                "NO_CAUSAL_GP_OBJECT_2"
            ] += 1

            continue

        replay = max(
            first.creation_utc,
            second.creation_utc,
        )

        cutoff = (
            parse_utc(
                row[
                    "reference_tca_utc"
                ]
            )
            - timedelta(
                hours=LSTAR_HOURS
            )
        )

        if replay > cutoff:
            raise ReplayInputError(
                "derived replay exceeds nominal cutoff"
            )

        event_index = (
            row[
                "final_g6_sample_index"
            ].strip()
        )

        event_replay[
            event_index
        ] = replay

        replay_by_object[
            object1
        ] = replay

        replay_by_object[
            object2
        ] = replay

        availability[
            "CAUSAL_GP_AVAILABLE_BOTH"
        ] += 1

    return (
        event_replay,
        replay_by_object,
        availability,
    )


def stream_main_reselection_pass(
    manifest_by_object,
    event_by_object,
    replay_by_object,
):

    objects_by_path = defaultdict(
        list
    )

    for object_id, manifest in (
        manifest_by_object.items()
    ):

        if (
            object_id
            not in replay_by_object
        ):
            continue

        objects_by_path[
            manifest[
                "main_raw_path"
            ].strip()
        ].append(
            object_id
        )

    replay_selected = {
        object_id:
            None
        for object_id
        in replay_by_object
    }

    files_read = 0

    for relative_path in sorted(
        objects_by_path
    ):

        path = (
            ROOT
            / relative_path
        )

        expected_objects = set(
            objects_by_path[
                relative_path
            ]
        )

        with path.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as stream:

            reader = csv.DictReader(
                stream
            )

            fieldnames = tuple(
                reader.fieldnames
                or ()
            )

            missing = (
                REQUIRED_RAW_FIELDS
                - set(fieldnames)
            )

            if missing:
                raise ReplayInputError(
                    "main raw schema changed during reselection"
                )

            for row in reader:

                object_id = normalize_id(
                    row[
                        "NORAD_CAT_ID"
                    ]
                )

                if (
                    object_id
                    not in expected_objects
                ):
                    continue

                record = make_record(
                    row,
                    fieldnames,
                    "MAIN_WINDOW",
                )

                manifest = (
                    manifest_by_object[
                        object_id
                    ]
                )

                lower = parse_utc(
                    manifest[
                        "lower_bound_utc"
                    ]
                )

                upper = (
                    event_by_object[
                        object_id
                    ][
                        "reference_tca_utc"
                    ]
                )

                replay = (
                    replay_by_object[
                        object_id
                    ]
                )

                if (
                    lower
                    <= record.creation_utc
                    < upper
                    and record.creation_utc
                    <= replay
                ):
                    replay_selected[
                        object_id
                    ] = choose_later(
                        replay_selected[
                            object_id
                        ],
                        record,
                    )

        files_read += 1

    return (
        replay_selected,
        files_read,
    )


def finalize_reselection(
    replay_selected,
    replay_by_object,
    predecessor_records,
):

    for object_id, replay in (
        replay_by_object.items()
    ):

        predecessor = (
            predecessor_records.get(
                object_id
            )
        )

        if (
            predecessor is not None
            and predecessor.creation_utc
            <= replay
        ):
            replay_selected[
                object_id
            ] = choose_later(
                replay_selected[
                    object_id
                ],
                predecessor,
            )


def write_prepared_outputs(
    sample_rows,
    manifest_by_object,
    event_by_object,
    cutoff_selected,
    replay_selected,
    event_replay,
    first_pass,
    queried_files_verified,
    second_pass_files,
):

    if (
        LOCAL_CACHE.exists()
        or SELECTION_OUT.exists()
        or SUMMARY_OUT.exists()
    ):
        raise ReplayInputError(
            "refusing to overwrite replay-input outputs"
        )

    selection_rows = []
    cache_rows = []

    identity_verified = 0
    unavailable_objects = 0

    for row in sample_rows:

        event_index = (
            row[
                "final_g6_sample_index"
            ].strip()
        )

        for role in (
            "obj1",
            "obj2",
        ):

            object_id = normalize_id(
                row[
                    role
                ]
            )

            meta = (
                event_by_object[
                    object_id
                ]
            )

            cutoff = (
                meta[
                    "reference_tca_utc"
                ]
                - timedelta(
                    hours=LSTAR_HOURS
                )
            )

            selected = (
                cutoff_selected[
                    object_id
                ]
            )

            replay = (
                event_replay.get(
                    event_index
                )
            )

            reselected = (
                replay_selected.get(
                    object_id
                )
                if replay is not None
                else None
            )

            if selected is None:

                unavailable_objects += 1

                if reselected is not None:
                    raise ReplayInputError(
                        "unavailable cutoff object gained "
                        "a replay selection"
                    )

                selection_rows.append(
                    {
                        "final_g6_sample_index":
                            meta[
                                "final_g6_sample_index"
                            ],

                        "reserve_event_index":
                            meta[
                                "reserve_event_index"
                            ],

                        "g6_temporal_stratum":
                            meta[
                                "g6_temporal_stratum"
                            ],

                        "conj_id":
                            meta[
                                "conj_id"
                            ],

                        "NORAD_CAT_ID":
                            object_id,

                        "object_role":
                            role,

                        "reference_tca_utc":
                            iso_utc(
                                meta[
                                    "reference_tca_utc"
                                ]
                            ),

                        "nominal_cutoff_utc":
                            iso_utc(
                                cutoff
                            ),

                        "replay_time_utc":
                            "",

                        "selection_status":
                            "NO_CAUSAL_GP_AT_48H_CUTOFF",

                        "cutoff_GP_ID":
                            "",

                        "cutoff_stable_tie_key":
                            "",

                        "cutoff_CREATION_DATE":
                            "",

                        "cutoff_EPOCH":
                            "",

                        "replay_GP_ID":
                            "",

                        "replay_stable_tie_key":
                            "",

                        "replay_CREATION_DATE":
                            "",

                        "replay_EPOCH":
                            "",

                        "identity_reselection_status":
                            "NOT_APPLICABLE",

                        "selected_source_class":
                            "",
                    }
                )

                continue

            if replay is None:
                # Pair unavailable because the other object is missing.
                selection_rows.append(
                    {
                        "final_g6_sample_index":
                            meta[
                                "final_g6_sample_index"
                            ],

                        "reserve_event_index":
                            meta[
                                "reserve_event_index"
                            ],

                        "g6_temporal_stratum":
                            meta[
                                "g6_temporal_stratum"
                            ],

                        "conj_id":
                            meta[
                                "conj_id"
                            ],

                        "NORAD_CAT_ID":
                            object_id,

                        "object_role":
                            role,

                        "reference_tca_utc":
                            iso_utc(
                                meta[
                                    "reference_tca_utc"
                                ]
                            ),

                        "nominal_cutoff_utc":
                            iso_utc(
                                cutoff
                            ),

                        "replay_time_utc":
                            "",

                        "selection_status":
                            "PAIR_REPLAY_NOT_DEFINED_OTHER_OBJECT_UNAVAILABLE",

                        "cutoff_GP_ID":
                            selected.gp_id,

                        "cutoff_stable_tie_key":
                            selected.stable_tie_key,

                        "cutoff_CREATION_DATE":
                            iso_utc(
                                selected.creation_utc
                            ),

                        "cutoff_EPOCH":
                            iso_utc(
                                selected.epoch_utc
                            ),

                        "replay_GP_ID":
                            "",

                        "replay_stable_tie_key":
                            "",

                        "replay_CREATION_DATE":
                            "",

                        "replay_EPOCH":
                            "",

                        "identity_reselection_status":
                            "NOT_APPLICABLE_PAIR_UNAVAILABLE",

                        "selected_source_class":
                            selected.source_class,
                    }
                )

                continue

            if reselected is None:
                raise ReplayInputError(
                    "reselection unexpectedly missing for "
                    + object_id
                )

            if (
                selected_identity(
                    selected
                )
                != selected_identity(
                    reselected
                )
            ):
                raise ReplayInputError(
                    "GP identity changed between "
                    "48 h cutoff and replay time for "
                    + object_id
                )

            identity_verified += 1

            selection_rows.append(
                {
                    "final_g6_sample_index":
                        meta[
                            "final_g6_sample_index"
                        ],

                    "reserve_event_index":
                        meta[
                            "reserve_event_index"
                        ],

                    "g6_temporal_stratum":
                        meta[
                            "g6_temporal_stratum"
                        ],

                    "conj_id":
                        meta[
                            "conj_id"
                        ],

                    "NORAD_CAT_ID":
                        object_id,

                    "object_role":
                        role,

                    "reference_tca_utc":
                        iso_utc(
                            meta[
                                "reference_tca_utc"
                            ]
                        ),

                    "nominal_cutoff_utc":
                        iso_utc(
                            cutoff
                        ),

                    "replay_time_utc":
                        iso_utc(
                            replay
                        ),

                    "selection_status":
                        "CAUSAL_GP_SELECTED",

                    "cutoff_GP_ID":
                        selected.gp_id,

                    "cutoff_stable_tie_key":
                        selected.stable_tie_key,

                    "cutoff_CREATION_DATE":
                        iso_utc(
                            selected.creation_utc
                        ),

                    "cutoff_EPOCH":
                        iso_utc(
                            selected.epoch_utc
                        ),

                    "replay_GP_ID":
                        reselected.gp_id,

                    "replay_stable_tie_key":
                        reselected.stable_tie_key,

                    "replay_CREATION_DATE":
                        iso_utc(
                            reselected.creation_utc
                        ),

                    "replay_EPOCH":
                        iso_utc(
                            reselected.epoch_utc
                        ),

                    "identity_reselection_status":
                        "UNCHANGED",

                    "selected_source_class":
                        reselected.source_class,
                }
            )

            cache_rows.append(
                {
                    "final_g6_sample_index":
                        meta[
                            "final_g6_sample_index"
                        ],

                    "conj_id":
                        meta[
                            "conj_id"
                        ],

                    "NORAD_CAT_ID":
                        object_id,

                    "object_role":
                        role,

                    "replay_time_utc":
                        iso_utc(
                            replay
                        ),

                    "stable_tie_key":
                        reselected.stable_tie_key,

                    "GP_ID":
                        reselected.gp_id,

                    "CREATION_DATE":
                        iso_utc(
                            reselected.creation_utc
                        ),

                    "EPOCH":
                        iso_utc(
                            reselected.epoch_utc
                        ),

                    "TLE_LINE1":
                        reselected.tle_line1,

                    "TLE_LINE2":
                        reselected.tle_line2,

                    "source_class":
                        reselected.source_class,
                }
            )

    if len(
        selection_rows
    ) != EXPECTED_OBJECTS:
        raise ReplayInputError(
            "selection output does not contain 13854 objects"
        )

    LOCAL_CACHE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{LOCAL_CACHE.name}.",
        suffix=".tmp",
        dir=LOCAL_CACHE.parent,
    )

    temp = Path(
        temp_name
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as stream:

            for item in cache_rows:
                stream.write(
                    json.dumps(
                        item,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )

            stream.flush()
            os.fsync(
                stream.fileno()
            )

        os.replace(
            temp,
            LOCAL_CACHE,
        )

    finally:
        temp.unlink(
            missing_ok=True
        )

    atomic_csv(
        SELECTION_OUT,
        SELECTION_FIELDS,
        selection_rows,
    )

    status_counts = Counter(
        row[
            "selection_status"
        ]
        for row in selection_rows
    )

    summary = {
        "status":
            "G6_48H_REPLAY_INPUT_PREPARATION_PASS",

        "network_requests":
            0,

        "protected_outcome_access":
            False,

        "propagation_performed":
            False,

        "reconstruction_performed":
            False,

        "selected_L_star_hours":
            LSTAR_HOURS,

        "events":
            EXPECTED_EVENTS,

        "objects":
            EXPECTED_OBJECTS,

        "selection_status_counts":
            dict(
                sorted(
                    status_counts.items()
                )
            ),

        "identity_reselection_verified_objects":
            identity_verified,

        "cutoff_unavailable_objects":
            unavailable_objects,

        "main_raw_files_sha256_verified":
            first_pass[
                "main_files_verified"
            ],

        "main_raw_bytes_sha256_verified":
            first_pass[
                "main_bytes_verified"
            ],

        "main_raw_rows_verified":
            first_pass[
                "main_rows_verified"
            ],

        "queried_predecessor_files_sha256_verified":
            queried_files_verified,

        "main_raw_files_read_in_reselection_pass":
            second_pass_files,

        "selection_manifest":
            SELECTION_OUT.relative_to(
                ROOT
            ).as_posix(),

        "selection_manifest_sha256":
            sha256(
                SELECTION_OUT
            ),

        "local_selected_TLE_cache":
            LOCAL_CACHE.relative_to(
                ROOT
            ).as_posix(),

        "local_selected_TLE_cache_sha256":
            sha256(
                LOCAL_CACHE
            ),

        "local_selected_TLE_cache_committable":
            False,

        "next_safe_action":
            "INDEPENDENT_AUDIT_AND_FREEZE_REPLAY_INPUT_SELECTION_BEFORE_PROPAGATION",
    }

    atomic_json(
        SUMMARY_OUT,
        summary,
    )

    print(
        json.dumps(
            {
                **summary,
                "summary_sha256":
                    sha256(
                        SUMMARY_OUT
                    ),
            },
            indent=2,
            sort_keys=True,
        )
    )


def prepare_inputs() -> None:

    raise ReplayInputError(
        "PREPARE_INPUTS_INACTIVE_UNTIL_IMPLEMENTATION_IS_REVIEWED_AND_AUTHORIZED"
    )


def main() -> int:

    parser = argparse.ArgumentParser()

    group = parser.add_mutually_exclusive_group(
        required=True
    )

    group.add_argument(
        "--offline-validate",
        action="store_true",
    )

    group.add_argument(
        "--prepare-inputs",
        action="store_true",
    )

    args = parser.parse_args()

    if args.offline_validate:
        offline_validate()
        return 0

    if args.prepare_inputs:
        prepare_inputs()
        return 0

    raise ReplayInputError(
        "unreachable command state"
    )


if __name__ == "__main__":
    raise SystemExit(main())
