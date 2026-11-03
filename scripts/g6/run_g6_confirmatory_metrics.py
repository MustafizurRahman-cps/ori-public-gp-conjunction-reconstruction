#!/usr/bin/env python3
"""Authorization wrapper for frozen G6 confirmatory metrics.

Offline validation:
- requires DR-0033 frozen boundary;
- verifies frozen implementation and already-recorded reference provenance;
- performs no TraCSS raw hashing/opening/parsing;
- performs no real metric execution.

Authorized execution:
- is permitted only after a separate authorization commit/tag;
- invokes only the frozen target.run_metrics() scientific implementation;
- does not reimplement any scientific metric.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

TARGET = (
    ROOT
    / "scripts/g6/compute_g6_confirmatory_metrics.py"
)

DR0033 = (
    ROOT
    / "docs/research/DR-0033_G6_Confirmatory_Metric_Implementation_Freeze.md"
)

G5_DECISION = (
    ROOT
    / "results/g5/G5_Calibration_Decision_v2.json"
)

TRACSS = (
    ROOT
    / "data/raw/g2/IVV_Releasable_Dataset_SFSH_DiscreteHBR.csv.gz"
)

OFFLINE_VALIDATION = (
    ROOT
    / "results/g6/G6_Confirmatory_Metric_Execution_Authorization_Launcher_Offline_Validation.json"
)

AUTH_REVIEW = (
    ROOT
    / "results/g6/G6_Confirmatory_Metric_Execution_Authorization_Review.json"
)

EVENT_OUT = (
    ROOT
    / "results/g6/G6_Confirmatory_Event_Metrics.csv"
)

TEMPORAL_OUT = (
    ROOT
    / "results/g6/G6_Confirmatory_Temporal_Summary.csv"
)

SUMMARY_OUT = (
    ROOT
    / "results/g6/G6_Confirmatory_Metric_Summary.json"
)

EXECUTION_AUDIT = (
    ROOT
    / "results/g6/G6_Confirmatory_Metric_Execution_Audit.json"
)


PARENT_COMMIT = (
    "9f9819ed0769e65a5e076069474dfd88090eda87"
)

PARENT_TAG = (
    "g6-confirmatory-metric-implementation-frozen"
)

AUTHORIZATION_TAG = (
    "g6-confirmatory-metric-execution-authorized"
)

AUTHORIZATION_TOKEN = (
    "DR-0034_G6_CONFIRMATORY_METRICS"
)


EXPECTED_TARGET_SHA = (
    "bf5cd3758a2791e1cd6e9506053b1ab288541fc9d906b933907df8b49b7c9643"
)

EXPECTED_DR0033_SHA = (
    "2a270fe9bcbb64e45b220f3736fd4ae19f31fde6f11c184d837379b3816f54ed"
)

EXPECTED_G5_DECISION_SHA = (
    "336ab27d9949b59b82dfebdbe6573f27bd6a59f1efb3dc4244fc6bc00725f1c9"
)

EXPECTED_TRACSS_SHA = (
    "b8e49b9c40f974e5ff37e26ade3a855813436bb4c6dab4d7ddeccc8ff5752d39"
)

EXPECTED_TRACSS_ROWS = 283595
EXPECTED_TRACSS_BYTES = 65406532

EXPECTED_EVENTS = 6927
EXPECTED_EVALUABLE = 6906
EXPECTED_VALID = 6839

MISS_RADIUS_KM = 78.75554652312711
TCA_RADIUS_S = 7.105892
NOMINAL_COVERAGE = 0.90
MIN_SUPPORT = 141


class AuthorizationError(RuntimeError):
    pass


def sha256(path: Path) -> str:

    h = hashlib.sha256()

    with path.open("rb") as stream:

        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def git_value(*args: str) -> str:

    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def atomic_json(
    path: Path,
    payload: dict,
):

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )

    tmp = Path(tmp_name)

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
            os.fsync(stream.fileno())

        os.replace(
            tmp,
            path,
        )

    finally:

        tmp.unlink(
            missing_ok=True
        )


def verify_parent_boundary():

    if (
        git_value(
            "rev-parse",
            "HEAD",
        )
        != PARENT_COMMIT
    ):
        raise AuthorizationError(
            "offline authorization validation requires DR-0033 HEAD"
        )

    if (
        git_value(
            "rev-parse",
            f"refs/tags/{PARENT_TAG}^{{commit}}",
        )
        != PARENT_COMMIT
    ):
        raise AuthorizationError(
            "DR-0033 parent tag mismatch"
        )

    if git_value(
        "status",
        "--porcelain",
        "--untracked-files=no",
    ):
        raise AuthorizationError(
            "tracked working tree is not clean"
        )


def verify_frozen_implementation():

    checks = {
        TARGET:
            EXPECTED_TARGET_SHA,

        DR0033:
            EXPECTED_DR0033_SHA,

        G5_DECISION:
            EXPECTED_G5_DECISION_SHA,
    }

    for path, expected in checks.items():

        if not path.is_file():
            raise AuthorizationError(
                f"missing frozen dependency: {path}"
            )

        actual = sha256(path)

        if actual != expected:
            raise AuthorizationError(
                f"frozen dependency SHA mismatch: {path}"
            )


def verify_source_metadata_only():

    decision = json.loads(
        G5_DECISION.read_text(
            encoding="utf-8"
        )
    )

    if (
        int(
            decision.get(
                "TraCSS_raw_row_count",
                -1,
            )
        )
        != EXPECTED_TRACSS_ROWS
    ):
        raise AuthorizationError(
            "recorded TraCSS row count changed"
        )

    if (
        str(
            decision.get(
                "TraCSS_raw_sha256",
                "",
            )
        ).lower()
        != EXPECTED_TRACSS_SHA
    ):
        raise AuthorizationError(
            "recorded TraCSS SHA changed"
        )

    if not TRACSS.is_file():
        raise AuthorizationError(
            "protected TraCSS source missing"
        )

    if (
        TRACSS.stat().st_size
        != EXPECTED_TRACSS_BYTES
    ):
        raise AuthorizationError(
            "protected TraCSS byte-count metadata changed"
        )

    # Deliberately no hash/open/decompression/parsing here.

    return {
        "path":
            TRACSS.relative_to(
                ROOT
            ).as_posix(),

        "exists":
            True,

        "byte_count":
            EXPECTED_TRACSS_BYTES,

        "recorded_row_count":
            EXPECTED_TRACSS_ROWS,

        "recorded_sha256":
            EXPECTED_TRACSS_SHA,

        "raw_source_hashed":
            False,

        "raw_source_opened":
            False,

        "raw_rows_parsed":
            0,

        "min_range_accessed":
            False,

        "mdistance_accessed":
            False,
    }


def verify_outputs_absent():

    for path in (
        EVENT_OUT,
        TEMPORAL_OUT,
        SUMMARY_OUT,
        EXECUTION_AUDIT,
    ):

        if path.exists():
            raise AuthorizationError(
                f"refusing to overwrite existing confirmatory metric output: {path}"
            )


def load_target():

    spec = (
        importlib.util.spec_from_file_location(
            "g6_frozen_confirmatory_metrics",
            TARGET,
        )
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise AuthorizationError(
            "could not load frozen metric implementation"
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


def offline_validate():

    if OFFLINE_VALIDATION.exists():
        raise AuthorizationError(
            "authorization offline-validation artifact already exists"
        )

    verify_parent_boundary()
    verify_frozen_implementation()
    verify_outputs_absent()

    source = (
        verify_source_metadata_only()
    )

    launcher_sha = sha256(
        Path(__file__)
    )

    payload = {
        "status":
            "G6_CONFIRMATORY_METRIC_EXECUTION_AUTHORIZATION_LAUNCHER_OFFLINE_VALIDATION_PASS",

        "launcher_sha256":
            launcher_sha,

        "parent_commit":
            PARENT_COMMIT,

        "parent_tag":
            PARENT_TAG,

        "future_authorization_tag":
            AUTHORIZATION_TAG,

        "authorization_token":
            AUTHORIZATION_TOKEN,

        "frozen_metric_implementation_sha256":
            EXPECTED_TARGET_SHA,

        "DR0033_decision_sha256":
            EXPECTED_DR0033_SHA,

        "events":
            EXPECTED_EVENTS,

        "evaluable_events":
            EXPECTED_EVALUABLE,

        "VALID_ASSOCIATED_events":
            EXPECTED_VALID,

        "miss_distance_radius_km":
            MISS_RADIUS_KM,

        "TCA_radius_s":
            TCA_RADIUS_S,

        "nominal_coverage":
            NOMINAL_COVERAGE,

        "minimum_confirmatory_support":
            MIN_SUPPORT,

        "reference_source_metadata":
            source,

        "real_metric_execution_performed":
            False,

        "protected_source_hashed":
            False,

        "protected_source_opened":
            False,

        "TraCSS_rows_parsed":
            0,

        "min_range_accessed":
            False,

        "mdistance_accessed":
            False,

        "E_d_computed":
            False,

        "E_t_computed":
            False,

        "coverage_computed":
            False,

        "CAL0_refit_performed":
            False,

        "method_retuning_performed":
            False,

        "network_requests":
            0,

        "next_safe_action":
            "INDEPENDENT_REVIEW_AND_FREEZE_EXECUTION_AUTHORIZATION_BEFORE_PROTECTED_OUTCOME_ACCESS",
    }

    atomic_json(
        OFFLINE_VALIDATION,
        payload,
    )

    print(
        json.dumps(
            {
                **payload,

                "offline_validation_sha256":
                    sha256(
                        OFFLINE_VALIDATION
                    ),
            },
            indent=2,
            sort_keys=True,
        )
    )


def verify_authorized_execution_boundary(
    authorization: str,
):

    if authorization != AUTHORIZATION_TOKEN:
        raise AuthorizationError(
            "invalid G6 confirmatory metric authorization token"
        )

    head = git_value(
        "rev-parse",
        "HEAD",
    )

    tag_target = git_value(
        "rev-parse",
        f"refs/tags/{AUTHORIZATION_TAG}^{{commit}}",
    )

    if tag_target != head:
        raise AuthorizationError(
            "current HEAD is not the frozen metric-execution authorization commit"
        )

    if (
        git_value(
            "rev-parse",
            "HEAD^",
        )
        != PARENT_COMMIT
    ):
        raise AuthorizationError(
            "authorization commit is not direct child of DR-0033"
        )

    if git_value(
        "status",
        "--porcelain",
        "--untracked-files=no",
    ):
        raise AuthorizationError(
            "tracked working tree is not clean"
        )

    verify_frozen_implementation()

    validation = json.loads(
        OFFLINE_VALIDATION.read_text(
            encoding="utf-8"
        )
    )

    if (
        validation.get("status")
        != "G6_CONFIRMATORY_METRIC_EXECUTION_AUTHORIZATION_LAUNCHER_OFFLINE_VALIDATION_PASS"
    ):
        raise AuthorizationError(
            "authorization launcher validation is not PASS"
        )

    if (
        validation.get(
            "launcher_sha256"
        )
        != sha256(
            Path(__file__)
        )
    ):
        raise AuthorizationError(
            "authorization launcher changed after validation"
        )

    review = json.loads(
        AUTH_REVIEW.read_text(
            encoding="utf-8"
        )
    )

    if (
        review.get("status")
        != "G6_CONFIRMATORY_METRIC_EXECUTION_AUTHORIZATION_REVIEW_PASS"
    ):
        raise AuthorizationError(
            "authorization independent review is not PASS"
        )

    if (
        review.get(
            "launcher_sha256"
        )
        != sha256(
            Path(__file__)
        )
    ):
        raise AuthorizationError(
            "authorization review references wrong launcher"
        )

    verify_source_metadata_only()
    verify_outputs_absent()


def validate_protected_outputs(
    summary: dict,
):

    if (
        summary.get("status")
        != "G6_CONFIRMATORY_METRICS_PASS"
    ):
        raise AuthorizationError(
            "frozen metric implementation did not report PASS"
        )

    if (
        int(
            summary.get(
                "events",
                -1,
            )
        )
        != EXPECTED_EVENTS
    ):
        raise AuthorizationError(
            "metric summary event count mismatch"
        )

    process = summary.get(
        "process_estimands",
        {}
    )

    evaluable = process.get(
        "evaluable",
        {}
    )

    association = process.get(
        "valid_association",
        {}
    )

    if (
        int(
            evaluable.get(
                "successes",
                -1,
            )
        )
        != EXPECTED_EVALUABLE
        or int(
            evaluable.get(
                "denominator",
                -1,
            )
        )
        != EXPECTED_EVENTS
    ):
        raise AuthorizationError(
            "evaluable process-estimand accounting mismatch"
        )

    if (
        int(
            association.get(
                "successes",
                -1,
            )
        )
        != EXPECTED_VALID
        or int(
            association.get(
                "denominator",
                -1,
            )
        )
        != EXPECTED_EVENTS
    ):
        raise AuthorizationError(
            "valid-association process-estimand accounting mismatch"
        )

    primary = summary.get(
        "primary_Ed",
        {}
    )

    secondary = summary.get(
        "secondary_Et",
        {}
    )

    if (
        int(
            primary.get(
                "n",
                -1,
            )
        )
        != EXPECTED_VALID
        or int(
            primary.get(
                "coverage_denominator",
                -1,
            )
        )
        != EXPECTED_VALID
    ):
        raise AuthorizationError(
            "primary E_d denominator mismatch"
        )

    if (
        int(
            secondary.get(
                "n",
                -1,
            )
        )
        != EXPECTED_VALID
        or int(
            secondary.get(
                "coverage_denominator",
                -1,
            )
        )
        != EXPECTED_VALID
    ):
        raise AuthorizationError(
            "secondary E_t denominator mismatch"
        )

    if not math.isclose(
        float(
            primary.get(
                "fixed_radius",
                float("nan"),
            )
        ),
        MISS_RADIUS_KM,
        rel_tol=0.0,
        abs_tol=1.0e-12,
    ):
        raise AuthorizationError(
            "primary fixed radius changed"
        )

    if not math.isclose(
        float(
            secondary.get(
                "fixed_radius",
                float("nan"),
            )
        ),
        TCA_RADIUS_S,
        rel_tol=0.0,
        abs_tol=1.0e-12,
    ):
        raise AuthorizationError(
            "secondary fixed radius changed"
        )

    if (
        primary.get(
            "reference_field"
        )
        != "min_range"
        or primary.get(
            "mdistance_used"
        )
        is not False
    ):
        raise AuthorizationError(
            "physical reference-field boundary changed"
        )

    reference = summary.get(
        "reference_source",
        {}
    )

    if (
        reference.get(
            "sha256"
        )
        != EXPECTED_TRACSS_SHA
    ):
        raise AuthorizationError(
            "protected source SHA mismatch in metric summary"
        )

    if (
        int(
            reference.get(
                "raw_rows_scanned",
                -1,
            )
        )
        != EXPECTED_TRACSS_ROWS
    ):
        raise AuthorizationError(
            "protected raw-row count mismatch"
        )

    if (
        int(
            reference.get(
                "valid_associated_reference_rows_used",
                -1,
            )
        )
        != EXPECTED_VALID
    ):
        raise AuthorizationError(
            "protected reference-row usage mismatch"
        )

    if (
        reference.get(
            "physical_reference_miss_distance_field"
        )
        != "min_range"
        or reference.get(
            "mdistance_used"
        )
        is not False
    ):
        raise AuthorizationError(
            "protected physical-reference semantics changed"
        )

    for key in (
        "orbital_propagation_performed",
        "candidate_generation_performed",
        "association_recomputed",
        "CAL0_refit_performed",
        "method_retuning_performed",
        "bootstrap_performed",
        "covariance_access",
        "Pc_access",
        "HBR_access",
    ):

        if summary.get(key) is not False:
            raise AuthorizationError(
                f"metric execution safety invariant failed: {key}"
            )

    if (
        int(
            summary.get(
                "network_requests",
                -1,
            )
        )
        != 0
    ):
        raise AuthorizationError(
            "network access reported during metric execution"
        )

    for path in (
        EVENT_OUT,
        TEMPORAL_OUT,
        SUMMARY_OUT,
    ):

        if not path.is_file():
            raise AuthorizationError(
                f"required protected output missing: {path}"
            )

    with EVENT_OUT.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        event_rows = sum(
            1
            for _ in csv.DictReader(
                stream
            )
        )

    if event_rows != EXPECTED_EVENTS:
        raise AuthorizationError(
            "event-level metric output row count mismatch"
        )

    with TEMPORAL_OUT.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        temporal_rows = list(
            csv.DictReader(
                stream
            )
        )

    if len(
        temporal_rows
    ) != 5:
        raise AuthorizationError(
            "temporal summary does not contain five frozen strata"
        )


def execute(
    authorization: str,
):

    verify_authorized_execution_boundary(
        authorization
    )

    target = load_target()

    # This is the only scientific execution call made by the launcher.
    summary = target.run_metrics()

    validate_protected_outputs(
        summary
    )

    if EXECUTION_AUDIT.exists():
        raise AuthorizationError(
            "execution audit unexpectedly exists"
        )

    audit = {
        "status":
            "G6_CONFIRMATORY_METRIC_EXECUTION_PASS",

        "authorization_token":
            AUTHORIZATION_TOKEN,

        "authorization_tag":
            AUTHORIZATION_TAG,

        "authorization_commit":
            git_value(
                "rev-parse",
                "HEAD",
            ),

        "launcher_sha256":
            sha256(
                Path(__file__)
            ),

        "frozen_metric_implementation_sha256":
            EXPECTED_TARGET_SHA,

        "protected_source_sha256":
            EXPECTED_TRACSS_SHA,

        "protected_source_rows_scanned":
            EXPECTED_TRACSS_ROWS,

        "events":
            EXPECTED_EVENTS,

        "evaluable_events":
            EXPECTED_EVALUABLE,

        "VALID_ASSOCIATED_events":
            EXPECTED_VALID,

        "event_metrics_sha256":
            sha256(
                EVENT_OUT
            ),

        "temporal_summary_sha256":
            sha256(
                TEMPORAL_OUT
            ),

        "metric_summary_sha256":
            sha256(
                SUMMARY_OUT
            ),

        "primary_Ed_coverage_successes":
            summary[
                "primary_Ed"
            ][
                "coverage_successes"
            ],

        "primary_Ed_coverage_denominator":
            summary[
                "primary_Ed"
            ][
                "coverage_denominator"
            ],

        "primary_Ed_observed_coverage":
            summary[
                "primary_Ed"
            ][
                "observed_coverage"
            ],

        "primary_Ed_wilson_95_lower":
            summary[
                "primary_Ed"
            ][
                "wilson_95_lower"
            ],

        "primary_Ed_wilson_95_upper":
            summary[
                "primary_Ed"
            ][
                "wilson_95_upper"
            ],

        "primary_interpretation":
            summary[
                "primary_Ed"
            ][
                "interpretation"
            ],

        "secondary_Et_coverage_successes":
            summary[
                "secondary_Et"
            ][
                "coverage_successes"
            ],

        "secondary_Et_coverage_denominator":
            summary[
                "secondary_Et"
            ][
                "coverage_denominator"
            ],

        "temporal_instability_flag":
            summary[
                "temporal_reporting"
            ][
                "TEMPORAL_INSTABILITY_FLAG"
            ],

        "orbital_propagation_performed":
            False,

        "candidate_generation_performed":
            False,

        "association_recomputed":
            False,

        "CAL0_refit_performed":
            False,

        "method_retuning_performed":
            False,

        "network_requests":
            0,

        "next_safe_action":
            "INDEPENDENT_AUDIT_AND_FREEZE_CONFIRMATORY_METRIC_OUTPUTS",
    }

    atomic_json(
        EXECUTION_AUDIT,
        audit,
    )

    print(
        json.dumps(
            audit,
            indent=2,
            sort_keys=True,
        )
    )


def main():

    parser = argparse.ArgumentParser()

    group = parser.add_mutually_exclusive_group(
        required=True
    )

    group.add_argument(
        "--offline-validate",
        action="store_true",
    )

    group.add_argument(
        "--execute",
        action="store_true",
    )

    parser.add_argument(
        "--authorization",
    )

    args = parser.parse_args()

    if args.offline_validate:

        if args.authorization is not None:
            raise AuthorizationError(
                "authorization token not accepted during offline validation"
            )

        offline_validate()
        return

    if args.execute:

        if args.authorization is None:
            raise AuthorizationError(
                "explicit authorization token required"
            )

        execute(
            args.authorization
        )

        return

    raise AuthorizationError(
        "unreachable command state"
    )


if __name__ == "__main__":
    main()
