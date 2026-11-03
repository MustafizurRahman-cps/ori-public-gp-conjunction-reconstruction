#!/usr/bin/env python3
"""G6 frozen reference-TCA association implementation.

The candidate set is already frozen by DR-0029.

--offline-validate
    Verifies the frozen candidate/sample boundary and exercises only
    synthetic association cases through the already-frozen G4 association
    primitive.

--associate
    Deliberately disabled until independent review and execution
    authorization.

Real G6 association uses only the already-allowed reference TCA metadata.
No reference miss distance, TraCSS physical miss distance, E_d, E_t,
coverage statistic, covariance, Pc, or HBR is read or produced here.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import inspect
import json
import math
import os
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

EXPECTED_HEAD = (
    "cb0981bfea448a45beefed2112df16d4ee6f8c4c"
)

EXPECTED_TAG = (
    "g6-preoutcome-candidate-outputs-frozen"
)

EXPECTED_EVENTS = 6927
EXPECTED_READY = 6906
EXPECTED_NONREADY = 21
EXPECTED_CANDIDATE_ROWS = 929821

ASSOCIATION_TOLERANCE_MIN = 5.0

SAMPLE = (
    ROOT
    / "docs/research/G6_Final_Confirmatory_Sample_Register.csv"
)

EVENT_STATUS = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Event_Status.csv"
)

CANDIDATES = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidates.csv"
)

CANDIDATE_SUMMARY = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Summary.json"
)

OUTPUT_AUDIT = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Output_Independent_Audit.json"
)

LOCAL_MANIFEST = (
    ROOT
    / "results/g6/G6_PreOutcome_Candidate_Local_Artifact_Manifest.json"
)

G6_LOCK = (
    ROOT
    / "docs/research/G6_PreOutcome_Confirmatory_Lock.md"
)

G4_NUMERICS = (
    ROOT
    / "scripts/g4/numerics.py"
)

OFFLINE_VALIDATION = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association_Implementation_Offline_Validation.json"
)

ASSOCIATION_OUT = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association.csv"
)

ASSOCIATION_SUMMARY = (
    ROOT
    / "results/g6/G6_Reference_TCA_Association_Summary.json"
)


EXPECTED_HASHES = {
    SAMPLE:
        "f8a103cbd268e7b6d3e95515e8a806da403511196145897997866adc1fab10ef",

    EVENT_STATUS:
        "844a4c3f90a1b5bb738f13911b5e9f99f22bfe72e01bdb43dd6242e292b7b308",

    CANDIDATES:
        "21b8a9f5c8aab4b3427fad5cd3f3c2823ffc0219bd2a36aeccafc6a0aaa80e36",

    CANDIDATE_SUMMARY:
        "a9a070163d744472e641f9f75e09ebadc42c03151c8c64a3e64f0429f51a8add",

    OUTPUT_AUDIT:
        "12733daa391701aaa3bce771efc4ed6fc50141e8dfc7801aad942b352bb0570b",

    LOCAL_MANIFEST:
        "6e0edc1e1dc7023b909e69b4fc3ff19989e3e3fe87e1c737f10cd2b0b3bfd813",

    G6_LOCK:
        "21fc2d4eb83b8d00d7bbb632986fcdc33ec437dce9ca3e69020b222d382adb2e",

    G4_NUMERICS:
        "dc8de410b89e05ffd829563b4996b06703c31236e9a10fb56bd7f4e690048406",
}


EXPECTED_CANDIDATE_BYTES = 128418422


EXPECTED_SAMPLE_FIELDS = (
    "final_g6_sample_index",
    "reserve_event_index",
    "g6_temporal_stratum",
    "conj_id",
    "obj1",
    "obj2",
    "reference_tca_utc",
    "source_class",
    "iadc_leo_eligible",
    "boundary_sensitive_flag",
    "G2_exposure_status",
    "allocation_status",
    "final_g6_status",
)


OUTPUT_FIELDS = (
    "final_g6_sample_index",
    "g6_temporal_stratum",
    "conj_id",
    "input_preoutcome_status",
    "association_status",
    "final_status",
    "selected_candidate_index",
)


class AssociationImplementationError(RuntimeError):
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

    return git(
        *args
    ).stdout.strip()


def parse_utc(value: str) -> datetime:

    text = str(value).strip()

    if not text:
        raise AssociationImplementationError(
            "empty UTC timestamp"
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
        raise AssociationImplementationError(
            f"malformed UTC timestamp: {value!r}"
        ) from exc

    if parsed.tzinfo is None:
        raise AssociationImplementationError(
            "naive UTC timestamp is prohibited"
        )

    return parsed.astimezone(
        UTC
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
            writer.writerows(rows)

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

    if (
        git_value(
            "rev-parse",
            "HEAD",
        )
        != EXPECTED_HEAD
    ):
        raise AssociationImplementationError(
            "unexpected repository HEAD"
        )

    if (
        git_value(
            "rev-parse",
            f"refs/tags/{EXPECTED_TAG}^{{commit}}",
        )
        != EXPECTED_HEAD
    ):
        raise AssociationImplementationError(
            "DR-0029 freeze tag target changed"
        )

    if git_value(
        "status",
        "--porcelain",
        "--untracked-files=no",
    ):
        raise AssociationImplementationError(
            "tracked working tree is not clean"
        )


def verify_frozen_hashes() -> dict[str, str]:

    verified = {}

    for path, expected in EXPECTED_HASHES.items():

        if not path.is_file():
            raise AssociationImplementationError(
                f"missing frozen dependency: {path}"
            )

        actual = sha256(
            path
        )

        if actual != expected:
            raise AssociationImplementationError(
                "frozen dependency SHA mismatch\n"
                f"path={path}\n"
                f"actual={actual}\n"
                f"expected={expected}"
            )

        verified[
            path.relative_to(
                ROOT
            ).as_posix()
        ] = actual

    if (
        CANDIDATES.stat().st_size
        != EXPECTED_CANDIDATE_BYTES
    ):
        raise AssociationImplementationError(
            "frozen candidate CSV byte count changed"
        )

    return verified


def import_g4():

    spec = (
        importlib.util.spec_from_file_location(
            "g6_frozen_g4_numerics",
            G4_NUMERICS,
        )
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise AssociationImplementationError(
            "could not import frozen G4 numerics"
        )

    module = (
        importlib.util.module_from_spec(
            spec
        )
    )

    sys.modules[
        spec.name
    ] = module

    spec.loader.exec_module(
        module
    )

    return module


def verify_association_signature(
    g4,
) -> dict:

    signature = inspect.signature(
        g4.associate_reference_event
    )

    parameters = list(
        signature.parameters
    )

    if parameters[:3] != [
        "frozen_candidates",
        "reference_time_s",
        "delta_assoc_min",
    ]:
        raise AssociationImplementationError(
            "frozen association positional signature changed"
        )

    if (
        "time_equality_tolerance_s"
        in signature.parameters
    ):
        raise AssociationImplementationError(
            "unexpected association equality/tie band present"
        )

    if (
        "reference_distance_km"
        not in signature.parameters
    ):
        raise AssociationImplementationError(
            "reference_distance_km interface missing"
        )

    return {
        "signature":
            str(signature),

        "no_equality_band":
            True,

        "association_tolerance_min":
            ASSOCIATION_TOLERANCE_MIN,

        "ordering":
            (
                "MIN_ABSOLUTE_REFERENCE_TCA_OFFSET__"
                "EARLIER_CANDIDATE_TIME__"
                "SMALLER_CANDIDATE_DISTANCE__"
                "LOWER_STABLE_CANDIDATE_INDEX"
            ),
    }


def read_sample():

    result = {}

    with SAMPLE.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        reader = csv.DictReader(
            stream
        )

        if tuple(
            reader.fieldnames or ()
        ) != EXPECTED_SAMPLE_FIELDS:
            raise AssociationImplementationError(
                "final G6 sample schema changed"
            )

        forbidden_fields = {
            "min_range",
            "mdistance",
            "Pc",
            "HBR",
            "covariance",
            "E_d",
            "E_t",
        }

        if forbidden_fields.intersection(
            set(
                reader.fieldnames or ()
            )
        ):
            raise AssociationImplementationError(
                "protected outcome field entered sample register"
            )

        for expected_index, row in enumerate(
            reader,
            start=1,
        ):

            index = int(
                row[
                    "final_g6_sample_index"
                ]
            )

            if index != expected_index:
                raise AssociationImplementationError(
                    "sample register is not canonical contiguous order"
                )

            if index in result:
                raise AssociationImplementationError(
                    "duplicate final sample index"
                )

            parse_utc(
                row[
                    "reference_tca_utc"
                ]
            )

            result[
                index
            ] = row

    if len(
        result
    ) != EXPECTED_EVENTS:
        raise AssociationImplementationError(
            "sample event count changed"
        )

    return result


def read_event_status():

    result = {}
    census = Counter()

    with EVENT_STATUS.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        reader = csv.DictReader(
            stream
        )

        for expected_index, row in enumerate(
            reader,
            start=1,
        ):

            index = int(
                row[
                    "final_g6_sample_index"
                ]
            )

            if index != expected_index:
                raise AssociationImplementationError(
                    "event status is not canonical contiguous order"
                )

            if index in result:
                raise AssociationImplementationError(
                    "duplicate event-status index"
                )

            parse_utc(
                row[
                    "replay_time_utc"
                ]
            )

            result[
                index
            ] = row

            census[
                row[
                    "preoutcome_status"
                ].strip()
            ] += 1

    if len(
        result
    ) != EXPECTED_EVENTS:
        raise AssociationImplementationError(
            "event-status count changed"
        )

    expected_census = {
        "CANDIDATE_SET_READY":
            6906,

        "PROPAGATION_FAILURE":
            1,

        "UNRECOVERED_REFINEMENT_FAILURE":
            20,
    }

    if dict(
        census
    ) != expected_census:
        raise AssociationImplementationError(
            f"pre-outcome status census changed: {dict(census)}"
        )

    return (
        result,
        census,
    )


def verify_sample_event_alignment(
    sample,
    event_status,
) -> None:

    if set(
        sample
    ) != set(
        event_status
    ):
        raise AssociationImplementationError(
            "sample/event-status membership differs"
        )

    for index in sample:

        source = sample[
            index
        ]

        event = event_status[
            index
        ]

        if (
            source[
                "conj_id"
            ].strip()
            != event[
                "conj_id"
            ].strip()
        ):
            raise AssociationImplementationError(
                f"conjunction-ID mismatch at event {index}"
            )

        if (
            source[
                "g6_temporal_stratum"
            ].strip()
            != event[
                "g6_temporal_stratum"
            ].strip()
        ):
            raise AssociationImplementationError(
                f"stratum mismatch at event {index}"
            )

        if (
            source[
                "obj1"
            ].strip()
            != event[
                "obj1"
            ].strip()
            or source[
                "obj2"
            ].strip()
            != event[
                "obj2"
            ].strip()
        ):
            raise AssociationImplementationError(
                f"object-ID mismatch at event {index}"
            )


def verify_local_manifest() -> dict:

    manifest = json.loads(
        LOCAL_MANIFEST.read_text(
            encoding="utf-8"
        )
    )

    if (
        manifest.get(
            "status"
        )
        != "G6_PREOUTCOME_CANDIDATE_LOCAL_ARTIFACT_MANIFEST_FROZEN"
    ):
        raise AssociationImplementationError(
            "local candidate manifest is not frozen"
        )

    candidate = manifest.get(
        "candidate_csv",
        {}
    )

    if (
        candidate.get(
            "sha256"
        )
        != EXPECTED_HASHES[
            CANDIDATES
        ]
        or int(
            candidate.get(
                "byte_count",
                -1,
            )
        )
        != EXPECTED_CANDIDATE_BYTES
        or int(
            candidate.get(
                "row_count",
                -1,
            )
        )
        != EXPECTED_CANDIDATE_ROWS
        or candidate.get(
            "git_tracked"
        )
        is not False
        or candidate.get(
            "git_ignored"
        )
        is not True
        or candidate.get(
            "required_for_reference_association"
        )
        is not True
    ):
        raise AssociationImplementationError(
            "local candidate manifest invariant mismatch"
        )

    relative = (
        CANDIDATES.relative_to(
            ROOT
        ).as_posix()
    )

    ignored = git(
        "check-ignore",
        "-v",
        relative,
        check=False,
    )

    if ignored.returncode != 0:
        raise AssociationImplementationError(
            "candidate CSV is not Git-ignored"
        )

    if git_value(
        "ls-files",
        "--",
        relative,
    ):
        raise AssociationImplementationError(
            "candidate CSV is unexpectedly tracked"
        )

    return manifest


def make_candidate(
    g4,
    row,
):

    converged_text = (
        row[
            "converged"
        ].strip()
    )

    if converged_text not in {
        "True",
        "False",
    }:
        raise AssociationImplementationError(
            "candidate converged field malformed"
        )

    candidate = g4.Candidate(
        time_s=float(
            row[
                "time_from_replay_s"
            ]
        ),

        distance_km=float(
            row[
                "distance_km"
            ]
        ),

        bracket_s=(
            float(
                row[
                    "bracket_lower_s"
                ]
            ),
            float(
                row[
                    "bracket_upper_s"
                ]
            ),
        ),

        bracket_index=int(
            row[
                "bracket_index"
            ]
        ),

        converged=(
            converged_text
            == "True"
        ),

        iterations=int(
            row[
                "iterations"
            ]
        ),

        function_evaluations=int(
            row[
                "function_evaluations"
            ]
        ),

        termination=str(
            row[
                "termination"
            ]
        ),
    )

    values = (
        candidate.time_s,
        candidate.distance_km,
        candidate.bracket_s[0],
        candidate.bracket_s[1],
    )

    if not all(
        math.isfinite(value)
        for value in values
    ):
        raise AssociationImplementationError(
            "nonfinite frozen candidate"
        )

    return candidate


def associate_one_ready_event(
    g4,
    sample_row,
    event_row,
    candidate_rows,
):

    if (
        event_row[
            "preoutcome_status"
        ].strip()
        != "CANDIDATE_SET_READY"
    ):
        raise AssociationImplementationError(
            "association called on non-ready event"
        )

    expected_count = int(
        event_row[
            "candidate_count"
        ]
    )

    if (
        len(
            candidate_rows
        )
        != expected_count
    ):
        raise AssociationImplementationError(
            "candidate count mismatch before association"
        )

    candidates = []

    candidate_indices = []

    for expected_candidate_index, row in enumerate(
        candidate_rows,
        start=1,
    ):

        candidate_index = int(
            row[
                "candidate_index"
            ]
        )

        if (
            candidate_index
            != expected_candidate_index
        ):
            raise AssociationImplementationError(
                "candidate index sequence changed"
            )

        candidates.append(
            make_candidate(
                g4,
                row,
            )
        )

        candidate_indices.append(
            candidate_index
        )

    replay = parse_utc(
        event_row[
            "replay_time_utc"
        ]
    )

    reference_tca = parse_utc(
        sample_row[
            "reference_tca_utc"
        ]
    )

    reference_time_s = (
        reference_tca
        - replay
    ).total_seconds()

    association = (
        g4.associate_reference_event(
            tuple(
                candidates
            ),
            reference_time_s,
            ASSOCIATION_TOLERANCE_MIN,
            reference_distance_km=None,
        )
    )

    if association.status not in {
        "VALID_ASSOCIATED",
        "UNASSOCIATED_REFERENCE_EVENT",
    }:
        raise AssociationImplementationError(
            "unexpected frozen association status"
        )

    # Deliberately do not read/export:
    #
    # association.tca_error_s
    # association.miss_distance_error_km
    # selected candidate time
    # selected candidate distance
    #
    # This stage freezes association identity/status only.

    selected_index = ""

    if association.status == "VALID_ASSOCIATED":

        if association.candidate is None:
            raise AssociationImplementationError(
                "VALID_ASSOCIATED lacks selected candidate"
            )

        try:
            position = candidates.index(
                association.candidate
            )

        except ValueError as exc:
            raise AssociationImplementationError(
                "associated candidate not found in frozen candidate set"
            ) from exc

        selected_index = str(
            candidate_indices[
                position
            ]
        )

    else:

        if association.candidate is not None:
            raise AssociationImplementationError(
                "unassociated event unexpectedly contains candidate"
            )

    return {
        "association_status":
            association.status,

        "selected_candidate_index":
            selected_index,
    }


def run_association():
    """Future authorized G6 association execution."""

    verify_frozen_hashes()

    sample = read_sample()

    (
        event_status,
        _census,
    ) = read_event_status()

    verify_sample_event_alignment(
        sample,
        event_status,
    )

    verify_local_manifest()

    g4 = import_g4()

    verify_association_signature(
        g4
    )

    if (
        ASSOCIATION_OUT.exists()
        or ASSOCIATION_SUMMARY.exists()
    ):
        raise AssociationImplementationError(
            "refusing to overwrite association outputs"
        )

    ready_indices = {
        index
        for index, row
        in event_status.items()
        if row[
            "preoutcome_status"
        ].strip()
        == "CANDIDATE_SET_READY"
    }

    results = {}

    for index, row in event_status.items():

        status = row[
            "preoutcome_status"
        ].strip()

        if status == "CANDIDATE_SET_READY":
            continue

        results[
            index
        ] = {
            "final_g6_sample_index":
                index,

            "g6_temporal_stratum":
                row[
                    "g6_temporal_stratum"
                ].strip(),

            "conj_id":
                row[
                    "conj_id"
                ].strip(),

            "input_preoutcome_status":
                status,

            "association_status":
                "NOT_APPLICABLE_PREOUTCOME_FAILURE",

            "final_status":
                status,

            "selected_candidate_index":
                "",
        }

    current_event = None
    current_rows = []

    def finalize_ready(
        event_index,
        rows,
    ):
        if event_index is None:
            return

        if event_index not in ready_indices:
            raise AssociationImplementationError(
                "candidate rows emitted for non-ready event"
            )

        event = event_status[
            event_index
        ]

        source = sample[
            event_index
        ]

        association = (
            associate_one_ready_event(
                g4,
                source,
                event,
                rows,
            )
        )

        results[
            event_index
        ] = {
            "final_g6_sample_index":
                event_index,

            "g6_temporal_stratum":
                event[
                    "g6_temporal_stratum"
                ].strip(),

            "conj_id":
                event[
                    "conj_id"
                ].strip(),

            "input_preoutcome_status":
                "CANDIDATE_SET_READY",

            "association_status":
                association[
                    "association_status"
                ],

            "final_status":
                association[
                    "association_status"
                ],

            "selected_candidate_index":
                association[
                    "selected_candidate_index"
                ],
        }

    rows_seen = 0

    with CANDIDATES.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        reader = csv.DictReader(
            stream
        )

        for row in reader:

            rows_seen += 1

            event_index = int(
                row[
                    "final_g6_sample_index"
                ]
            )

            if (
                current_event is None
            ):
                current_event = (
                    event_index
                )

            if (
                event_index
                != current_event
            ):

                finalize_ready(
                    current_event,
                    current_rows,
                )

                current_event = (
                    event_index
                )

                current_rows = []

            current_rows.append(
                row
            )

    finalize_ready(
        current_event,
        current_rows,
    )

    if (
        rows_seen
        != EXPECTED_CANDIDATE_ROWS
    ):
        raise AssociationImplementationError(
            "candidate row count changed during association"
        )

    if (
        set(
            results
        )
        != set(
            range(
                1,
                EXPECTED_EVENTS + 1,
            )
        )
    ):
        raise AssociationImplementationError(
            "association result event accounting incomplete"
        )

    ordered = [
        results[
            index
        ]
        for index in range(
            1,
            EXPECTED_EVENTS + 1,
        )
    ]

    final_counts = Counter(
        row[
            "final_status"
        ]
        for row in ordered
    )

    if (
        final_counts[
            "PROPAGATION_FAILURE"
        ]
        != 1
        or final_counts[
            "UNRECOVERED_REFINEMENT_FAILURE"
        ]
        != 20
    ):
        raise AssociationImplementationError(
            "pre-association terminal failures changed"
        )

    if (
        final_counts[
            "VALID_ASSOCIATED"
        ]
        + final_counts[
            "UNASSOCIATED_REFERENCE_EVENT"
        ]
        != EXPECTED_READY
    ):
        raise AssociationImplementationError(
            "association accounting does not equal 6906 ready events"
        )

    atomic_csv(
        ASSOCIATION_OUT,
        OUTPUT_FIELDS,
        ordered,
    )

    summary = {
        "status":
            "G6_REFERENCE_TCA_ASSOCIATION_PASS",

        "events":
            EXPECTED_EVENTS,

        "candidate_set_ready_input_events":
            EXPECTED_READY,

        "preoutcome_terminal_events":
            EXPECTED_NONREADY,

        "final_status_counts":
            dict(
                sorted(
                    final_counts.items()
                )
            ),

        "association_tolerance_min":
            ASSOCIATION_TOLERANCE_MIN,

        "association_rule":
            (
                "MIN_ABSOLUTE_REFERENCE_TCA_OFFSET__"
                "EARLIER_CANDIDATE_TIME__"
                "SMALLER_CANDIDATE_DISTANCE__"
                "LOWER_STABLE_CANDIDATE_INDEX"
            ),

        "association_equality_band":
            None,

        "reference_TCA_source":
            "FROZEN_G6_SAMPLE_REGISTER_METADATA",

        "reference_miss_distance_read":
            False,

        "selected_candidate_distance_exported":
            False,

        "selected_candidate_time_exported":
            False,

        "signed_TCA_residual_exported":
            False,

        "absolute_TCA_error_E_t_computed":
            False,

        "miss_distance_error_E_d_computed":
            False,

        "coverage_computed":
            False,

        "TraCSS_physical_outcome_rows_opened":
            0,

        "covariance_access":
            False,

        "Pc_access":
            False,

        "HBR_access":
            False,

        "orbital_propagation_performed":
            False,

        "candidate_generation_performed":
            False,

        "method_retuning_performed":
            False,

        "network_requests":
            0,

        "association_output":
            ASSOCIATION_OUT.relative_to(
                ROOT
            ).as_posix(),

        "association_output_sha256":
            sha256(
                ASSOCIATION_OUT
            ),

        "next_safe_action":
            "INDEPENDENT_AUDIT_AND_FREEZE_REFERENCE_TCA_ASSOCIATION_OUTPUTS_BEFORE_ERROR_METRICS",
    }

    atomic_json(
        ASSOCIATION_SUMMARY,
        summary,
    )

    return summary


def synthetic_association_tests(
    g4,
) -> dict:

    def candidate(
        time_s,
        distance_km,
        bracket_index,
    ):
        return g4.Candidate(
            time_s=float(
                time_s
            ),

            distance_km=float(
                distance_km
            ),

            bracket_s=(
                max(
                    0.0,
                    float(
                        time_s
                    )
                    - 1.0,
                ),
                float(
                    time_s
                )
                + 1.0,
            ),

            bracket_index=int(
                bracket_index
            ),

            converged=True,
            iterations=1,
            function_evaluations=1,
            termination="SYNTHETIC",
        )

    # Equal absolute offsets: earlier candidate time must win.
    earlier = candidate(
        100.0,
        9.0,
        10,
    )

    later = candidate(
        200.0,
        1.0,
        20,
    )

    result = g4.associate_reference_event(
        (
            later,
            earlier,
        ),
        150.0,
        ASSOCIATION_TOLERANCE_MIN,
        reference_distance_km=None,
    )

    if (
        result.status
        != "VALID_ASSOCIATED"
        or result.candidate
        != earlier
    ):
        raise AssociationImplementationError(
            "earlier-time association tie-break failed"
        )

    if (
        result.miss_distance_error_km
        is not None
    ):
        raise AssociationImplementationError(
            "synthetic association unexpectedly computed miss error"
        )

    # Equal time: smaller candidate distance wins.
    large_distance = candidate(
        100.0,
        3.0,
        10,
    )

    small_distance = candidate(
        100.0,
        2.0,
        20,
    )

    result_distance = (
        g4.associate_reference_event(
            (
                large_distance,
                small_distance,
            ),
            100.0,
            ASSOCIATION_TOLERANCE_MIN,
            reference_distance_km=None,
        )
    )

    if (
        result_distance.candidate
        != small_distance
    ):
        raise AssociationImplementationError(
            "distance association tie-break failed"
        )

    # Equal time and distance: lower stable bracket index wins.
    high_index = candidate(
        100.0,
        2.0,
        20,
    )

    low_index = candidate(
        100.0,
        2.0,
        10,
    )

    result_index = (
        g4.associate_reference_event(
            (
                high_index,
                low_index,
            ),
            100.0,
            ASSOCIATION_TOLERANCE_MIN,
            reference_distance_km=None,
        )
    )

    if (
        result_index.candidate
        != low_index
    ):
        raise AssociationImplementationError(
            "stable-index association tie-break failed"
        )

    # Inclusive 5-minute boundary.
    boundary = candidate(
        300.0,
        1.0,
        1,
    )

    result_boundary = (
        g4.associate_reference_event(
            (
                boundary,
            ),
            0.0,
            ASSOCIATION_TOLERANCE_MIN,
            reference_distance_km=None,
        )
    )

    if (
        result_boundary.status
        != "VALID_ASSOCIATED"
    ):
        raise AssociationImplementationError(
            "5-minute inclusive boundary failed"
        )

    # Immediately outside 5-minute boundary.
    outside = candidate(
        300.000001,
        1.0,
        1,
    )

    result_outside = (
        g4.associate_reference_event(
            (
                outside,
            ),
            0.0,
            ASSOCIATION_TOLERANCE_MIN,
            reference_distance_km=None,
        )
    )

    if (
        result_outside.status
        != "UNASSOCIATED_REFERENCE_EVENT"
        or result_outside.candidate
        is not None
    ):
        raise AssociationImplementationError(
            "outside-boundary association semantics failed"
        )

    empty = (
        g4.associate_reference_event(
            (),
            0.0,
            ASSOCIATION_TOLERANCE_MIN,
            reference_distance_km=None,
        )
    )

    if (
        empty.status
        != "UNASSOCIATED_REFERENCE_EVENT"
    ):
        raise AssociationImplementationError(
            "empty-candidate association semantics failed"
        )

    return {
        "equal_absolute_offset_selects_earlier_time":
            True,

        "equal_time_selects_smaller_distance":
            True,

        "equal_time_distance_selects_lower_stable_index":
            True,

        "five_minute_boundary_inclusive":
            True,

        "outside_five_minute_boundary_unassociated":
            True,

        "empty_candidate_set_unassociated":
            True,

        "reference_distance_supplied":
            False,

        "miss_distance_error_returned":
            False,
    }


def offline_validate():

    if OFFLINE_VALIDATION.exists():
        raise AssociationImplementationError(
            "offline-validation artifact already exists"
        )

    verify_repository_boundary()

    hashes = verify_frozen_hashes()

    manifest = verify_local_manifest()

    sample = read_sample()

    (
        event_status,
        census,
    ) = read_event_status()

    verify_sample_event_alignment(
        sample,
        event_status,
    )

    if (
        ASSOCIATION_OUT.exists()
        or ASSOCIATION_SUMMARY.exists()
    ):
        raise AssociationImplementationError(
            "association output unexpectedly exists before authorization"
        )

    g4 = import_g4()

    signature = (
        verify_association_signature(
            g4
        )
    )

    synthetic = (
        synthetic_association_tests(
            g4
        )
    )

    result = {
        "status":
            "G6_REFERENCE_TCA_ASSOCIATION_IMPLEMENTATION_OFFLINE_VALIDATION_PASS",

        "implementation_sha256":
            sha256(
                Path(__file__)
            ),

        "events_verified":
            len(
                sample
            ),

        "candidate_set_ready_events":
            census[
                "CANDIDATE_SET_READY"
            ],

        "preoutcome_terminal_events":
            (
                census[
                    "PROPAGATION_FAILURE"
                ]
                + census[
                    "UNRECOVERED_REFINEMENT_FAILURE"
                ]
            ),

        "candidate_rows_frozen":
            EXPECTED_CANDIDATE_ROWS,

        "candidate_csv_byte_count":
            CANDIDATES.stat().st_size,

        "frozen_hashes":
            hashes,

        "candidate_manifest_status":
            manifest[
                "status"
            ],

        "association_signature":
            signature,

        "synthetic_association_tests":
            synthetic,

        "reference_TCA_allowed_metadata_verified":
            True,

        "reference_distance_km_used":
            False,

        "real_G6_association_executed":
            False,

        "orbital_propagation_performed":
            False,

        "candidate_generation_performed":
            False,

        "TraCSS_physical_outcome_rows_opened":
            0,

        "selected_candidate_distance_exported":
            False,

        "selected_candidate_time_exported":
            False,

        "signed_TCA_residual_exported":
            False,

        "absolute_TCA_error_E_t_computed":
            False,

        "miss_distance_error_E_d_computed":
            False,

        "coverage_computed":
            False,

        "covariance_access":
            False,

        "Pc_access":
            False,

        "HBR_access":
            False,

        "method_retuning_performed":
            False,

        "network_requests":
            0,

        "association_authorized":
            False,

        "next_safe_action":
            "INDEPENDENT_REVIEW_AND_FREEZE_INACTIVE_REFERENCE_TCA_ASSOCIATION_IMPLEMENTATION",
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


def associate():
    raise AssociationImplementationError(
        "G6_REFERENCE_TCA_ASSOCIATION_INACTIVE_UNTIL_INDEPENDENT_REVIEW_AND_AUTHORIZATION"
    )


def main() -> int:

    parser = argparse.ArgumentParser()

    group = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )

    group.add_argument(
        "--offline-validate",
        action="store_true",
    )

    group.add_argument(
        "--associate",
        action="store_true",
    )

    args = parser.parse_args()

    if args.offline_validate:
        offline_validate()
        return 0

    if args.associate:
        associate()
        return 0

    raise AssociationImplementationError(
        "unreachable command state"
    )


if __name__ == "__main__":

    raise SystemExit(
        main()
    )
