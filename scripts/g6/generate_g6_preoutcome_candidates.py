#!/usr/bin/env python3
"""G6 pre-outcome numerical candidate-generation implementation.

The scientific replay inputs are already frozen by DR-0025/DR-0026.

Modes
-----
--offline-validate
    Verify the frozen numerical/input boundary and exercise only a synthetic
    analytical scalar quantity through the already-frozen redundancy-safe
    candidate enumerator.

--generate
    Deliberately disabled until this implementation has been independently
    reviewed and separately authorized.

No TraCSS/reference outcome source is opened by this module. Reference
association is deliberately absent. G6 candidate geometry is generated only
after a later explicit authorization.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]

EXPECTED_HEAD = (
    "d12b51a8ebdcc16b6c9d728d63e4ae08f3c7cecd"
)

EXPECTED_PARENT_FREEZE = (
    "5704a63a50c4cc7a3800d3ebf40ba8882795801f"
)

CURRENT_TAG = (
    "g6-dr0025-corrective-verification-recorded"
)

INPUT_FREEZE_TAG = (
    "g6-48h-replay-input-selection-frozen"
)

EXPECTED_EVENTS = 6_927
EXPECTED_OBJECTS = 13_854

SELECTION = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Selection.csv"
)

INPUT_SUMMARY = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Summary.json"
)

INPUT_EXECUTION_AUDIT = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Preparation_Execution_Audit.json"
)

INPUT_INDEPENDENT_AUDIT = (
    ROOT
    / "results/g6/G6_48H_Replay_Input_Selection_Independent_Audit.json"
)

CORRECTIVE_RECORD = (
    ROOT
    / "results/g6/G6_DR0025_PostFreeze_Corrective_Verification.json"
)

DR0026 = (
    ROOT
    / "docs/research/DR-0026_G6_DR0025_PostFreeze_Corrective_Verification.md"
)

CACHE = (
    ROOT
    / "data/raw/g6/G6_48H_Replay_Selected_GP_Cache.jsonl"
)

G4_NUMERICS = (
    ROOT
    / "scripts/g4/numerics.py"
)

G4_SUMMARY = (
    ROOT
    / "results/g4/G4_Run_Summary.json"
)

G5_CONTROLFLOW = (
    ROOT
    / "scripts/g5/select_lstar_controlflow_amended.py"
)

G5_PUBLICATION_REPLAY = (
    ROOT
    / "scripts/g5/run_g5_calibration_v2.py"
)

G6_LOCK = (
    ROOT
    / "docs/research/G6_PreOutcome_Confirmatory_Lock.md"
)

OFFLINE_VALIDATION = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Implementation_Offline_Validation.json"
)

# Future execution artifacts.
WORK_DB = (
    ROOT
    / "data/raw/g6/G6_PreOutcome_Candidate_Work.sqlite3"
)

EVENT_OUT = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Event_Status.csv"
)

CANDIDATE_OUT = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidates.csv"
)

SUMMARY_OUT = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Summary.json"
)


EXPECTED_HASHES = {
    SELECTION:
        "6d03f0b4c7104b7cbac76b649bedc87e9f497f297dccc19f2270e7fec5552095",

    INPUT_SUMMARY:
        "aa64f1353f61bb516e9ff0737f60b2d1b238fed42fa92ff20f55e2df613eae68",

    INPUT_EXECUTION_AUDIT:
        "f2565c4c14aca249bc9ef7dc5d254d860f0794f2c209f0298af579c2cbcfb07c",

    INPUT_INDEPENDENT_AUDIT:
        "46323d81b4d5a2736b545977c52588b1b27426c0a98c5f27bca9e24293ea060a",

    CORRECTIVE_RECORD:
        "01134a845fc8aceaf3e7536314f92c77308f5d65a35e6a1414f7351a4e4180ea",

    DR0026:
        "e17e09196094f24dc432ddc7e2702b978f72de91c2b5c4bab17ca6398860e54e",

    CACHE:
        "b77cf2893cf92b2e577bf04f5f23d67dc6be31b9d1e8aeafe0e40efd6b08ed69",

    G4_NUMERICS:
        "dc8de410b89e05ffd829563b4996b06703c31236e9a10fb56bd7f4e690048406",

    G4_SUMMARY:
        "d0a0a4f159a9a7eeb9763263f7a66c14b1c76bf07a2149027d17c5916ce22756",

    G5_CONTROLFLOW:
        "553578f4e90b044c6a783209a756ea3accc009ecbb7a09853e8cae46503f33c2",

    G5_PUBLICATION_REPLAY:
        "2ceae52cd0e4ffa6c1e9dea2df38c49d2c4bb983e7dfe7f8cdbc8dbeaf202c0d",

    G6_LOCK:
        "21fc2d4eb83b8d00d7bbb632986fcdc33ec437dce9ca3e69020b222d382adb2e",
}


EXPECTED_PACKAGES = {
    "numpy": "2.3.5",
    "scipy": "1.18.1",
    "sgp4": "2.27",
}


EVENT_FIELDS = (
    "final_g6_sample_index",
    "g6_temporal_stratum",
    "conj_id",
    "obj1",
    "obj2",
    "replay_time_utc",
    "obj1_GP_ID",
    "obj2_GP_ID",
    "parse_status",
    "propagation_status",
    "rejected_local_inconsistency_count",
    "recovered_local_inconsistency_count",
    "unresolved_boundary_count",
    "sampled_count",
    "raw_candidate_count",
    "candidate_count",
    "preoutcome_status",
    "failure_detail",
)


CANDIDATE_FIELDS = (
    "final_g6_sample_index",
    "g6_temporal_stratum",
    "conj_id",
    "candidate_index",
    "time_from_replay_s",
    "candidate_time_utc",
    "distance_km",
    "bracket_lower_s",
    "bracket_upper_s",
    "bracket_index",
    "converged",
    "iterations",
    "function_evaluations",
    "termination",
)


ALLOWED_PREOUTCOME_STATUSES = {
    "GP_PARSE_FAILURE",
    "PROPAGATION_FAILURE",
    "UNRECOVERED_REFINEMENT_FAILURE",
    "NO_VALID_CANDIDATE",
    "CANDIDATE_SET_READY",
}


class CandidateImplementationError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def git(
    *args: str,
    check: bool = True,
):
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def git_value(*args: str) -> str:
    return git(*args).stdout.strip()


def parse_utc(value: str) -> datetime:
    text = str(value).strip()

    if not text:
        raise CandidateImplementationError(
            "empty timestamp"
        )

    text = text.replace(
        " ",
        "T",
    )

    if text.endswith(
        ("Z", "z")
    ):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(
            text
        )
    except ValueError as exc:
        raise CandidateImplementationError(
            f"malformed UTC timestamp: {value!r}"
        ) from exc

    if parsed.tzinfo is None:
        raise CandidateImplementationError(
            "naive UTC timestamp is prohibited"
        )

    return parsed.astimezone(
        UTC
    )


def iso_utc(value: datetime) -> str:
    return (
        value.astimezone(UTC)
        .isoformat(
            timespec="microseconds"
        )
        .replace(
            "+00:00",
            "Z",
        )
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

    if git_value(
        "rev-parse",
        "HEAD",
    ) != EXPECTED_HEAD:
        raise CandidateImplementationError(
            "unexpected repository HEAD"
        )

    if git_value(
        "rev-parse",
        f"refs/tags/{CURRENT_TAG}^{{commit}}",
    ) != EXPECTED_HEAD:
        raise CandidateImplementationError(
            "DR-0026 tag target changed"
        )

    if git_value(
        "rev-parse",
        f"refs/tags/{INPUT_FREEZE_TAG}^{{commit}}",
    ) != EXPECTED_PARENT_FREEZE:
        raise CandidateImplementationError(
            "DR-0025 input-freeze tag target changed"
        )

    if git_value(
        "status",
        "--porcelain",
        "--untracked-files=no",
    ):
        raise CandidateImplementationError(
            "tracked working tree is not clean"
        )


def verify_frozen_hashes() -> dict[str, str]:

    result = {}

    for path, expected in (
        EXPECTED_HASHES.items()
    ):

        if not path.is_file():
            raise CandidateImplementationError(
                f"missing frozen dependency: {path}"
            )

        actual = sha256(
            path
        )

        if actual != expected:
            raise CandidateImplementationError(
                "frozen dependency SHA mismatch\n"
                f"path={path}\n"
                f"actual={actual}\n"
                f"expected={expected}"
            )

        result[
            path.relative_to(
                ROOT
            ).as_posix()
        ] = actual

    return result


def verify_environment() -> dict:

    packages = {
        package:
            importlib.metadata.version(
                package
            )
        for package
        in EXPECTED_PACKAGES
    }

    if packages != EXPECTED_PACKAGES:
        raise CandidateImplementationError(
            f"runtime package versions changed: {packages}"
        )

    if (
        sys.version_info[:3]
        != (3, 12, 14)
    ):
        raise CandidateImplementationError(
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


def import_frozen_controlflow():

    g5_dir = (
        ROOT
        / "scripts/g5"
    )

    if str(
        g5_dir
    ) not in sys.path:
        sys.path.insert(
            0,
            str(g5_dir),
        )

    import select_lstar_controlflow_amended as s

    return s


def read_selection_rows():
    with SELECTION.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        return list(
            csv.DictReader(
                stream
            )
        )


def read_cache_rows():
    rows = []

    with CACHE.open(
        "r",
        encoding="utf-8",
    ) as stream:

        for line in stream:

            if line.strip():
                rows.append(
                    json.loads(
                        line
                    )
                )

    return rows


def verify_compact_inputs():

    selection = (
        read_selection_rows()
    )

    cache = (
        read_cache_rows()
    )

    if (
        len(selection)
        != EXPECTED_OBJECTS
        or len(cache)
        != EXPECTED_OBJECTS
    ):
        raise CandidateImplementationError(
            "frozen compact input count changed"
        )

    selection_by_object = {}

    event_rows = defaultdict(
        list
    )

    for row in selection:

        object_id = row[
            "NORAD_CAT_ID"
        ].strip()

        if object_id in selection_by_object:
            raise CandidateImplementationError(
                "duplicate object in selection"
            )

        if (
            row[
                "selection_status"
            ].strip()
            != "CAUSAL_GP_SELECTED"
        ):
            raise CandidateImplementationError(
                "non-selected object appears in frozen selection"
            )

        if (
            row[
                "identity_reselection_status"
            ].strip()
            != "UNCHANGED"
        ):
            raise CandidateImplementationError(
                "reselection identity is not frozen UNCHANGED"
            )

        if (
            row[
                "selected_source_class"
            ].strip()
            != "MAIN_WINDOW"
        ):
            raise CandidateImplementationError(
                "frozen selected source is no longer MAIN_WINDOW"
            )

        selection_by_object[
            object_id
        ] = row

        event_rows[
            row[
                "final_g6_sample_index"
            ].strip()
        ].append(
            row
        )

    if (
        len(event_rows)
        != EXPECTED_EVENTS
        or any(
            len(rows) != 2
            for rows in event_rows.values()
        )
    ):
        raise CandidateImplementationError(
            "frozen event pairing changed"
        )

    cache_by_object = {}

    for item in cache:

        object_id = str(
            item[
                "NORAD_CAT_ID"
            ]
        ).strip()

        if object_id in cache_by_object:
            raise CandidateImplementationError(
                "duplicate object in selected-TLE cache"
            )

        cache_by_object[
            object_id
        ] = item

    if (
        set(cache_by_object)
        != set(selection_by_object)
    ):
        raise CandidateImplementationError(
            "cache/selection object sets differ"
        )

    for object_id, row in (
        selection_by_object.items()
    ):

        item = (
            cache_by_object[
                object_id
            ]
        )

        if (
            str(
                item[
                    "GP_ID"
                ]
            ).strip()
            != row[
                "replay_GP_ID"
            ].strip()
        ):
            raise CandidateImplementationError(
                "cache GP_ID mismatch for "
                + object_id
            )

        if (
            str(
                item[
                    "stable_tie_key"
                ]
            ).strip()
            != row[
                "replay_stable_tie_key"
            ].strip()
        ):
            raise CandidateImplementationError(
                "cache stable-tie mismatch for "
                + object_id
            )

        if (
            parse_utc(
                item[
                    "CREATION_DATE"
                ]
            )
            != parse_utc(
                row[
                    "replay_CREATION_DATE"
                ]
            )
            or parse_utc(
                item[
                    "EPOCH"
                ]
            )
            != parse_utc(
                row[
                    "replay_EPOCH"
                ]
            )
            or parse_utc(
                item[
                    "replay_time_utc"
                ]
            )
            != parse_utc(
                row[
                    "replay_time_utc"
                ]
            )
        ):
            raise CandidateImplementationError(
                "cache temporal metadata mismatch for "
                + object_id
            )

    return (
        selection_by_object,
        cache_by_object,
        event_rows,
    )


def make_gp_record(
    s,
    object_id: str,
    cache_item: dict,
):

    return s.g4.GPRecord(
        object_id=object_id,

        epoch_utc=parse_utc(
            cache_item[
                "EPOCH"
            ]
        ),

        creation_date_utc=parse_utc(
            cache_item[
                "CREATION_DATE"
            ]
        ),

        stable_tie_key=str(
            cache_item[
                "stable_tie_key"
            ]
        ).strip(),

        payload={
            "GP_ID":
                str(
                    cache_item[
                        "GP_ID"
                    ]
                ).strip(),

            "TLE_LINE1":
                str(
                    cache_item[
                        "TLE_LINE1"
                    ]
                ),

            "TLE_LINE2":
                str(
                    cache_item[
                        "TLE_LINE2"
                    ]
                ),
        },
    )


def build_events(
    event_rows,
    cache_by_object,
):

    events = []

    for event_index in sorted(
        event_rows,
        key=int,
    ):

        rows = (
            event_rows[
                event_index
            ]
        )

        by_role = {
            row[
                "object_role"
            ].strip():
                row
            for row in rows
        }

        if set(
            by_role
        ) != {
            "obj1",
            "obj2",
        }:
            raise CandidateImplementationError(
                "event object roles are not exactly obj1/obj2"
            )

        first = by_role[
            "obj1"
        ]

        second = by_role[
            "obj2"
        ]

        if (
            first[
                "conj_id"
            ].strip()
            != second[
                "conj_id"
            ].strip()
        ):
            raise CandidateImplementationError(
                "event conjunction ID mismatch"
            )

        if (
            first[
                "g6_temporal_stratum"
            ].strip()
            != second[
                "g6_temporal_stratum"
            ].strip()
        ):
            raise CandidateImplementationError(
                "event temporal-stratum mismatch"
            )

        replay1 = parse_utc(
            first[
                "replay_time_utc"
            ]
        )

        replay2 = parse_utc(
            second[
                "replay_time_utc"
            ]
        )

        if replay1 != replay2:
            raise CandidateImplementationError(
                "event replay-time mismatch"
            )

        obj1 = first[
            "NORAD_CAT_ID"
        ].strip()

        obj2 = second[
            "NORAD_CAT_ID"
        ].strip()

        if (
            parse_utc(
                cache_by_object[
                    obj1
                ][
                    "replay_time_utc"
                ]
            )
            != replay1
            or parse_utc(
                cache_by_object[
                    obj2
                ][
                    "replay_time_utc"
                ]
            )
            != replay1
        ):
            raise CandidateImplementationError(
                "cache/event replay-time mismatch"
            )

        events.append(
            {
                "final_g6_sample_index":
                    event_index,

                "g6_temporal_stratum":
                    first[
                        "g6_temporal_stratum"
                    ].strip(),

                "conj_id":
                    first[
                        "conj_id"
                    ].strip(),

                "obj1":
                    obj1,

                "obj2":
                    obj2,

                "replay_time":
                    replay1,
            }
        )

    if len(
        events
    ) != EXPECTED_EVENTS:
        raise CandidateImplementationError(
            "event count changed"
        )

    return events


def process_event(
    s,
    event,
    cache_by_object,
):
    """Generate model candidates only; no reference association."""

    event_row = {
        "final_g6_sample_index":
            event[
                "final_g6_sample_index"
            ],

        "g6_temporal_stratum":
            event[
                "g6_temporal_stratum"
            ],

        "conj_id":
            event[
                "conj_id"
            ],

        "obj1":
            event[
                "obj1"
            ],

        "obj2":
            event[
                "obj2"
            ],

        "replay_time_utc":
            iso_utc(
                event[
                    "replay_time"
                ]
            ),

        "obj1_GP_ID":
            str(
                cache_by_object[
                    event[
                        "obj1"
                    ]
                ][
                    "GP_ID"
                ]
            ).strip(),

        "obj2_GP_ID":
            str(
                cache_by_object[
                    event[
                        "obj2"
                    ]
                ][
                    "GP_ID"
                ]
            ).strip(),

        "parse_status":
            "NOT_EVALUATED",

        "propagation_status":
            "NOT_EVALUATED",

        "rejected_local_inconsistency_count":
            0,

        "recovered_local_inconsistency_count":
            0,

        "unresolved_boundary_count":
            0,

        "sampled_count":
            0,

        "raw_candidate_count":
            0,

        "candidate_count":
            0,

        "preoutcome_status":
            "",

        "failure_detail":
            "",
    }

    try:
        record1 = make_gp_record(
            s,
            event[
                "obj1"
            ],
            cache_by_object[
                event[
                    "obj1"
                ]
            ],
        )

        record2 = make_gp_record(
            s,
            event[
                "obj2"
            ],
            cache_by_object[
                event[
                    "obj2"
                ]
            ],
        )

        sat1 = s.satrec_from_record(
            record1
        )

        sat2 = s.satrec_from_record(
            record2
        )

    except (
        s.g4.ParseError,
        ValueError,
        TypeError,
    ) as exc:

        event_row[
            "parse_status"
        ] = "FAILED"

        event_row[
            "preoutcome_status"
        ] = "GP_PARSE_FAILURE"

        event_row[
            "failure_detail"
        ] = (
            f"{type(exc).__name__}: {exc}"
        )

        return (
            event_row,
            [],
        )

    event_row[
        "parse_status"
    ] = "SUCCESS"

    try:
        quantity = (
            s.g4.Sgp4PairQuantity(
                sat1,
                sat2,
                event[
                    "replay_time"
                ],
            )
        )

        enumeration = (
            s.enumerate_candidates_redundancy_safe(
                quantity
            )
        )

    except (
        s.UnrecoveredRefinementFailure,
        s.g4.OptimizationError,
    ) as exc:

        event_row[
            "propagation_status"
        ] = "FAILED_NUMERICAL_REFINEMENT"

        event_row[
            "preoutcome_status"
        ] = "UNRECOVERED_REFINEMENT_FAILURE"

        event_row[
            "failure_detail"
        ] = (
            f"{type(exc).__name__}: {exc}"
        )

        return (
            event_row,
            [],
        )

    except (
        s.g4.VerificationError,
        FloatingPointError,
        OverflowError,
        ValueError,
    ) as exc:

        event_row[
            "propagation_status"
        ] = "FAILED"

        event_row[
            "preoutcome_status"
        ] = "PROPAGATION_FAILURE"

        event_row[
            "failure_detail"
        ] = (
            f"{type(exc).__name__}: {exc}"
        )

        return (
            event_row,
            [],
        )

    event_row[
        "rejected_local_inconsistency_count"
    ] = (
        enumeration
        .rejected_local_inconsistency_count
    )

    event_row[
        "recovered_local_inconsistency_count"
    ] = (
        enumeration
        .recovered_local_inconsistency_count
    )

    event_row[
        "unresolved_boundary_count"
    ] = (
        enumeration
        .unresolved_boundary_count
    )

    event_row[
        "sampled_count"
    ] = (
        enumeration
        .sampled_count
    )

    event_row[
        "raw_candidate_count"
    ] = len(
        enumeration.raw_candidates
    )

    event_row[
        "candidate_count"
    ] = len(
        enumeration.candidates
    )

    event_row[
        "propagation_status"
    ] = (
        "SUCCESS_WITH_REDUNDANT_BRACKET_RECOVERY"
        if (
            enumeration
            .recovered_local_inconsistency_count
        )
        else "SUCCESS"
    )

    if not enumeration.candidates:

        event_row[
            "preoutcome_status"
        ] = "NO_VALID_CANDIDATE"

        return (
            event_row,
            [],
        )

    event_row[
        "preoutcome_status"
    ] = "CANDIDATE_SET_READY"

    candidate_rows = []

    for candidate_index, candidate in enumerate(
        enumeration.candidates,
        start=1,
    ):

        candidate_rows.append(
            {
                "final_g6_sample_index":
                    event[
                        "final_g6_sample_index"
                    ],

                "g6_temporal_stratum":
                    event[
                        "g6_temporal_stratum"
                    ],

                "conj_id":
                    event[
                        "conj_id"
                    ],

                "candidate_index":
                    candidate_index,

                "time_from_replay_s":
                    repr(
                        float(
                            candidate.time_s
                        )
                    ),

                "candidate_time_utc":
                    iso_utc(
                        event[
                            "replay_time"
                        ]
                        + timedelta(
                            seconds=float(
                                candidate.time_s
                            )
                        )
                    ),

                "distance_km":
                    repr(
                        float(
                            candidate.distance_km
                        )
                    ),

                "bracket_lower_s":
                    repr(
                        float(
                            candidate.bracket_s[
                                0
                            ]
                        )
                    ),

                "bracket_upper_s":
                    repr(
                        float(
                            candidate.bracket_s[
                                1
                            ]
                        )
                    ),

                "bracket_index":
                    int(
                        candidate.bracket_index
                    ),

                "converged":
                    bool(
                        candidate.converged
                    ),

                "iterations":
                    int(
                        candidate.iterations
                    ),

                "function_evaluations":
                    int(
                        candidate
                        .function_evaluations
                    ),

                "termination":
                    str(
                        candidate.termination
                    ),
            }
        )

    return (
        event_row,
        candidate_rows,
    )


def open_work_db(
    implementation_sha: str,
):
    """Create/resume deterministic local-only event checkpoint DB."""

    WORK_DB.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        WORK_DB
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS event_results (
            final_g6_sample_index INTEGER PRIMARY KEY,
            payload_json TEXT NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_results (
            final_g6_sample_index INTEGER NOT NULL,
            candidate_index INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (
                final_g6_sample_index,
                candidate_index
            )
        )
        """
    )

    expected_metadata = {
        "implementation_sha256":
            implementation_sha,

        "selection_sha256":
            EXPECTED_HASHES[
                SELECTION
            ],

        "selected_TLE_cache_sha256":
            EXPECTED_HASHES[
                CACHE
            ],

        "g4_numerics_sha256":
            EXPECTED_HASHES[
                G4_NUMERICS
            ],

        "g5_controlflow_sha256":
            EXPECTED_HASHES[
                G5_CONTROLFLOW
            ],

        "g6_lock_sha256":
            EXPECTED_HASHES[
                G6_LOCK
            ],

        "expected_events":
            str(
                EXPECTED_EVENTS
            ),
    }

    existing = dict(
        connection.execute(
            "SELECT key, value FROM metadata"
        ).fetchall()
    )

    if existing:

        if existing != expected_metadata:
            connection.close()

            raise CandidateImplementationError(
                "candidate work DB metadata "
                "does not match frozen execution boundary"
            )

    else:

        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            sorted(
                expected_metadata.items()
            ),
        )

        connection.commit()

    return connection


def run_generation_to_work_db():
    """Future authorized G6 propagation/candidate-generation execution."""

    frozen_hashes = (
        verify_frozen_hashes()
    )

    environment = (
        verify_environment()
    )

    s = import_frozen_controlflow()

    (
        _selection_by_object,
        cache_by_object,
        event_rows,
    ) = verify_compact_inputs()

    events = build_events(
        event_rows,
        cache_by_object,
    )

    implementation_sha = sha256(
        Path(__file__)
    )

    connection = open_work_db(
        implementation_sha
    )

    try:
        completed = {
            int(row[0])
            for row in connection.execute(
                "SELECT final_g6_sample_index "
                "FROM event_results"
            )
        }

        for event in events:

            event_index = int(
                event[
                    "final_g6_sample_index"
                ]
            )

            if event_index in completed:
                continue

            (
                event_result,
                candidate_results,
            ) = process_event(
                s,
                event,
                cache_by_object,
            )

            status = event_result[
                "preoutcome_status"
            ]

            if status not in ALLOWED_PREOUTCOME_STATUSES:
                raise CandidateImplementationError(
                    "unexpected pre-outcome status: "
                    + status
                )

            with connection:

                connection.execute(
                    "INSERT INTO event_results("
                    "final_g6_sample_index, payload_json"
                    ") VALUES (?, ?)",
                    (
                        event_index,
                        json.dumps(
                            event_result,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )

                for candidate in candidate_results:

                    connection.execute(
                        "INSERT INTO candidate_results("
                        "final_g6_sample_index, "
                        "candidate_index, "
                        "payload_json"
                        ") VALUES (?, ?, ?)",
                        (
                            event_index,
                            int(
                                candidate[
                                    "candidate_index"
                                ]
                            ),
                            json.dumps(
                                candidate,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        ),
                    )

        event_payloads = [
            json.loads(
                row[0]
            )
            for row in connection.execute(
                "SELECT payload_json "
                "FROM event_results "
                "ORDER BY final_g6_sample_index"
            )
        ]

        if len(
            event_payloads
        ) != EXPECTED_EVENTS:
            raise CandidateImplementationError(
                "candidate work DB is incomplete"
            )

        candidate_payloads = [
            json.loads(
                row[0]
            )
            for row in connection.execute(
                "SELECT payload_json "
                "FROM candidate_results "
                "ORDER BY "
                "final_g6_sample_index, "
                "candidate_index"
            )
        ]

    finally:
        connection.close()

    if (
        EVENT_OUT.exists()
        or CANDIDATE_OUT.exists()
        or SUMMARY_OUT.exists()
    ):
        raise CandidateImplementationError(
            "refusing to overwrite final "
            "candidate-generation outputs"
        )

    atomic_csv(
        EVENT_OUT,
        EVENT_FIELDS,
        event_payloads,
    )

    atomic_csv(
        CANDIDATE_OUT,
        CANDIDATE_FIELDS,
        candidate_payloads,
    )

    status_counts = Counter(
        row[
            "preoutcome_status"
        ]
        for row in event_payloads
    )

    stratum_counts = Counter(
        row[
            "g6_temporal_stratum"
        ]
        for row in event_payloads
    )

    candidate_total = sum(
        int(
            row[
                "candidate_count"
            ]
        )
        for row in event_payloads
    )

    if candidate_total != len(
        candidate_payloads
    ):
        raise CandidateImplementationError(
            "candidate count/output row mismatch"
        )

    summary = {
        "status":
            "G6_PREOUTCOME_CANDIDATE_GENERATION_PASS",

        "events":
            EXPECTED_EVENTS,

        "event_status_counts":
            dict(
                sorted(
                    status_counts.items()
                )
            ),

        "temporal_stratum_event_counts":
            dict(
                sorted(
                    stratum_counts.items()
                )
            ),

        "candidate_rows":
            len(
                candidate_payloads
            ),

        "events_with_candidate_set":
            status_counts[
                "CANDIDATE_SET_READY"
            ],

        "GP_PARSE_FAILURE":
            status_counts[
                "GP_PARSE_FAILURE"
            ],

        "PROPAGATION_FAILURE":
            status_counts[
                "PROPAGATION_FAILURE"
            ],

        "UNRECOVERED_REFINEMENT_FAILURE":
            status_counts[
                "UNRECOVERED_REFINEMENT_FAILURE"
            ],

        "NO_VALID_CANDIDATE":
            status_counts[
                "NO_VALID_CANDIDATE"
            ],

        "reference_association_performed":
            False,

        "TraCSS_reference_rows_opened":
            0,

        "protected_outcome_access":
            False,

        "G6_error_metrics_computed":
            False,

        "network_requests":
            0,

        "input_hashes":
            frozen_hashes,

        "environment":
            environment,

        "event_output":
            EVENT_OUT.relative_to(
                ROOT
            ).as_posix(),

        "event_output_sha256":
            sha256(
                EVENT_OUT
            ),

        "candidate_output":
            CANDIDATE_OUT.relative_to(
                ROOT
            ).as_posix(),

        "candidate_output_sha256":
            sha256(
                CANDIDATE_OUT
            ),

        "local_work_db":
            WORK_DB.relative_to(
                ROOT
            ).as_posix(),

        "local_work_db_committable":
            False,

        "next_safe_action":
            "INDEPENDENT_AUDIT_AND_FREEZE_CANDIDATE_OUTPUTS_BEFORE_REFERENCE_ASSOCIATION",
    }

    atomic_json(
        SUMMARY_OUT,
        summary,
    )

    return summary


def verify_numerical_constants(
    s,
):
    expected = {
        "PRIMARY_HORIZON_S":
            432000.0,

        "COARSE_STEP_S":
            10.0,

        "REFINEMENT_TOLERANCE_S":
            0.01,

        "DEDUP_TIME_TOLERANCE_S":
            0.01,

        "DEDUP_DISTANCE_TOLERANCE_KM":
            1.0e-6,

        "ASSOCIATION_TOLERANCE_MIN":
            5.0,

        "RECOVERY_GRID_STEP_S":
            0.01,

        "RECOVERY_WINDOW_EXPANSION_S":
            5.0,

        "RECOVERY_TCA_TOLERANCE_S":
            0.5,

        "RECOVERY_DISTANCE_TOLERANCE_KM":
            1.0e-6,
    }

    actual = {
        "PRIMARY_HORIZON_S":
            float(
                s.g4.PRIMARY_HORIZON_S
            ),

        "COARSE_STEP_S":
            float(
                s.COARSE_STEP_S
            ),

        "REFINEMENT_TOLERANCE_S":
            float(
                s.REFINEMENT_TOLERANCE_S
            ),

        "DEDUP_TIME_TOLERANCE_S":
            float(
                s.DEDUP_TIME_TOLERANCE_S
            ),

        "DEDUP_DISTANCE_TOLERANCE_KM":
            float(
                s.DEDUP_DISTANCE_TOLERANCE_KM
            ),

        "ASSOCIATION_TOLERANCE_MIN":
            float(
                s.ASSOCIATION_TOLERANCE_MIN
            ),

        "RECOVERY_GRID_STEP_S":
            float(
                s.RECOVERY_GRID_STEP_S
            ),

        "RECOVERY_WINDOW_EXPANSION_S":
            float(
                s.RECOVERY_WINDOW_EXPANSION_S
            ),

        "RECOVERY_TCA_TOLERANCE_S":
            float(
                s.RECOVERY_TCA_TOLERANCE_S
            ),

        "RECOVERY_DISTANCE_TOLERANCE_KM":
            float(
                s.RECOVERY_DISTANCE_TOLERANCE_KM
            ),
    }

    if actual != expected:
        raise CandidateImplementationError(
            f"frozen numerical constants changed: {actual}"
        )

    return actual


def synthetic_controlflow_test(
    s,
):
    """Exercise frozen enumerator without orbital propagation.

    Two analytical cases are used:

    1. An off-grid single minimum.  This is the nominal smoke test and must
       deduplicate to exactly one final candidate.

    2. A minimum exactly on the frozen 10 s coarse grid.  This intentionally
       exercises overlapping bracket-detection paths.  At least one accurate
       candidate must survive, but the frozen two-dimensional deduplication
       tolerance is allowed to retain more than one numerical representation.
    """

    class AnalyticQuantity:

        def __init__(
            self,
            minimum_time_s: float,
            minimum_distance_km: float,
        ):
            self.minimum_time_s = float(
                minimum_time_s
            )

            self.minimum_distance_km = float(
                minimum_distance_km
            )

        def evaluate(
            self,
            time_s: float,
        ):
            x = (
                float(
                    time_s
                )
                - self.minimum_time_s
            )

            q = (
                x * x
                + self.minimum_distance_km
                * self.minimum_distance_km
            )

            qdot = 2.0 * x

            return (
                q,
                qdot,
            )

        def evaluate_many(
            self,
            times_s,
        ):
            times = np.asarray(
                times_s,
                dtype=float,
            )

            x = (
                times
                - self.minimum_time_s
            )

            q = (
                x * x
                + self.minimum_distance_km
                * self.minimum_distance_km
            )

            qdot = (
                2.0
                * x
            )

            return (
                q,
                qdot,
            )

    def run_case(
        target_time: float,
        target_distance: float,
    ):
        result = (
            s.enumerate_candidates_redundancy_safe(
                AnalyticQuantity(
                    target_time,
                    target_distance,
                )
            )
        )

        if not result.candidates:
            raise CandidateImplementationError(
                "synthetic enumerator produced no candidate"
            )

        closest = min(
            result.candidates,
            key=lambda candidate:
                abs(
                    candidate.time_s
                    - target_time
                ),
        )

        time_error = abs(
            float(
                closest.time_s
            )
            - target_time
        )

        distance_error = abs(
            float(
                closest.distance_km
            )
            - target_distance
        )

        if (
            time_error
            > s.REFINEMENT_TOLERANCE_S
        ):
            raise CandidateImplementationError(
                "synthetic candidate TCA failed tolerance"
            )

        if (
            distance_error
            > s.DEDUP_DISTANCE_TOLERANCE_KM
        ):
            raise CandidateImplementationError(
                "synthetic candidate distance failed tolerance"
            )

        return (
            result,
            closest,
            time_error,
            distance_error,
        )

    # --------------------------------------------------------
    # Nominal off-grid minimum
    # --------------------------------------------------------

    nominal_time = 123456.789
    nominal_distance = 5.0

    (
        nominal_result,
        nominal_closest,
        nominal_time_error,
        nominal_distance_error,
    ) = run_case(
        nominal_time,
        nominal_distance,
    )

    if len(
        nominal_result.candidates
    ) != 1:
        raise CandidateImplementationError(
            "off-grid single-minimum synthetic test "
            "did not deduplicate to exactly one candidate"
        )

    # --------------------------------------------------------
    # Grid-aligned edge case
    # --------------------------------------------------------

    grid_time = 123450.0
    grid_distance = 5.0

    (
        grid_result,
        grid_closest,
        grid_time_error,
        grid_distance_error,
    ) = run_case(
        grid_time,
        grid_distance,
    )

    return {
        "nominal_offgrid_case": {
            "target_time_s":
                nominal_time,

            "recovered_time_s":
                float(
                    nominal_closest.time_s
                ),

            "absolute_time_error_s":
                nominal_time_error,

            "target_distance_km":
                nominal_distance,

            "recovered_distance_km":
                float(
                    nominal_closest.distance_km
                ),

            "absolute_distance_error_km":
                nominal_distance_error,

            "candidate_count":
                len(
                    nominal_result.candidates
                ),

            "rejected_local_inconsistency_count":
                nominal_result
                .rejected_local_inconsistency_count,

            "recovered_local_inconsistency_count":
                nominal_result
                .recovered_local_inconsistency_count,
        },

        "grid_aligned_edge_case": {
            "target_time_s":
                grid_time,

            "recovered_time_s":
                float(
                    grid_closest.time_s
                ),

            "absolute_time_error_s":
                grid_time_error,

            "target_distance_km":
                grid_distance,

            "recovered_distance_km":
                float(
                    grid_closest.distance_km
                ),

            "absolute_distance_error_km":
                grid_distance_error,

            "candidate_count":
                len(
                    grid_result.candidates
                ),

            "rejected_local_inconsistency_count":
                grid_result
                .rejected_local_inconsistency_count,

            "recovered_local_inconsistency_count":
                grid_result
                .recovered_local_inconsistency_count,

            "interpretation":
                "COARSE_GRID_ALIGNED_EDGE_CASE; "
                "MULTIPLE NUMERICAL REPRESENTATIONS ARE PERMITTED "
                "UNDER FROZEN TIME_AND_DISTANCE DEDUPLICATION",
        },
    }


def offline_validate():

    if OFFLINE_VALIDATION.exists():
        raise CandidateImplementationError(
            "offline-validation artifact already exists"
        )

    verify_repository_boundary()

    frozen_hashes = (
        verify_frozen_hashes()
    )

    environment = (
        verify_environment()
    )

    (
        _selection_by_object,
        cache_by_object,
        event_rows,
    ) = verify_compact_inputs()

    events = build_events(
        event_rows,
        cache_by_object,
    )

    s = import_frozen_controlflow()

    constants = (
        verify_numerical_constants(
            s
        )
    )

    synthetic = (
        synthetic_controlflow_test(
            s
        )
    )

    # Future local work DB must remain local-only.
    ignore = git(
        "check-ignore",
        "-v",
        WORK_DB.relative_to(
            ROOT
        ).as_posix(),
        check=False,
    )

    if ignore.returncode != 0:
        raise CandidateImplementationError(
            "candidate work DB is not Git-ignored"
        )

    if git_value(
        "ls-files",
        "--",
        WORK_DB.relative_to(
            ROOT
        ).as_posix(),
    ):
        raise CandidateImplementationError(
            "candidate work DB is unexpectedly tracked"
        )

    result = {
        "status":
            "G6_PREOUTCOME_CANDIDATE_IMPLEMENTATION_OFFLINE_VALIDATION_PASS",

        "implementation_sha256":
            sha256(
                Path(__file__)
            ),

        "events_verified":
            len(
                events
            ),

        "objects_verified":
            len(
                cache_by_object
            ),

        "frozen_input_hashes":
            frozen_hashes,

        "environment":
            environment,

        "numerical_constants":
            constants,

        "gravity_constants":
            "WGS-72",

        "coordinate_frame":
            "TEME",

        "time_scale":
            "UTC",

        "candidate_horizon_days":
            5.0,

        "candidate_horizon_seconds":
            432000.0,

        "synthetic_controlflow_test":
            synthetic,

        "synthetic_candidate_enumeration_executed":
            True,

        "G6_orbital_propagation_performed":
            False,

        "G6_candidate_generation_executed":
            False,

        "reference_association_performed":
            False,

        "TraCSS_reference_rows_opened":
            0,

        "protected_outcome_access":
            False,

        "G6_error_metrics_computed":
            False,

        "network_requests":
            0,

        "full_GP_HISTORY_reread":
            False,

        "candidate_work_db_gitignored":
            True,

        "candidate_work_db_tracked":
            False,

        "allowed_preoutcome_statuses":
            sorted(
                ALLOWED_PREOUTCOME_STATUSES
            ),

        "final_association_statuses_emitted":
            False,

        "generation_authorized":
            False,

        "next_safe_action":
            "INDEPENDENT_REVIEW_AND_FREEZE_INACTIVE_CANDIDATE_IMPLEMENTATION",
    }

    atomic_json(
        OFFLINE_VALIDATION,
        result,
    )

    print(
        json.dumps(
            {
                **result,

                "offline_validation_sha256":
                    sha256(
                        OFFLINE_VALIDATION
                    ),
            },
            indent=2,
            sort_keys=True,
        )
    )


def generate():
    raise CandidateImplementationError(
        "G6_CANDIDATE_GENERATION_INACTIVE_UNTIL_INDEPENDENT_REVIEW_AND_AUTHORIZATION"
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
        "--generate",
        action="store_true",
    )

    args = parser.parse_args()

    if args.offline_validate:
        offline_validate()
        return 0

    if args.generate:
        generate()
        return 0

    raise CandidateImplementationError(
        "unreachable command state"
    )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
