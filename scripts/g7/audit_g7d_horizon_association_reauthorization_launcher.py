from __future__ import annotations

import ast
import csv
import gzip
import hashlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]

LAUNCHER = (
    ROOT
    / "scripts/g7/run_g7d_horizon_association_execution_v2.py"
)

VALIDATION = (
    ROOT
    / "results/g7/"
      "G7D_Horizon_Association_Reauthorization_Launcher_Offline_Validation.json"
)

EVALUATOR = (
    ROOT
    / "scripts/g7/run_g7d_horizon_association.py"
)

EVALUATOR_VALIDATION = (
    ROOT
    / "results/g7/G7D_Horizon_Association_Offline_Validation.json"
)

HISTORICAL_LAUNCHER = (
    ROOT
    / "scripts/g7/run_g7d_horizon_association_execution.py"
)

AUDIT_OUT = (
    ROOT
    / "results/g7/"
      "G7D_Horizon_Association_Reauthorization_Launcher_Independent_Audit.json"
)

EXPECTED_LAUNCHER_SHA = (
    "f34915e30eb3f2041592c56470d612190db229cd6af3192f8c695bc4f35a3fda"
)

EXPECTED_VALIDATION_SHA = (
    "936f736606a8be97a9f403a805b931cc3671d62181df88f655ea6a51d2320513"
)

EXPECTED_EVALUATOR_SHA = (
    "4ebc7f1d887b9d56cf43d41f9822bd32cb189b1fd4ffe543bdcc67ad81ecd769"
)

EXPECTED_EVALUATOR_VALIDATION_SHA = (
    "2de8432b8e832cdad14aaf3fcbb7d7e38ca8ee25bdbf760ebd0127751b979e94"
)

EXPECTED_HISTORICAL_LAUNCHER_SHA = (
    "618cc2d0e91d04775386631bdf43f165fdf9b7af9187aae6e4fe1506857faf7b"
)

CORRECTION_COMMIT = (
    "044b8b5d235c426ba035c361537e86068d33c100"
)

CORRECTION_TAG = (
    "g7d-protected-reference-time-parser-correction-frozen"
)

HISTORICAL_AUTHORIZATION_COMMIT = (
    "c14b7ed33e3448ed7bf7a617b18d11033b7c1350"
)

HISTORICAL_AUTHORIZATION_TAG = (
    "g7d-horizon-association-execution-authorized"
)

FUTURE_AUTHORIZATION_TAG = (
    "g7d-horizon-association-execution-reauthorized"
)

EXPECTED_TOKEN = (
    "DR0050_G7D_HORIZON_ASSOCIATION_EXECUTION_REAUTHORIZED"
)

EXPECTED_SAMPLE_SHA = (
    "f8a103cbd268e7b6d3e95515e8a806da403511196145897997866adc1fab10ef"
)

EXPECTED_TRACSS_SHA = (
    "b8e49b9c40f974e5ff37e26ade3a855813436bb4c6dab4d7ddeccc8ff5752d39"
)

EXPECTED_H3_EVENT_INPUT_SHA = (
    "5689d7a8b279a6d6a7f7ccd7941d977f2ce9ff04928a29201b82c385c3e94b43"
)

EXPECTED_H3_CANDIDATE_INPUT_SHA = (
    "a64c500a73a2b66df44642066337d1cdd039404cc72147ada65f0862dad77aae"
)

EXPECTED_H7_EVENT_INPUT_SHA = (
    "418348e3389b47462d196ccbc53d0a4a0e90b0c7a4a9f17e93de51a3a75c2595"
)

EXPECTED_H7_CANDIDATE_INPUT_SHA = (
    "1f32718dc5607ed3d5b8d6a75c6bdd728fe6518b39e49bba607339cf71793791"
)

REAL_OUTPUTS = (
    ROOT / "results/g7/G7D_H3_Horizon_Association_Event.csv",
    ROOT / "results/g7/G7D_H3_Horizon_Association_Summary.json",
    ROOT / "results/g7/G7D_H3_Horizon_Association_Execution_Audit.json",
    ROOT / "results/g7/G7D_H7_Horizon_Association_Event.csv",
    ROOT / "results/g7/G7D_H7_Horizon_Association_Summary.json",
    ROOT / "results/g7/G7D_H7_Horizon_Association_Execution_Audit.json",
)


class AuditError(RuntimeError):
    pass


def require(condition, message):

    if not condition:
        raise AuditError(message)


def sha256(path: Path) -> str:

    digest = hashlib.sha256()

    with path.open("rb") as stream:

        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def git(*args: str, check: bool = True):

    process = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if check and process.returncode != 0:
        raise AuditError(
            process.stderr.strip()
            or f"git command failed: {args!r}"
        )

    return process


def run_cli(arguments):

    environment = dict(os.environ)

    environment[
        "PYTHONDONTWRITEBYTECODE"
    ] = "1"

    return subprocess.run(
        [
            sys.executable,
            str(LAUNCHER),
            *arguments,
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


# ================================================================
# 1. Exact artifact identities
# ================================================================

expected_hashes = {
    LAUNCHER:
        EXPECTED_LAUNCHER_SHA,

    VALIDATION:
        EXPECTED_VALIDATION_SHA,

    EVALUATOR:
        EXPECTED_EVALUATOR_SHA,

    EVALUATOR_VALIDATION:
        EXPECTED_EVALUATOR_VALIDATION_SHA,

    HISTORICAL_LAUNCHER:
        EXPECTED_HISTORICAL_LAUNCHER_SHA,
}

for path, expected in expected_hashes.items():

    require(
        path.is_file(),
        f"missing artifact: {path}",
    )

    require(
        sha256(path) == expected,
        f"SHA mismatch: {path}",
    )


# ================================================================
# 2. Repository/tag boundary
# ================================================================

require(
    git(
        "rev-parse",
        "HEAD",
    ).stdout.strip()
    == CORRECTION_COMMIT,
    "HEAD changed",
)

require(
    git(
        "rev-parse",
        f"refs/tags/{CORRECTION_TAG}^{{commit}}",
    ).stdout.strip()
    == CORRECTION_COMMIT,
    "DR-0049 correction tag changed",
)

require(
    git(
        "rev-parse",
        f"refs/tags/{HISTORICAL_AUTHORIZATION_TAG}^{{commit}}",
    ).stdout.strip()
    == HISTORICAL_AUTHORIZATION_COMMIT,
    "historical DR-0048 authorization tag changed",
)

future = git(
    "rev-parse",
    "-q",
    "--verify",
    f"refs/tags/{FUTURE_AUTHORIZATION_TAG}^{{commit}}",
    check=False,
)

require(
    future.returncode != 0,
    "future DR-0050 authorization tag already exists",
)


for path in REAL_OUTPUTS:

    require(
        not path.exists(),
        f"real H3/H7 result already exists: {path}",
    )


# ================================================================
# 3. Import V2 launcher and corrected evaluator
# ================================================================

launcher_spec = importlib.util.spec_from_file_location(
    "g7d_reauthorization_launcher_audit",
    LAUNCHER,
)

require(
    launcher_spec is not None
    and launcher_spec.loader is not None,
    "cannot import V2 launcher",
)

launcher = importlib.util.module_from_spec(
    launcher_spec
)

launcher_spec.loader.exec_module(
    launcher
)


evaluator_spec = importlib.util.spec_from_file_location(
    "g7d_corrected_evaluator_audit",
    EVALUATOR,
)

require(
    evaluator_spec is not None
    and evaluator_spec.loader is not None,
    "cannot import corrected evaluator",
)

evaluator = importlib.util.module_from_spec(
    evaluator_spec
)

evaluator_spec.loader.exec_module(
    evaluator
)


# ================================================================
# 4. Exact V2 authorization constants
# ================================================================

require(
    launcher.CORRECTION_COMMIT
    == CORRECTION_COMMIT,
    "correction commit constant mismatch",
)

require(
    launcher.CORRECTION_TAG
    == CORRECTION_TAG,
    "correction tag constant mismatch",
)

require(
    launcher.HISTORICAL_AUTHORIZATION_COMMIT
    == HISTORICAL_AUTHORIZATION_COMMIT,
    "historical authorization commit mismatch",
)

require(
    launcher.HISTORICAL_AUTHORIZATION_TAG
    == HISTORICAL_AUTHORIZATION_TAG,
    "historical authorization tag mismatch",
)

require(
    launcher.AUTHORIZATION_TAG
    == FUTURE_AUTHORIZATION_TAG,
    "future authorization tag mismatch",
)

require(
    launcher.AUTHORIZATION_TOKEN
    == EXPECTED_TOKEN,
    "reauthorization token mismatch",
)

require(
    launcher.EVALUATOR_SHA256
    == EXPECTED_EVALUATOR_SHA,
    "corrected evaluator SHA constant mismatch",
)

require(
    launcher.SAMPLE_SHA256
    == EXPECTED_SAMPLE_SHA,
    "sample SHA constant mismatch",
)

require(
    launcher.TRACSS_SHA256
    == EXPECTED_TRACSS_SHA,
    "TraCSS SHA constant mismatch",
)

require(
    launcher.H3_EVENT_INPUT_SHA256
    == EXPECTED_H3_EVENT_INPUT_SHA,
    "H3 event-input SHA changed",
)

require(
    launcher.H3_CANDIDATE_INPUT_SHA256
    == EXPECTED_H3_CANDIDATE_INPUT_SHA,
    "H3 candidate-input SHA changed",
)

require(
    launcher.H7_EVENT_INPUT_SHA256
    == EXPECTED_H7_EVENT_INPUT_SHA,
    "H7 event-input SHA changed",
)

require(
    launcher.H7_CANDIDATE_INPUT_SHA256
    == EXPECTED_H7_CANDIDATE_INPUT_SHA,
    "H7 candidate-input SHA changed",
)

require(
    launcher.N_EVENTS == 6927,
    "event census changed",
)

require(
    launcher.EXPECTED_REFERENCE_ROWS
    == 283595,
    "reference row census changed",
)

require(
    launcher.ASSOCIATION_TOLERANCE_MIN
    == 5,
    "association tolerance changed",
)

require(
    set(
        launcher.OUTPUTS.keys()
    )
    == {3, 7},
    "authorized horizon set changed",
)

require(
    launcher.FAILED_H3_ATTEMPT_RESULT_PRODUCED
    is False,
    "prior failed attempt incorrectly classified as result",
)


# ================================================================
# 5. Static execution-boundary audit
# ================================================================

verify_source = inspect.getsource(
    launcher.verify_execution_boundary
)

execute_source = inspect.getsource(
    launcher.execute_horizon
)

offline_source = inspect.getsource(
    launcher.offline_validate
)

main_source = inspect.getsource(
    launcher.main
)


required_verify_fragments = (
    "token != AUTHORIZATION_TOKEN",
    "horizon_days not in (3, 7)",
    "AUTHORIZATION_TAG",
    "CORRECTION_COMMIT",
    "CORRECTION_TAG",
    "HISTORICAL_AUTHORIZATION_COMMIT",
    "HISTORICAL_AUTHORIZATION_TAG",
    '"merge-base"',
    '"--is-ancestor"',
    "expected_untracked_for_h7",
    "untracked != required",
    "event_output_sha256",
    "summary_output_sha256",
    "H3_EVENT_INPUT_SHA256",
    "H3_CANDIDATE_INPUT_SHA256",
    "candidate_rows_scanned",
    "protected_reference_rows_scanned",
    "association_tolerance_min",
    "association_threshold_inclusive",
    "automatic_retry_performed",
)

for fragment in required_verify_fragments:

    require(
        fragment in verify_source,
        f"missing V2 execution guard: {fragment}",
    )


boundary_position = execute_source.find(
    "verify_execution_boundary"
)

load_position = execute_source.find(
    "load_evaluator"
)

evaluation_position = execute_source.find(
    "evaluate_horizon"
)

require(
    (
        boundary_position >= 0
        and load_position >= 0
        and evaluation_position >= 0
        and boundary_position < load_position < evaluation_position
    ),
    "authorization boundary does not precede evaluation",
)


require(
    main_source.count(
        "execute_horizon("
    )
    == 1,
    "one-horizon-per-invocation property changed",
)


for forbidden in (
    "evaluate_horizon(",
    "load_protected_reference(",
):

    require(
        forbidden not in offline_source,
        (
            "offline validation can enter protected "
            f"execution path: {forbidden}"
        ),
    )


launcher_text = LAUNCHER.read_text(
    encoding="utf-8"
)

require(
    "invalid DR-0050 reauthorization token"
    in launcher_text,
    "current token diagnostic is not DR-0050",
)

require(
    "DR-0050 reauthorization tag is absent"
    in launcher_text,
    "current missing-tag diagnostic is not DR-0050",
)

require(
    "DR-0050 reauthorization tag does not equal HEAD"
    in launcher_text,
    "current tag-target diagnostic is not DR-0050",
)

for stale in (
    "invalid DR-0048 authorization token",
    "DR-0048 authorization tag is absent",
    "DR-0048 authorization tag does not equal HEAD",
):

    require(
        stale not in launcher_text,
        f"stale current-authorization diagnostic remains: {stale}",
    )


# Historical DR-0048 provenance must still exist.
for historical in (
    "HISTORICAL_AUTHORIZATION_COMMIT",
    "HISTORICAL_AUTHORIZATION_TAG",
    "historical_DR0048_authorization_preserved",
    "old_DR0048_authorization_reused",
):

    require(
        historical in launcher_text,
        f"historical DR-0048 provenance missing: {historical}",
    )


# ================================================================
# 6. No network implementation in V2 launcher
# ================================================================

tree = ast.parse(
    launcher_text
)

imports = set()

for node in ast.walk(tree):

    if isinstance(
        node,
        ast.Import,
    ):

        for alias in node.names:
            imports.add(
                alias.name
            )

    elif isinstance(
        node,
        ast.ImportFrom,
    ):

        if node.module:
            imports.add(
                node.module
            )


for forbidden_prefix in (
    "requests",
    "urllib",
    "http.client",
    "httpx",
    "socket",
):

    require(
        not any(
            name == forbidden_prefix
            or name.startswith(
                forbidden_prefix + "."
            )
            for name in imports
        ),
        (
            "network-capable import detected: "
            f"{forbidden_prefix}"
        ),
    )


# ================================================================
# 7. Offline-validation semantics
# ================================================================

validation = json.loads(
    VALIDATION.read_text(
        encoding="utf-8"
    )
)

expected_validation = {
    "status":
        "G7D_HORIZON_ASSOCIATION_REAUTHORIZATION_LAUNCHER_OFFLINE_VALIDATION_PASS",

    "DR0049_correction_commit":
        CORRECTION_COMMIT,

    "DR0049_correction_tag":
        CORRECTION_TAG,

    "DR0049_correction_ancestry_required":
        True,

    "historical_DR0048_authorization_commit":
        HISTORICAL_AUTHORIZATION_COMMIT,

    "historical_DR0048_authorization_tag":
        HISTORICAL_AUTHORIZATION_TAG,

    "historical_DR0048_authorization_preserved":
        True,

    "prior_H3_attempt_result_produced":
        False,

    "prior_H3_attempt_classified_as_scientific_result":
        False,

    "prior_H3_attempt_failure_class":
        "PRE_RESULT_SOURCE_SPECIFIC_TIMESTAMP_PARSER_WIRING_DEFECT",

    "corrected_execution_requires_new_authorization":
        True,

    "old_DR0048_authorization_reused":
        False,

    "future_authorization_tag":
        FUTURE_AUTHORIZATION_TAG,

    "authorization_token":
        EXPECTED_TOKEN,

    "authorized_horizons_days":
        [3, 7],

    "H5_execution_permitted":
        False,

    "H3_before_H7_required":
        True,

    "H3_execution_audit_integrity_required_before_H7":
        True,

    "H3_event_hash_reverified_before_H7":
        True,

    "H3_summary_hash_reverified_before_H7":
        True,

    "association_tolerance_min":
        5,

    "association_threshold_inclusive":
        True,

    "direct_execution_currently_authorized":
        False,

    "real_H3_execution_performed":
        False,

    "real_H7_execution_performed":
        False,

    "protected_reference_accessed":
        False,

    "orbital_propagation_performed":
        False,

    "candidate_generation_performed":
        False,

    "CAL0_refit_performed":
        False,

    "method_retuning_performed":
        False,

    "network_requests":
        0,

    "automatic_retry":
        False,
}

for key, expected in expected_validation.items():

    require(
        validation.get(key)
        == expected,
        (
            "reauthorization-validation mismatch: "
            f"{key}"
        ),
    )


require(
    "DR0047_ancestry_required"
    not in validation,
    "stale DR0047 validation label remains",
)


# ================================================================
# 8. Corrected-evaluator H5 regression / no-candidate evidence
# ================================================================

evaluator_validation = json.loads(
    EVALUATOR_VALIDATION.read_text(
        encoding="utf-8"
    )
)

require(
    evaluator_validation.get(
        "status"
    )
    == "G7D_HORIZON_ASSOCIATION_OFFLINE_VALIDATION_PASS",
    "corrected evaluator offline validation is not PASS",
)

for key in (
    "H5_exact_G6_metric_reproduction",
    "H5_exact_G6_selected_candidate_reproduction",
    "H5_exact_G6_status_reproduction",
    "H5_exact_G7C_5min_reproduction",
):

    require(
        evaluator_validation.get(
            key
        )
        is True,
        f"H5 regression failure: {key}",
    )


h5_total = int(
    evaluator_validation[
        "H5_control_events"
    ]
)

h5_ready = int(
    evaluator_validation[
        "H5_control_candidate_ready_events"
    ]
)

h5_no_candidate = (
    h5_total
    - h5_ready
)

require(
    h5_total == 6927,
    "H5 control event census changed",
)

require(
    h5_ready == 6906,
    "H5 candidate-ready census changed",
)

require(
    h5_no_candidate == 21,
    "H5 no-candidate event census changed",
)


# ================================================================
# 9. Supplemental synthetic missing-reference fail-closed test
#
# Uses a temporary synthetic gzip file OUTSIDE the repository.
# Does NOT read real TraCSS and does NOT touch H3/H7 candidates.
# ================================================================

original_TRACSS = evaluator.TRACSS
original_TRACSS_SHA256 = evaluator.TRACSS_SHA256
original_EXPECTED_REFERENCE_ROWS = evaluator.EXPECTED_REFERENCE_ROWS
original_N_EVENTS = evaluator.N_EVENTS

missing_reference_fail_closed = False
missing_reference_error = None

try:

    with tempfile.TemporaryDirectory() as temp_dir:

        synthetic = (
            Path(temp_dir)
            / "synthetic_reference.csv.gz"
        )

        with gzip.open(
            synthetic,
            "wt",
            encoding="utf-8",
            newline="",
        ) as stream:

            writer = csv.DictWriter(
                stream,
                fieldnames=[
                    "conj_id",
                    "epoch",
                    "min_range",
                ],
                lineterminator="\n",
            )

            writer.writeheader()

            writer.writerow(
                {
                    "conj_id":
                        "SYNTH-A",

                    "epoch":
                        "2025-01-01 00:00:00.000000",

                    "min_range":
                        "1.25",
                }
            )

        evaluator.TRACSS = synthetic
        evaluator.TRACSS_SHA256 = sha256(
            synthetic
        )
        evaluator.EXPECTED_REFERENCE_ROWS = 1
        evaluator.N_EVENTS = 2

        synthetic_sample = {
            1: {
                "conj_id":
                    "SYNTH-A",

                "reference_tca_utc":
                    "2025-01-01T00:00:00.000000Z",
            },

            2: {
                "conj_id":
                    "SYNTH-B",

                "reference_tca_utc":
                    "2025-01-02T00:00:00.000000Z",
            },
        }

        try:

            evaluator.load_protected_reference(
                synthetic_sample
            )

        except evaluator.AssociationError as exc:

            missing_reference_error = str(
                exc
            )

            missing_reference_fail_closed = (
                "protected reference sample coverage mismatch"
                in missing_reference_error
            )

finally:

    evaluator.TRACSS = original_TRACSS
    evaluator.TRACSS_SHA256 = original_TRACSS_SHA256
    evaluator.EXPECTED_REFERENCE_ROWS = (
        original_EXPECTED_REFERENCE_ROWS
    )
    evaluator.N_EVENTS = original_N_EVENTS


require(
    missing_reference_fail_closed,
    (
        "synthetic missing-reference condition "
        "did not fail closed"
    ),
)


# ================================================================
# 10. Dynamic launcher rejection-path tests
# ================================================================

invalid_h3 = run_cli(
    [
        "--execute",
        "--horizon-days",
        "3",
        "--authorization-token",
        "INVALID_TOKEN",
    ]
)

invalid_h3_text = (
    invalid_h3.stdout
    + "\n"
    + invalid_h3.stderr
)

require(
    invalid_h3.returncode != 0,
    "invalid H3 token unexpectedly succeeded",
)

require(
    "invalid DR-0050 reauthorization token"
    in invalid_h3_text,
    "invalid H3 token diagnostic mismatch",
)


invalid_h7 = run_cli(
    [
        "--execute",
        "--horizon-days",
        "7",
        "--authorization-token",
        "INVALID_TOKEN",
    ]
)

invalid_h7_text = (
    invalid_h7.stdout
    + "\n"
    + invalid_h7.stderr
)

require(
    invalid_h7.returncode != 0,
    "invalid H7 token unexpectedly succeeded",
)

require(
    "invalid DR-0050 reauthorization token"
    in invalid_h7_text,
    "invalid H7 token diagnostic mismatch",
)


h5 = run_cli(
    [
        "--execute",
        "--horizon-days",
        "5",
        "--authorization-token",
        EXPECTED_TOKEN,
    ]
)

h5_text = (
    h5.stdout
    + "\n"
    + h5.stderr
)

require(
    h5.returncode != 0,
    "H5 execution unexpectedly succeeded",
)

require(
    "invalid choice"
    in h5_text.lower(),
    "H5 CLI rejection missing",
)


offline_mix = run_cli(
    [
        "--offline-validate",
        "--horizon-days",
        "3",
    ]
)

offline_mix_text = (
    offline_mix.stdout
    + "\n"
    + offline_mix.stderr
)

require(
    offline_mix.returncode != 0,
    "offline mode accepted execution horizon",
)

require(
    "offline validation accepts no execution arguments"
    in offline_mix_text,
    "offline/execution separation diagnostic missing",
)


# Correct token must still fail until the NEW tag exists.
no_tag = run_cli(
    [
        "--execute",
        "--horizon-days",
        "3",
        "--authorization-token",
        EXPECTED_TOKEN,
    ]
)

no_tag_text = (
    no_tag.stdout
    + "\n"
    + no_tag.stderr
)

require(
    no_tag.returncode != 0,
    "H3 execution succeeded before reauthorization tag",
)

require(
    "DR-0050 reauthorization tag is absent"
    in no_tag_text,
    "missing-reauthorization-tag diagnostic mismatch",
)


# No rejection test may create a result.
for path in REAL_OUTPUTS:

    require(
        not path.exists(),
        (
            "rejection test created scientific output: "
            f"{path}"
        ),
    )


future_after = git(
    "rev-parse",
    "-q",
    "--verify",
    f"refs/tags/{FUTURE_AUTHORIZATION_TAG}^{{commit}}",
    check=False,
)

require(
    future_after.returncode != 0,
    "audit unexpectedly created reauthorization tag",
)


# ================================================================
# 11. Emit independent audit
# ================================================================

payload = {
    "status":
        "G7D_HORIZON_ASSOCIATION_REAUTHORIZATION_LAUNCHER_INDEPENDENT_AUDIT_PASS",

    "analysis_role":
        "POST_CONFIRMATORY_PROPAGATION_HORIZON_SENSITIVITY",

    "launcher_sha256":
        sha256(
            LAUNCHER
        ),

    "offline_validation_sha256":
        sha256(
            VALIDATION
        ),

    "corrected_evaluator_sha256":
        sha256(
            EVALUATOR
        ),

    "corrected_evaluator_validation_sha256":
        sha256(
            EVALUATOR_VALIDATION
        ),

    "historical_launcher_sha256":
        sha256(
            HISTORICAL_LAUNCHER
        ),

    "DR0049_correction_commit":
        CORRECTION_COMMIT,

    "DR0049_correction_tag":
        CORRECTION_TAG,

    "DR0049_correction_ancestry_guard_verified":
        True,

    "historical_DR0048_authorization_commit":
        HISTORICAL_AUTHORIZATION_COMMIT,

    "historical_DR0048_authorization_tag":
        HISTORICAL_AUTHORIZATION_TAG,

    "historical_DR0048_authorization_preserved":
        True,

    "old_DR0048_authorization_reused":
        False,

    "future_authorization_tag":
        FUTURE_AUTHORIZATION_TAG,

    "future_authorization_tag_absent":
        True,

    "authorization_token_exact":
        True,

    "authorized_horizons_days":
        [3, 7],

    "H5_execution_blocked":
        True,

    "H3_before_H7_required":
        True,

    "H3_execution_audit_integrity_required_before_H7":
        True,

    "H3_event_hash_reverified_before_H7":
        True,

    "H3_summary_hash_reverified_before_H7":
        True,

    "H3_frozen_input_hashes_reverified_before_H7":
        True,

    "execution_boundary_precedes_evaluator_loading":
        True,

    "execution_boundary_precedes_protected_evaluation":
        True,

    "single_requested_horizon_per_invocation":
        True,

    "offline_validation_real_execution_isolated":
        True,

    "current_runtime_diagnostics_reference_DR0050":
        True,

    "historical_DR0048_diagnostics_provenance_preserved":
        True,

    "invalid_H3_token_blocked":
        True,

    "invalid_H7_token_blocked":
        True,

    "correct_token_without_new_authorization_tag_blocked":
        True,

    "offline_execution_argument_mix_blocked":
        True,

    "network_capable_launcher_import_detected":
        False,

    "prior_H3_attempt_result_produced":
        False,

    "prior_H3_attempt_classified_as_scientific_result":
        False,

    "prior_H3_attempt_failure_class":
        "PRE_RESULT_SOURCE_SPECIFIC_TIMESTAMP_PARSER_WIRING_DEFECT",

    "corrected_execution_requires_new_authorization":
        True,

    "supplemental_missing_reference_fail_closed_test":
        True,

    "supplemental_missing_reference_error":
        missing_reference_error,

    "supplemental_missing_reference_used_real_TraCSS":
        False,

    "H5_full_regression_no_candidate_behavior_exercised":
        True,

    "H5_control_events":
        h5_total,

    "H5_control_candidate_ready_events":
        h5_ready,

    "H5_control_no_candidate_events":
        h5_no_candidate,

    "H5_exact_G6_status_reproduction":
        True,

    "real_H3_execution_performed":
        False,

    "real_H7_execution_performed":
        False,

    "H3_candidate_file_accessed_by_audit":
        False,

    "H7_candidate_file_accessed_by_audit":
        False,

    "real_protected_TraCSS_accessed_by_audit":
        False,

    "orbital_propagation_performed":
        False,

    "candidate_generation_performed":
        False,

    "CAL0_refit_performed":
        False,

    "method_retuning_performed":
        False,

    "network_requests":
        0,

    "automatic_retry_performed":
        False,

    "next_safe_action":
        "FREEZE_DR0050_REAUTHORIZATION_BEFORE_CORRECTED_H3_EXECUTION",
}


AUDIT_OUT.parent.mkdir(
    parents=True,
    exist_ok=True,
)

with AUDIT_OUT.open(
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


print(
    json.dumps(
        payload,
        indent=2,
        sort_keys=True,
    )
)