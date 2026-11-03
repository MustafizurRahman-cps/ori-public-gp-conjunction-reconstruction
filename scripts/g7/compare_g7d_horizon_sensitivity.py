from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

OUT = (
    ROOT
    / "results/g7/G7D_H3_H5_H7_Event_Level_Comparison.json"
)

H3_EVENT = ROOT / "results/g7/G7D_H3_Horizon_Association_Event.csv"
H3_SUMMARY = ROOT / "results/g7/G7D_H3_Horizon_Association_Summary.json"

H5_STATUS = ROOT / "results/g6/G6_PreOutcome_Candidate_Event_Status.csv"
H5_METRICS = ROOT / "results/g6/G6_Confirmatory_Event_Metrics.csv"
H5_SUMMARY = ROOT / "results/g6/G6_Confirmatory_Metric_Summary.json"

H7_EVENT = ROOT / "results/g7/G7D_H7_Horizon_Association_Event.csv"
H7_SUMMARY = ROOT / "results/g7/G7D_H7_Horizon_Association_Summary.json"

N = 6927


EXPECTED_HASHES = {
    H3_EVENT:
        "9dd8a7223177f94a11bfe2bad9ddafc61cb87eedb07a31a127222ceac3950920",

    H3_SUMMARY:
        "039c38715722fd6661f02a86a853d1bac9dfbac43e9eafe10990a790a02bcd92",

    H5_STATUS:
        "844a4c3f90a1b5bb738f13911b5e9f99f22bfe72e01bdb43dd6242e292b7b308",

    H5_METRICS:
        "e92553698deba0643e5c11ad48ddccab33c2f9a0fd9ce4298a6ee167860c0a79",

    H5_SUMMARY:
        "0b057d16ee25ce8beb6a729fc51d0aa330f980375bb23dcb7a5be51160dbd976",

    H7_EVENT:
        "3bcb364cedc2020daebb84ff76ef68c9c121d2e825bb39c19be73e308ce366b6",

    H7_SUMMARY:
        "017520ea63a7fbc70a8efa3ed9b9e6c9bc74d27ad5644cca91bbd073c23a8eda",
}


class ComparisonError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ComparisonError(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_bool(value):
    text = str(value).strip().lower()

    if text == "true":
        return True

    if text == "false":
        return False

    raise ComparisonError(f"invalid boolean: {value!r}")


def optional_float(value):
    text = str(value).strip()

    if not text:
        return None

    result = float(text)

    require(
        math.isfinite(result),
        f"nonfinite float: {value!r}",
    )

    return result


for path, expected in EXPECTED_HASHES.items():
    require(path.is_file(), f"missing input: {path}")
    require(
        sha256(path) == expected,
        f"input SHA mismatch: {path}",
    )


def load_sensitivity(path, horizon):

    result = {}

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        reader = csv.DictReader(stream)

        for row in reader:

            index = int(
                row["final_g6_sample_index"]
            )

            require(
                index not in result,
                f"duplicate H{horizon} event: {index}",
            )

            require(
                int(row["horizon_days"]) == horizon,
                f"wrong horizon in H{horizon}: {index}",
            )

            result[index] = {
                "stratum":
                    row["g6_temporal_stratum"].strip(),

                "conj_id":
                    row["conj_id"].strip(),

                "ready":
                    parse_bool(
                        row["candidate_ready"]
                    ),

                "associated":
                    parse_bool(
                        row["associated"]
                    ),

                "predicted_tca":
                    row[
                        "selected_candidate_time_utc"
                    ].strip(),

                "predicted_distance":
                    optional_float(
                        row[
                            "selected_candidate_distance_km"
                        ]
                    ),

                "Ed":
                    optional_float(
                        row[
                            "absolute_miss_distance_error_km"
                        ]
                    ),

                "Ed_covered":
                    (
                        parse_bool(
                            row["miss_distance_covered"]
                        )
                        if row[
                            "miss_distance_covered"
                        ].strip()
                        else None
                    ),

                "Et":
                    optional_float(
                        row[
                            "absolute_tca_error_s"
                        ]
                    ),

                "Et_covered":
                    (
                        parse_bool(
                            row["tca_covered"]
                        )
                        if row[
                            "tca_covered"
                        ].strip()
                        else None
                    ),
            }

    require(
        len(result) == N,
        f"H{horizon} census mismatch",
    )

    return result


h3 = load_sensitivity(H3_EVENT, 3)
h7 = load_sensitivity(H7_EVENT, 7)


h5_status = {}

with H5_STATUS.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    for row in csv.DictReader(stream):

        index = int(
            row["final_g6_sample_index"]
        )

        h5_status[index] = {
            "stratum":
                row["g6_temporal_stratum"].strip(),

            "conj_id":
                row["conj_id"].strip(),

            "ready":
                row[
                    "preoutcome_status"
                ].strip()
                == "CANDIDATE_SET_READY",
        }


h5 = {}

with H5_METRICS.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as stream:

    for row in csv.DictReader(stream):

        index = int(
            row["final_g6_sample_index"]
        )

        h5[index] = {
            "stratum":
                row["g6_temporal_stratum"].strip(),

            "conj_id":
                row["conj_id"].strip(),

            "ready":
                h5_status[index]["ready"],

            "associated":
                parse_bool(
                    row["valid_associated"]
                ),

            "predicted_tca":
                row["predicted_tca_utc"].strip(),

            "predicted_distance":
                optional_float(
                    row[
                        "predicted_miss_distance_km"
                    ]
                ),

            "Ed":
                optional_float(
                    row[
                        "absolute_miss_distance_error_km"
                    ]
                ),

            "Ed_covered":
                (
                    parse_bool(
                        row["miss_distance_covered"]
                    )
                    if row[
                        "miss_distance_covered"
                    ].strip()
                    else None
                ),

            "Et":
                optional_float(
                    row["absolute_tca_error_s"]
                ),

            "Et_covered":
                (
                    parse_bool(
                        row["tca_covered"]
                    )
                    if row[
                        "tca_covered"
                    ].strip()
                    else None
                ),
        }


require(
    len(h5_status) == N
    and len(h5) == N,
    "H5 census mismatch",
)


data = {
    3: h3,
    5: h5,
    7: h7,
}


for index in range(1, N + 1):

    identities = {
        (
            data[h][index]["stratum"],
            data[h][index]["conj_id"],
        )
        for h in (3, 5, 7)
    }

    require(
        len(identities) == 1,
        f"cross-horizon identity mismatch: {index}",
    )


ready = {
    h: {
        i
        for i in range(1, N + 1)
        if data[h][i]["ready"]
    }
    for h in (3, 5, 7)
}


associated = {
    h: {
        i
        for i in range(1, N + 1)
        if data[h][i]["associated"]
    }
    for h in (3, 5, 7)
}


require(
    [len(ready[h]) for h in (3, 5, 7)]
    == [6915, 6906, 6889],
    "candidate-ready counts changed",
)

require(
    [len(associated[h]) for h in (3, 5, 7)]
    == [6620, 6839, 6839],
    "association counts changed",
)


def pairwise_sets(a, b, source):

    return {
        "both":
            len(source[a] & source[b]),

        f"H{a}_only":
            len(source[a] - source[b]),

        f"H{b}_only":
            len(source[b] - source[a]),

        "neither":
            N - len(source[a] | source[b]),
    }


def coverage_transition(a, b, key):

    common = (
        associated[a]
        & associated[b]
    )

    counts = Counter()

    for index in common:

        left = data[a][index][key]
        right = data[b][index][key]

        require(
            left is not None
            and right is not None,
            f"missing {key}: {index}",
        )

        counts[
            (
                bool(left),
                bool(right),
            )
        ] += 1

    return {
        "covered_at_both":
            counts[(True, True)],

        f"covered_H{a}_only":
            counts[(True, False)],

        f"covered_H{b}_only":
            counts[(False, True)],

        "uncovered_at_both":
            counts[(False, False)],
    }


def compare_common(a, b):

    common = (
        associated[a]
        & associated[b]
    )

    exact_tca = 0
    exact_distance = 0
    exact_solution = 0

    max_ed = 0.0
    max_et = 0.0

    for index in common:

        left = data[a][index]
        right = data[b][index]

        tca_equal = (
            left["predicted_tca"]
            == right["predicted_tca"]
        )

        distance_equal = (
            left["predicted_distance"]
            == right["predicted_distance"]
        )

        exact_tca += int(tca_equal)
        exact_distance += int(distance_equal)
        exact_solution += int(
            tca_equal
            and distance_equal
        )

        require(
            left["Ed"] is not None
            and right["Ed"] is not None,
            f"missing Ed: {index}",
        )

        require(
            left["Et"] is not None
            and right["Et"] is not None,
            f"missing Et: {index}",
        )

        max_ed = max(
            max_ed,
            abs(
                right["Ed"]
                - left["Ed"]
            ),
        )

        max_et = max(
            max_et,
            abs(
                right["Et"]
                - left["Et"]
            ),
        )

    return {
        "common_associated_events":
            len(common),

        "exact_predicted_TCA_matches":
            exact_tca,

        "exact_predicted_distance_matches":
            exact_distance,

        "exact_predicted_solution_matches":
            exact_solution,

        "maximum_absolute_Ed_change_km":
            max_ed,

        "maximum_absolute_Et_change_s":
            max_et,
    }


def coverage_total(horizon, key):

    values = [
        data[horizon][i][key]
        for i in associated[horizon]
    ]

    require(
        all(value is not None for value in values),
        f"missing coverage values at H{horizon}",
    )

    successes = sum(
        int(bool(value))
        for value in values
    )

    return (
        successes,
        len(values),
        successes / len(values),
    )


def unique_successes(a, b, key):

    a_only = associated[a] - associated[b]
    b_only = associated[b] - associated[a]

    return {
        f"H{a}_only_events":
            len(a_only),

        f"H{a}_only_successes":
            sum(
                int(bool(data[a][i][key]))
                for i in a_only
            ),

        f"H{b}_only_events":
            len(b_only),

        f"H{b}_only_successes":
            sum(
                int(bool(data[b][i][key]))
                for i in b_only
            ),
    }


ed3 = coverage_total(3, "Ed_covered")
ed5 = coverage_total(5, "Ed_covered")
ed7 = coverage_total(7, "Ed_covered")

et3 = coverage_total(3, "Et_covered")
et5 = coverage_total(5, "Et_covered")
et7 = coverage_total(7, "Et_covered")


payload = {
    "status":
        "G7D_H3_H5_H7_EVENT_LEVEL_COMPARISON_PASS",

    "analysis_role":
        "POST_CONFIRMATORY_PROPAGATION_HORIZON_SENSITIVITY",

    "total_events":
        N,

    "primary_horizon_days":
        5,

    "sensitivity_horizons_days":
        [3, 7],

    "candidate_ready_counts": {
        "H3": len(ready[3]),
        "H5": len(ready[5]),
        "H7": len(ready[7]),
    },

    "associated_counts": {
        "H3": len(associated[3]),
        "H5": len(associated[5]),
        "H7": len(associated[7]),
    },

    "triple_candidate_ready_intersection":
        len(
            ready[3]
            & ready[5]
            & ready[7]
        ),

    "triple_associated_intersection":
        len(
            associated[3]
            & associated[5]
            & associated[7]
        ),

    "candidate_ready_pairwise": {
        "H3_vs_H5":
            pairwise_sets(3, 5, ready),

        "H5_vs_H7":
            pairwise_sets(5, 7, ready),

        "H3_vs_H7":
            pairwise_sets(3, 7, ready),
    },

    "association_pairwise": {
        "H3_vs_H5":
            pairwise_sets(
                3,
                5,
                associated,
            ),

        "H5_vs_H7":
            pairwise_sets(
                5,
                7,
                associated,
            ),

        "H3_vs_H7":
            pairwise_sets(
                3,
                7,
                associated,
            ),
    },

    "common_associated_solution_comparison": {
        "H3_vs_H5":
            compare_common(3, 5),

        "H5_vs_H7":
            compare_common(5, 7),

        "H3_vs_H7":
            compare_common(3, 7),
    },

    "Ed_coverage_transition_common_associated": {
        "H3_vs_H5":
            coverage_transition(
                3,
                5,
                "Ed_covered",
            ),

        "H5_vs_H7":
            coverage_transition(
                5,
                7,
                "Ed_covered",
            ),
    },

    "Et_coverage_transition_common_associated": {
        "H3_vs_H5":
            coverage_transition(
                3,
                5,
                "Et_covered",
            ),

        "H5_vs_H7":
            coverage_transition(
                5,
                7,
                "Et_covered",
            ),
    },

    "unique_membership_Ed_successes": {
        "H3_vs_H5":
            unique_successes(
                3,
                5,
                "Ed_covered",
            ),

        "H5_vs_H7":
            unique_successes(
                5,
                7,
                "Ed_covered",
            ),
    },

    "unique_membership_Et_successes": {
        "H3_vs_H5":
            unique_successes(
                3,
                5,
                "Et_covered",
            ),

        "H5_vs_H7":
            unique_successes(
                5,
                7,
                "Et_covered",
            ),
    },

    "aggregate_Ed": {
        "H3": {
            "successes": ed3[0],
            "denominator": ed3[1],
            "coverage": ed3[2],
        },

        "H5": {
            "successes": ed5[0],
            "denominator": ed5[1],
            "coverage": ed5[2],
        },

        "H7": {
            "successes": ed7[0],
            "denominator": ed7[1],
            "coverage": ed7[2],
        },
    },

    "aggregate_Et": {
        "H3": {
            "successes": et3[0],
            "denominator": et3[1],
            "coverage": et3[2],
        },

        "H5": {
            "successes": et5[0],
            "denominator": et5[1],
            "coverage": et5[2],
        },

        "H7": {
            "successes": et7[0],
            "denominator": et7[1],
            "coverage": et7[2],
        },
    },

    "nominal_90_percent_Ed_coverage_supported": {
        "H3": False,
        "H5": False,
        "H7": False,
    },

    "common_associated_predictions_invariant":
        True,

    "aggregate_differences_are_membership_driven":
        True,

    "H5_remains_primary":
        True,

    "horizon_optimization_or_selection_performed":
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
}


require(
    payload[
        "triple_candidate_ready_intersection"
    ]
    == 6889,
    "triple ready intersection changed",
)

require(
    payload[
        "triple_associated_intersection"
    ]
    == 6602,
    "triple associated intersection changed",
)

require(
    payload[
        "common_associated_solution_comparison"
    ][
        "H3_vs_H5"
    ][
        "exact_predicted_solution_matches"
    ]
    == 6616,
    "H3/H5 common solution reproduction failed",
)

require(
    payload[
        "common_associated_solution_comparison"
    ][
        "H5_vs_H7"
    ][
        "exact_predicted_solution_matches"
    ]
    == 6825,
    "H5/H7 common solution reproduction failed",
)

require(
    payload[
        "common_associated_solution_comparison"
    ][
        "H3_vs_H7"
    ][
        "exact_predicted_solution_matches"
    ]
    == 6602,
    "H3/H7 common solution reproduction failed",
)


OUT.parent.mkdir(
    parents=True,
    exist_ok=True,
)

with OUT.open(
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