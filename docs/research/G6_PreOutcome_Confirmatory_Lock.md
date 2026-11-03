# G6 Pre-Outcome Confirmatory Experiment Lock

Status: **PROPOSED FINAL LOCK — READY FOR INDEPENDENT REVIEW**

Protected-outcome state: **UNOPENED**

This document incorporates the human design decision approving the full reserve census, minimum-support floor, and five chronological strata. It is not operationally active until independent human review approves this final generated lock and sample register. No G6 acquisition or protected-outcome access is authorized before that approval.

## 1. Authority and evidence boundary

The controlling plan requires a dated experiment lock before confirmatory held-out outcomes are inspected. It requires the model, preprocessing and inclusion rules, history/lookback, propagation and screening horizon, thresholds, baselines, primary metric, statistical procedure, and any resampling scheme or seed to be fixed before evaluation. It prohibits changes driven by favorable or disappointing held-out results.

This proposal uses only the following frozen pre-outcome evidence:

- `ORI_Q1_Final_Research_Plan_v1.1.docx`, SHA-256 `51c301689e37ce47bd4aa0dc6cbb2931add97c82965603d24e5e718f06ffcc6f`;
- `docs/decisions/DR-0007_G5_Gate_Approval.md`, SHA-256 `621fe01b4be343ad0da44bab01de6ee2b3c0f6b5481df719006f89cedba6777f`;
- `docs/control/G5_State_Summary.md`, SHA-256 `2877e8140ea9a4568e79c722268ebcd69e3a5f0f8df3632bfd21500dbfb98973`;
- `docs/research/G5_V2_G6_Candidate_Reserve_Register.csv`, SHA-256 `a44a56c0eb78e4b084155274dd54fc280691213dcdedc15d00e3825d870c7ee8`;
- `docs/research/G5_PreOutcome_Calibration_Lock_v2.md`, SHA-256 `e1fbee0e4f793c4d974227faa7f959f3597c80788eabb93ea0786f214a63499d`;
- `results/g5/G5_CAL0_Final_Development_Fit_v2.json`, SHA-256 `4dcd7e58bf614329420d52a1a80a6aa376ad464ceb8da20d20a7c33eaa44e48e`;
- `docs/research/G5_Calibration_Numerical_Closure_v2.md`, SHA-256 `16c4a0bc634caa134b0754b10cfe2d2d44b35cb4b3da112f611a3975e59d6287` for frozen residual definitions only.

No raw Space-Track record or G6 protected field was inspected.

## 2. Controlling-plan determination

The plan fixes the need for an external/reference conjunction benchmark, a pre-outcome lock, defensible primary metrics and uncertainty intervals, retained failures, and no post-hoc retuning. It does not fix an exact G6 sample size, a power or precision threshold, final membership, chronological strata, or a binary primary-confirmation decision rule.

The approved G5 record fixed a 6,927-event object-disjoint candidate reserve but did not itself make that reserve the final G6 sample. The subsequent human design decision approved the full reserve census, all 13,854 objects, the minimum `n_valid_associated = 141` floor, and the five chronological strata specified below. Independent review of this completed pre-outcome lock remains required before execution.

## 3. Frozen G5 handoff

The following inputs are immutable in G6:

- inference target: object-disjoint catalog-derived IADC-LEO conjunction events selected under the frozen metadata-only allocation rule;
- selected lead: `L* = 48 h`;
- replay semantics: at cutoff `t_ref - 48 h`, select each object's lexicographically current causal GP; set replay time to the later `CREATION_DATE` of the two selected GPs; reselect both objects at replay and require both identities to remain unchanged;
- retained uncertainty method: CAL-0;
- fixed primary 90% `E_d` radius: `78.75554652312711 km`;
- fixed secondary 90% `E_t` radius: `7.105892 s`.

These radii are development-fitted confirmatory inputs. They are not G6 performance estimates and may not be refitted, tuned, smoothed, or replaced using G6 outcomes.

## 4. Final confirmatory population and sample rule

The candidate reserve contains 6,927 unique conjunction IDs and 13,854 distinct objects. It spans reference TCA from `2025-01-04T12:00:13.371034Z` through `2025-01-08T11:57:35.599207Z`. Every row is catalog-derived TLE/catalog-derived TLE, IADC-LEO eligible, non-boundary-sensitive, not G2-development-exposed, and marked `G6_CANDIDATE_RESERVE — OUTCOME UNOPENED`. It is object-disjoint internally and has zero G5 object overlap.

### Final membership rule

Use the entire frozen 6,927-event reserve as a census of the available allocation-defined G6 target population. Membership is exactly every row of `docs/research/G5_V2_G6_Candidate_Reserve_Register.csv`, in ascending `reserve_event_index`; no outcome-based sampling, replacement, ranking, or filtering occurs.

The metadata-only final register is `docs/research/G6_Final_Confirmatory_Sample_Register.csv`, SHA-256 `f8a103cbd268e7b6d3e95515e8a806da403511196145897997866adc1fab10ef`. It contains 6,927 unique conjunctions and 13,854 distinct objects, preserves canonical reserve order and membership exactly, is internally object-disjoint, and has zero overlap with the 354 G5 development objects.

The register's membership is frozen by the human design decision, but acquisition and outcome access remain prohibited until independent review approves this proposed final lock and its register.

### Frozen minimum-support rule

Primary fixed-radius coverage is reportable for any valid-associated denominator, but confirmatory calibration classification requires at least 141 valid-associated events. This reuses the frozen pre-outcome Wilson planning rule at nominal `p0 = 0.90`: the two-sided 95% Wilson half-width is `0.049995086961` at `n = 141`. If all 6,927 candidates were valid-associated, the planning half-width at `p0 = 0.90` would be `0.007066269920`; this is a planning quantity, not a result.

If fewer than 141 events are valid-associated, the primary calibration conclusion is `INSUFFICIENT CAUSAL/EVALUABLE SUPPORT`, without sample replacement or threshold relaxation.

### Fixed temporal strata

The approved full-reserve census is partitioned into five contiguous strata in canonical reserve order:

- G6-T1: reserve indices 1–1,386 (`n = 1,386`);
- G6-T2: 1,387–2,772 (`n = 1,386`);
- G6-T3: 2,773–4,157 (`n = 1,385`);
- G6-T4: 4,158–5,542 (`n = 1,385`);
- G6-T5: 5,543–6,927 (`n = 1,385`).

These strata are fixed by allocation-safe chronology only. They support temporal-stability reporting and may not be revised using outcomes. No other confirmatory subgroup is authorized; any later diagnostic subgroup is exploratory.

## 5. Protected-information embargo

Before the final lock is approved and hashed, do not open, inspect, summarize, compute, sort, rank, filter, or use for any G6 candidate:

- physical reference miss distance, including `min_range`;
- `mdistance`;
- reconstructed miss distance;
- signed or absolute `E_d`;
- reference/reconstructed TCA difference or `E_t`;
- reference state-vector components;
- relative speed or any outcome-derived kinematic quantity;
- `P_c`, covariance-like values, HBR, or collision-risk products;
- any label, ranking, exclusion, or status derived from these fields.

Allowed pre-outcome metadata are limited to canonical reserve index, stable conjunction ID, object IDs, reference TCA, catalog-derived source class, frozen IADC-LEO eligibility, boundary-sensitivity flag, prior development-exposure status, and frozen allocation status. Reference TCA may be used only for chronology, causal cutoffs, and the frozen association operation.

## 6. GP_HISTORY acquisition contract

Acquisition is designed here but remains unauthorized until final sample approval.

1. The requested NORAD object set is the exact union of `obj1` and `obj2` in the approved final sample register. Arbitrary IDs and replacement objects are prohibited.
2. For each object/event, acquire GP_HISTORY with `CREATION_DATE` in `[t_ref - 180 d, t_ref)` plus the latest predecessor before the lower bound where available.
3. No record with `CREATION_DATE >= t_ref` may enter the causal information set or replay.
4. Preserve every raw response locally under `data/raw/g6/`; do not commit or redistribute restricted raw responses absent explicit permission.
5. Record exact query semantics, selected objects, event/time bounds, retrieval UTC, HTTP/result state, row count, byte count, SHA-256, format, local path, and access restriction.
6. Verify `NORAD_CAT_ID`, `OBJECT_ID` where available, `GP_ID`, `EPOCH`, `CREATION_DATE`, and TLE/OMM propagation fields. Record missing values, parse failures, duplicates, and ordering without silently dropping rows.
7. The predecessor is the lexicographically latest record before the 180-day lower bound under (`CREATION_DATE`, `EPOCH`, `stable_tie_key`), where `stable_tie_key` is `GP_ID` when present and otherwise the SHA-256 of the canonical preserved record, matching the frozen G3 rule. If none is returned, record `LEFT_CENSORED_NO_PREDECESSOR_RETURNED`; do not fabricate one.
8. An authenticated empty result is not a transport failure. Distinguish successful-empty, authentication/HTTP/transport failure, and schema failure.
9. Use local-only authenticated Space-Track handling. Never place credentials, cookies, tokens, or session details in the repository.
10. Stop on an allocation/hash mismatch, unexpected object, protected-field exposure, schema change, causal-bound violation, or ambiguous transport result. Do not compensate by changing membership, time bounds, batch size, or timeout without an approved amendment.

## 7. Replay, propagation, and association

For each approved event:

1. Define nominal cutoff `t_c = t_ref - 48 h`.
2. For each object, select the lexicographically latest GP satisfying `CREATION_DATE <= t_c`, ordered by (`CREATION_DATE`, `EPOCH`, `stable_tie_key`), where `stable_tie_key` is `GP_ID` when present and otherwise the SHA-256 of the canonical preserved record.
3. Define replay time as the later `CREATION_DATE` of those two selected GPs.
4. Reselect both objects at replay time and require the GP identities to equal the cutoff-selected identities. An identity change is an integrity failure, not a reason to substitute another record.
5. Reuse the frozen G4 common-TEME/UTC SGP4 propagation, candidate-generation, numerical-refinement, deduplication, and association implementation unchanged: `H = 5 d` and `delta_assoc = 5 min`.
6. Generate the candidate set before reference association. Do not tune horizon, search grid, tolerances, deduplication, tie rules, or association threshold using G6 outcomes.

`scripts/g5/select_lstar_controlflow_amended.py` preserves the validated redundancy-safe numerical-control-flow logic but is a historical exact-cutoff L-star implementation. Historical exact-cutoff artifacts, including `scripts/g5/select_lstar.py`, remain audit history. Final publication-driven replay semantics were established by the G5 replay-time correction and implemented in `scripts/g5/run_g5_calibration_v2.py`: select each object's lexicographically current causal GP at `t_ref - 48 h`, set replay time to the later `CREATION_DATE`, reselect both objects at replay, and require unchanged identities. G6 reuses those replay semantics and the frozen G4 numerical implementation. The G5 calibration implementation may be reused only for already-defined metric calculations with fixed radii; it may not refit CAL-0.

## 8. Evaluability and failure handling

Assign exactly one terminal status per event using this precedence:

1. `NO_CAUSAL_GP_OBJECT_1`;
2. `NO_CAUSAL_GP_OBJECT_2`;
3. `GP_PARSE_FAILURE`;
4. `PROPAGATION_FAILURE`;
5. `UNRECOVERED_REFINEMENT_FAILURE` if a required refinement cannot be recovered under the frozen redundancy-safe control flow;
6. `NO_VALID_CANDIDATE`;
7. `UNASSOCIATED_REFERENCE_EVENT`;
8. `VALID_ASSOCIATED`.

`Evaluable` requires both causal GPs, successful parsing/propagation/refinement, and at least one valid candidate. `VALID_ASSOCIATED` additionally requires frozen five-minute association to the reference TCA.

Recoverable `LOCAL_NEIGHBORHOOD_INCONSISTENT` cases remain within the validated redundancy-safe control flow and are not collapsed into terminal propagation or refinement failure. All approved sample events remain in the denominator for evaluability and valid-association rates. Only `VALID_ASSOCIATED` events enter conditional `E_d` and `E_t` distributions and fixed-radius coverage denominators. Invalid or unevaluable events receive no numerical error penalty and are not silently converted to calibration successes or failures.

## 9. Confirmatory estimands and metrics

The event is the analysis unit.

### Primary process estimands

- evaluability rate = evaluable events / all frozen sample events;
- valid-association rate = `VALID_ASSOCIATED` events / all frozen sample events.

Report exact numerators, denominators, proportions, and two-sided 95% Wilson intervals.

### Primary scientific estimand

Among `VALID_ASSOCIATED` events, define signed miss-distance residual `r_d = d_hat - d_ref` and `E_d = |r_d|` in km, where the physical scalar reference miss distance is `min_range`; `mdistance` is never used as physical miss distance.

Report conditional `E_d` median, P90, P95, P99, and maximum. Quantiles use the deterministic linear empirical-quantile rule used by the frozen G5 implementation.

Fixed CAL-0 90% coverage is:

`I(E_d <= 78.75554652312711 km)`.

Report exact successes/valid-associated denominator, observed coverage, two-sided 95% Wilson interval, and `|coverage - 0.90|`. The radius remains fixed.

### Secondary TCA estimand

Among `VALID_ASSOCIATED` events, define `r_t = t_hat - t_ref` and `E_t = |r_t|` in seconds. Report the analogous distribution summaries and fixed coverage `I(E_t <= 7.105892 s)`, exact successes/denominator, two-sided 95% Wilson interval, and absolute deviation from 0.90. TCA evidence is secondary.

### Temporal reporting

For each of the five prespecified chronological strata, report total sample events; evaluable count and rate; valid-associated count and rate; fixed-radius `E_d` successes and denominator; observed `E_d` coverage; two-sided 95% Wilson interval; `|coverage - 0.90|`; and conditional `E_d` median, P90, P95, P99, and maximum. Report the ordered range of observed coverage across the five strata. These are prespecified descriptive temporal-transport/stability evidence, not five co-primary hypothesis tests; no multiplicity-based confirmatory claim is authorized. Outcome-driven subgroup discovery is prohibited as confirmatory evidence.

## 10. Statistical inference

- Analysis unit: one object-disjoint conjunction event.
- Confidence level: two-sided 95%.
- Binary interval: Wilson score interval with `z = 1.959963984540054`.
- Primary confirmatory quantity: fixed CAL-0 90% `E_d` coverage among valid-associated events.
- Secondary quantity: fixed CAL-0 90% `E_t` coverage.
- Evaluability, association, conditional distribution summaries, and temporal strata are required supporting/descriptive evidence.
- No bootstrap is required for the primary binary-coverage conclusion. Distribution quantiles are descriptive point estimates; adding bootstrap intervals would require a pre-outcome amendment with a fixed method and seed.
- No multiplicity correction is applied because there is one primary calibration estimand. Secondary and descriptive metrics are not co-primary hypotheses.
- Missing and unsupported events are handled only by the frozen status and denominator rules in Section 8.
- Alpha, confidence method, sample, strata, estimator, radii, and association rules may not change after outcome access.

## 11. Confirmatory interpretation rule

- `CONSISTENT WITH NOMINAL 90% COVERAGE`: at least 141 events are valid-associated and the two-sided 95% Wilson interval for fixed-radius `E_d` coverage contains 0.90.
- `NOMINAL 90% COVERAGE NOT SUPPORTED`: at least 141 events are valid-associated and the Wilson interval excludes 0.90. Always state direction: `UNDERCOVERAGE` when observed coverage is below 0.90, or `CONSERVATIVE OVERCOVERAGE` when observed coverage is above 0.90. Do not refit.
- `INSUFFICIENT CAUSAL/EVALUABLE SUPPORT`: fewer than 141 events are valid-associated. Report all failure categories and do not replace events.

Wilson-interval containment is consistency/non-rejection evidence. It is not an equivalence test and does not prove exact calibration. No equivalence margin is defined. Temporal-stratum results are reported descriptively under Section 9 and cannot independently generate a multiplicity-based confirmatory claim.

These classifications concern the frozen pair-conditioned reconstruction/calibration method. They are not a paper-level pass/fail rule. All findings, including unfavorable results, must be retained.

## 12. Stopping and amendment rules

- Once any protected G6 outcome is opened, final membership cannot change.
- Difficult, missing, failed, or unfavorable events cannot be replaced.
- CAL-0 radii, lead time, replay rule, horizon, numerical settings, association tolerance, metrics, strata, and confidence method cannot be tuned after exposure.
- Stop before outcome access if final membership or any source hash differs from the approved lock.
- Stop during execution for protected-field exposure outside the approved evaluation path, an unexpected schema/frame/time-system condition, or a material implementation defect.
- A genuine numerical bug requires an explicit amendment, preserved pre-access evidence, impact analysis, and independent review. Bug correction must be distinguished from outcome-driven scientific adaptation.
- If a changed method has seen the confirmatory outcomes, the exposed G6 sample loses pristine confirmatory status for that changed method. Subsequent use is exploratory unless a new external/temporal test is obtained.

## 13. Interpretation and generalizability boundary

G6 evaluates the frozen pair-conditioned public-GP reconstruction and calibration method within the frozen allocation-defined population. It does not establish live catalog screening recall, operational warning capability, covariance accuracy, collision probability, maneuver effectiveness or safety, or operational certification.

Generalization remains limited by metadata/sample representativeness, the object-disjoint allocation target, the TraCSS/reference dataset and its short temporal window, and dependence on public-GP availability and publication processes. Object-disjointness removes shared-spacecraft overlap but does not prove independence from common temporal, catalog-generation, observation/update, environmental, or propagation-model systematics.

## 14. Required future G6 artifacts

After final lock approval and before or during authorized execution, retain:

1. approved pre-outcome experiment lock and decision record;
2. final confirmatory sample register containing allocation-safe metadata only, with SHA-256;
3. frozen configuration and input-hash record;
4. GP_HISTORY acquisition/provenance register and local raw-response manifest;
5. event-level confirmatory table with terminal status and approved outcome fields only after authorized exposure;
6. summary metrics table with pooled and prespecified temporal-stratum results;
7. integrity/leakage/failure audit;
8. deterministic rerun evidence where feasible;
9. machine-readable sources and metadata for any later T09/F09/F10 candidate assets.

No outcome-based publication table or figure is generated at this design stage.

## 15. Independent-review boundary

The human design decision approved the full 6,927-event census, all 13,854 objects, the `n_valid_associated >= 141` minimum-support floor, the five fixed chronological strata, and the scientifically corrected interpretation framework in Section 11. The generated final sample register is `docs/research/G6_Final_Confirmatory_Sample_Register.csv`, SHA-256 `f8a103cbd268e7b6d3e95515e8a806da403511196145897997866adc1fab10ef`.

Status remains `PROPOSED FINAL LOCK — READY FOR INDEPENDENT REVIEW`. Independent human approval of this final generated lock and register is required before Space-Track acquisition, protected-outcome access, or confirmatory computation. No such execution is authorized by this document alone.
