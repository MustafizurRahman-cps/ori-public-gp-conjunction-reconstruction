from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]

LAUNCHER = (
    ROOT
    / "scripts/g7/"
      "run_g7d_horizon_association_execution.py"
)

VALIDATION = (
    ROOT
    / "results/g7/"
      "G7D_Horizon_Association_Authorization_Launcher_Offline_Validation.json"
)

EVALUATOR = (
    ROOT
    / "scripts/g7/"
      "run_g7d_horizon_association.py"
)

AUDIT_OUT = (
    ROOT
    / "results/g7/"
      "G7D_Horizon_Association_Authorization_Launcher_Independent_Audit.json"
)

EXPECTED_LAUNCHER_SHA = (
    "618cc2d0e91d04775386631bdf43f165fdf9b7af9187aae6e4fe1506857faf7b"
)

EXPECTED_VALIDATION_SHA = (
    "7929c6ce526ddb09dc042bebcf2fe729b3bb4de5135d0e9cf4cd82cd5c9a0010"
)

EXPECTED_EVALUATOR_SHA = (
    "ee9c867ed6b70c40fec23de0f8ddbb2c5a6e46885aec2dc99e66a8f8440987c6"
)

EXPECTED_HEAD = (
    "198d002c74d1f9ddeff67a02e0a5e710b74cd7fb"
)

EXPECTED_PARENT_TAG = (
    "g7d-horizon-association-evaluator-frozen"
)

FUTURE_AUTHORIZATION_TAG = (
    "g7d-horizon-association-execution-authorized"
)

EXPECTED_TOKEN = (
    "DR0048_G7D_HORIZON_ASSOCIATION_EXECUTION_AUTHORIZED"
)

EXPECTED_TRACSS_SHA = (
    "b8e49b9c40f974e5ff37e26ade3a855813436bb4c6dab4d7ddeccc8ff5752d39"
)

EXPECTED_SAMPLE_SHA = (
    "f8a103cbd268e7b6d3e95515e8a806da403511196145897997866adc1fab10ef"
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


def require(condition: bool, message: str):

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
# 1. Frozen artifact identities
# ================================================================

for path in (
    LAUNCHER,
    VALIDATION,
    EVALUATOR,
):

    require(
        path.is_file(),
        f"missing required file: {path}",
    )


require(
    sha256(LAUNCHER)
    == EXPECTED_LAUNCHER_SHA,
    "launcher SHA mismatch",
)

require(
    sha256(VALIDATION)
    == EXPECTED_VALIDATION_SHA,
    "validation SHA mismatch",
)

require(
    sha256(EVALUATOR)
    == EXPECTED_EVALUATOR_SHA,
    "evaluator SHA mismatch",
)


# ================================================================
# 2. Repository boundary
# ================================================================

require(
    git(
        "rev-parse",
        "HEAD",
    ).stdout.strip()
    == EXPECTED_HEAD,
    "HEAD changed",
)

require(
    git(
        "rev-parse",
        f"refs/tags/{EXPECTED_PARENT_TAG}^{{commit}}",
    ).stdout.strip()
    == EXPECTED_HEAD,
    "DR-0047 parent tag changed",
)

future_tag = git(
    "rev-parse",
    "-q",
    "--verify",
    f"refs/tags/{FUTURE_AUTHORIZATION_TAG}^{{commit}}",
    check=False,
)

require(
    future_tag.returncode != 0,
    "future authorization tag already exists",
)


# ================================================================
# 3. Real execution outputs must remain absent
# ================================================================

for path in REAL_OUTPUTS:

    require(
        not path.exists(),
        f"real association output already exists: {path}",
    )


# ================================================================
# 4. Import launcher without invoking main()
# ================================================================

spec = importlib.util.spec_from_file_location(
    "g7d_horizon_association_launcher_audit",
    LAUNCHER,
)

require(
    spec is not None
    and spec.loader is not None,
    "cannot create launcher import specification",
)

module = importlib.util.module_from_spec(
    spec
)

spec.loader.exec_module(
    module
)


# ================================================================
# 5. Exact frozen launcher constants
# ================================================================

require(
    module.DR0047_COMMIT
    == EXPECTED_HEAD,
    "DR0047 commit constant changed",
)

require(
    module.DR0047_TAG
    == EXPECTED_PARENT_TAG,
    "DR0047 tag constant changed",
)

require(
    module.AUTHORIZATION_TAG
    == FUTURE_AUTHORIZATION_TAG,
    "future authorization tag changed",
)

require(
    module.AUTHORIZATION_TOKEN
    == EXPECTED_TOKEN,
    "authorization token changed",
)

require(
    module.EVALUATOR_SHA256
    == EXPECTED_EVALUATOR_SHA,
    "evaluator SHA constant changed",
)

require(
    module.SAMPLE_SHA256
    == EXPECTED_SAMPLE_SHA,
    "sample SHA constant changed",
)

require(
    module.TRACSS_SHA256
    == EXPECTED_TRACSS_SHA,
    "protected-reference SHA constant changed",
)

require(
    module.H3_EVENT_INPUT_SHA256
    == EXPECTED_H3_EVENT_INPUT_SHA,
    "H3 event-input SHA changed",
)

require(
    module.H3_CANDIDATE_INPUT_SHA256
    == EXPECTED_H3_CANDIDATE_INPUT_SHA,
    "H3 candidate-input SHA changed",
)

require(
    module.H7_EVENT_INPUT_SHA256
    == EXPECTED_H7_EVENT_INPUT_SHA,
    "H7 event-input SHA changed",
)

require(
    module.H7_CANDIDATE_INPUT_SHA256
    == EXPECTED_H7_CANDIDATE_INPUT_SHA,
    "H7 candidate-input SHA changed",
)

require(
    module.N_EVENTS == 6927,
    "event census changed",
)

require(
    module.EXPECTED_REFERENCE_ROWS
    == 283595,
    "protected-reference row census changed",
)

require(
    module.ASSOCIATION_TOLERANCE_MIN
    == 5,
    "association tolerance changed",
)

require(
    set(module.OUTPUTS.keys())
    == {3, 7},
    "authorized output horizon set changed",
)


# ================================================================
# 6. Exact output namespaces
# ================================================================

expected_outputs = {
    3: {
        "event":
            "results/g7/G7D_H3_Horizon_Association_Event.csv",

        "summary":
            "results/g7/G7D_H3_Horizon_Association_Summary.json",

        "audit":
            "results/g7/G7D_H3_Horizon_Association_Execution_Audit.json",
    },

    7: {
        "event":
            "results/g7/G7D_H7_Horizon_Association_Event.csv",

        "summary":
            "results/g7/G7D_H7_Horizon_Association_Summary.json",

        "audit":
            "results/g7/G7D_H7_Horizon_Association_Execution_Audit.json",
    },
}

for horizon in (3, 7):

    for key in (
        "event",
        "summary",
        "audit",
    ):

        actual = str(
            module.OUTPUTS[
                horizon
            ][
                key
            ].relative_to(ROOT)
        ).replace(
            "\\",
            "/",
        )

        require(
            actual
            == expected_outputs[
                horizon
            ][
                key
            ],
            (
                "output namespace changed: "
                f"H{horizon} {key}"
            ),
        )


# ================================================================
# 7. Static execution-order / fail-closed audit
# ================================================================

verify_source = inspect.getsource(
    module.verify_execution_boundary
)

execute_source = inspect.getsource(
    module.execute_horizon
)

main_source = inspect.getsource(
    module.main
)

offline_source = inspect.getsource(
    module.offline_validate
)


# Authorization token and horizon are checked before authorization
# tag and before any evaluator/reference execution.
token_position = verify_source.find(
    "token != AUTHORIZATION_TOKEN"
)

horizon_position = verify_source.find(
    "horizon_days not in (3, 7)"
)

tag_position = verify_source.find(
    "git_tag_target"
)

require(
    token_position >= 0,
    "authorization-token guard missing",
)

require(
    horizon_position >= 0,
    "horizon guard missing",
)

require(
    tag_position >= 0,
    "authorization-tag guard missing",
)

require(
    token_position < tag_position,
    "authorization tag consulted before token guard",
)

require(
    horizon_position < tag_position,
    "authorization tag consulted before horizon guard",
)


# Explicit ancestry and parent-freeze controls.
for fragment in (
    '"merge-base"',
    '"--is-ancestor"',
    "DR0047_COMMIT",
    "DR0047_TAG",
):

    require(
        fragment in verify_source,
        f"missing ancestry/freeze guard: {fragment}",
    )


# H3 must begin from a clean working tree and may not coexist
# with any H7 output.
for fragment in (
    "if horizon_days == 3",
    "if untracked",
    "OUTPUTS[7]",
):

    require(
        fragment in verify_source,
        f"missing H3 boundary: {fragment}",
    )


# H7 requires exactly all three H3 outputs.
for fragment in (
    "expected_untracked_for_h7",
    "untracked != required",
    'OUTPUTS[3]["summary"]',
    'OUTPUTS[3]["audit"]',
):

    require(
        fragment in verify_source,
        f"missing H7/H3 dependency guard: {fragment}",
    )


# Hardened H3 integrity checks required before H7.
for fragment in (
    "event_output_sha256",
    "summary_output_sha256",
    "H3_EVENT_INPUT_SHA256",
    "H3_CANDIDATE_INPUT_SHA256",
    "candidate_rows_scanned",
    "protected_reference_rows_scanned",
    "association_tolerance_min",
    "association_threshold_inclusive",
    "automatic_retry_performed",
):

    require(
        fragment in verify_source,
        (
            "missing hardened H3-before-H7 "
            f"integrity control: {fragment}"
        ),
    )


# Execution boundary MUST precede evaluator loading/evaluation.
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
    ),
    "execution-call structure incomplete",
)

require(
    boundary_position
    < load_position
    < evaluation_position,
    (
        "protected evaluation can occur "
        "before authorization boundary"
    ),
)


# Only one requested horizon is executed per CLI invocation.
require(
    main_source.count(
        "execute_horizon("
    )
    == 1,
    "main() does not contain exactly one execution call",
)


# Offline validation must not execute either horizon.
for forbidden in (
    "evaluate_horizon(",
    "load_evaluator(",
):

    require(
        forbidden not in offline_source,
        (
            "offline validation can enter real "
            f"execution path: {forbidden}"
        ),
    )


# ================================================================
# 8. No network implementation in launcher
# ================================================================

tree = ast.parse(
    LAUNCHER.read_text(
        encoding="utf-8"
    )
)

import_names = set()

for node in ast.walk(tree):

    if isinstance(node, ast.Import):

        for alias in node.names:
            import_names.add(
                alias.name
            )

    elif isinstance(
        node,
        ast.ImportFrom,
    ):

        if node.module:
            import_names.add(
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
            for name
            in import_names
        ),
        (
            "launcher imports network-capable "
            f"module: {forbidden_prefix}"
        ),
    )


# ================================================================
# 9. Offline-validation evidence
# ================================================================

validation = json.loads(
    VALIDATION.read_text(
        encoding="utf-8"
    )
)

expected_validation = {
    "status":
        "G7D_HORIZON_ASSOCIATION_AUTHORIZATION_LAUNCHER_OFFLINE_VALIDATION_PASS",

    "analysis_role":
        "POST_CONFIRMATORY_PROPAGATION_HORIZON_SENSITIVITY",

    "evaluator_sha256":
        EXPECTED_EVALUATOR_SHA,

    "DR0047_commit":
        EXPECTED_HEAD,

    "DR0047_tag":
        EXPECTED_PARENT_TAG,

    "future_authorization_tag":
        FUTURE_AUTHORIZATION_TAG,

    "authorization_token":
        EXPECTED_TOKEN,

    "authorized_horizons_days":
        [3, 7],

    "H5_execution_permitted":
        False,

    "association_tolerance_min":
        5,

    "association_threshold_inclusive":
        True,

    "H3_before_H7_required":
        True,

    "H3_execution_audit_integrity_required_before_H7":
        True,

    "H3_event_hash_reverified_before_H7":
        True,

    "H3_summary_hash_reverified_before_H7":
        True,

    "DR0047_ancestry_required":
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
            "offline-validation semantic "
            f"mismatch: {key}"
        ),
    )


# ================================================================
# 10. Dynamic rejection-path tests
#
# These tests deliberately stop BEFORE evaluator loading,
# H3/H7 candidate access, or protected-reference access.
# ================================================================

before_outputs = {
    str(path):
        path.exists()
    for path
    in REAL_OUTPUTS
}

require(
    not any(
        before_outputs.values()
    ),
    "real outputs existed before rejection tests",
)


# Invalid H3 token.
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
    "invalid DR-0048 authorization token"
    in invalid_h3_text,
    "invalid H3 token rejection message missing",
)


# Invalid H7 token.
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
    "invalid DR-0048 authorization token"
    in invalid_h7_text,
    "invalid H7 token rejection message missing",
)


# H5 must be rejected by CLI argument choices.
h5_attempt = run_cli(
    [
        "--execute",
        "--horizon-days",
        "5",
        "--authorization-token",
        EXPECTED_TOKEN,
    ]
)

h5_text = (
    h5_attempt.stdout
    + "\n"
    + h5_attempt.stderr
)

require(
    h5_attempt.returncode != 0,
    "H5 sensitivity execution unexpectedly succeeded",
)

require(
    (
        "invalid choice"
        in h5_text.lower()
    ),
    "H5 rejection was not enforced by CLI horizon choices",
)


# Offline mode may not be combined with execution arguments.
offline_with_horizon = run_cli(
    [
        "--offline-validate",
        "--horizon-days",
        "3",
    ]
)

offline_text = (
    offline_with_horizon.stdout
    + "\n"
    + offline_with_horizon.stderr
)

require(
    offline_with_horizon.returncode != 0,
    "offline validation accepted an execution horizon",
)

require(
    (
        "offline validation accepts no execution arguments"
        in offline_text
    ),
    "offline/execution separation rejection message missing",
)


# Even the correct token is insufficient before the future
# authorization tag exists.
no_tag_attempt = run_cli(
    [
        "--execute",
        "--horizon-days",
        "3",
        "--authorization-token",
        EXPECTED_TOKEN,
    ]
)

no_tag_text = (
    no_tag_attempt.stdout
    + "\n"
    + no_tag_attempt.stderr
)

require(
    no_tag_attempt.returncode != 0,
    "real H3 execution succeeded without authorization tag",
)

require(
    "DR-0048 authorization tag is absent"
    in no_tag_text,
    "missing-tag authorization rejection message absent",
)


# Rejection tests must create no real output.
for path in REAL_OUTPUTS:

    require(
        not path.exists(),
        (
            "rejection test created a real "
            f"association output: {path}"
        ),
    )


# Future authorization tag must still be absent.
future_tag_after = git(
    "rev-parse",
    "-q",
    "--verify",
    f"refs/tags/{FUTURE_AUTHORIZATION_TAG}^{{commit}}",
    check=False,
)

require(
    future_tag_after.returncode != 0,
    "audit unexpectedly created authorization tag",
)


# ================================================================
# 11. Independent-audit result
# ================================================================

payload = {
    "status":
        "G7D_HORIZON_ASSOCIATION_AUTHORIZATION_LAUNCHER_INDEPENDENT_AUDIT_PASS",

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

    "evaluator_sha256":
        sha256(
            EVALUATOR
        ),

    "DR0047_commit":
        EXPECTED_HEAD,

    "DR0047_tag":
        EXPECTED_PARENT_TAG,

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

    "association_tolerance_min":
        5,

    "association_threshold_inclusive":
        True,

    "DR0047_ancestry_guard_verified":
        True,

    "H3_clean_tree_required":
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

    "H3_scientific_control_flags_reverified_before_H7":
        True,

    "H7_requires_exact_three_H3_outputs":
        True,

    "execution_boundary_precedes_evaluator_loading":
        True,

    "execution_boundary_precedes_protected_evaluation":
        True,

    "single_requested_horizon_per_invocation":
        True,

    "offline_validation_real_execution_isolated":
        True,

    "network_capable_launcher_import_detected":
        False,

    "invalid_H3_token_blocked":
        True,

    "invalid_H7_token_blocked":
        True,

    "correct_token_without_authorization_tag_blocked":
        True,

    "offline_execution_argument_mix_blocked":
        True,

    "real_H3_execution_performed":
        False,

    "real_H7_execution_performed":
        False,

    "H3_candidate_file_accessed_by_audit":
        False,

    "H7_candidate_file_accessed_by_audit":
        False,

    "protected_reference_accessed_by_audit":
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
        "FREEZE_DR0048_AUTHORIZATION_LAUNCHER_BEFORE_REAL_H3_H7_EXECUTION",
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