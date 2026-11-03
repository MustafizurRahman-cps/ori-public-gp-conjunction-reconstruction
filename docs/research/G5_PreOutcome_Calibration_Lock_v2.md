# G5 Pre-Outcome Calibration Lock v2

Status: **FROZEN BEFORE SPACE-TRACK ACQUISITION OR PROTECTED-OUTCOME ACCESS**  
Lock creation UTC: `2026-09-09T19:21:50.010Z`  
Gate: G5 — Uncertainty / Quality Model Validation  
Controlling authority: `ORI_Q1_Final_Research_Plan_v1.1.docx`

## 1. Lineage, human approval, and supersession

The immutable v1 lock is `docs/research/G5_PreOutcome_Calibration_Lock.md`, created at `2026-09-09T04:00:35.743Z` with SHA-256 `e57f53e9137244aab17bdb0ed8707572f79de073734b4ae219038f4eef75a858`. Its whole-network connected-component rule yielded 76 effective components from 267,245 eligible events, below the locked 141-unit requirement. The resulting v1 G5 decision was HOLD. No L-star was selected, Space-Track was not queried, and no protected geometry-error outcome was opened.

The v1 lock, its addendum, and its HOLD result remain historically valid and immutable. The pre-outcome revision trail is:

- `docs/research/G5_PreOutcome_Dependence_Revision_Audit.md`;
- `docs/research/G5_TimeStratified_ObjectDisjoint_Allocation_Audit.md`.

Independent human scientific review approved changing the future G5 dependence unit from a whole-network connected component to an object-disjoint selected event and approved a 72 h calibration window. This v2 lock supersedes v1 for future G5 execution but does not edit, erase, or invalidate v1.

All 24/48/72 h diagnostic windows filled the exact 177-event quotas and retained more than 141 later reserve events. Human review selected 72 h before acquisition or protected-outcome access because it maximizes temporal development support among those already tested windows and avoids unnecessary one-day concentration. This human decision supersedes the diagnostic shortest-passing rule and is not retrospective outcome tuning.

## 2. Scientific interpretation and evidence boundary

G5 evaluates public-GP quality and empirical reconstruction-error calibration. It does not construct or claim operational covariance, confidence ellipsoids, collision probability, Mahalanobis risk, or operational confidence.

At v2 lock creation:

- protected reference miss distance, relative speed, Pc, state components, covariance, reconstruction error, TCA error, stability magnitude, ORI performance, and calibration outcomes remained unopened;
- Space-Track remained unqueried for G5;
- no GP_HISTORY or TraCSS data was acquired;
- calibration had not started;
- G6 had not started.

## 3. Canonical G5 v2 membership

The canonical register is `docs/research/G5_V2_Calibration_Allocation_Register.csv` (177 rows, 354 globally unique objects; SHA-256 `78ea7546d0cf679fc00fcb9414c9ba7c1e032b8ec9ab981b9bc9fdb06d975514`). It contains allocation-safe metadata only. No selected event or object is G2 development-exposed. Every row remains outcome-unopened at this lock.

Membership is immutable after the hash freeze. No event may move between blocks after protected-outcome access.

## 4. Frozen 72 h temporal design

Reference start:

`T0 = 2025-01-01T12:00:12.363233Z`.

Frozen G5 development window:

`[T0, T0 + 72 h) = [2025-01-01T12:00:12.363233Z, 2025-01-04T12:00:12.363233Z)`.

The five blocks are equal-duration, each 14 h 24 min, and left-closed/right-open:

1. Block 1: `[2025-01-01T12:00:12.363233Z, 2025-01-02T02:24:12.363233Z)` — quota 36.
2. Block 2: `[2025-01-02T02:24:12.363233Z, 2025-01-02T16:48:12.363233Z)` — quota 36.
3. Block 3: `[2025-01-02T16:48:12.363233Z, 2025-01-03T07:12:12.363233Z)` — quota 35.
4. Block 4: `[2025-01-03T07:12:12.363233Z, 2025-01-03T21:36:12.363233Z)` — quota 35.
5. Block 5: `[2025-01-03T21:36:12.363233Z, 2025-01-04T12:00:12.363233Z)` — quota 35.

Total G5 events: 177. Blocks 2–5 contain 141 potential out-of-fold validation units.

## 5. Frozen allocation algorithm

Inside each temporal block, order eligible events by:

`SHA256("ORI-G5-V2|" + conj_id)` ascending hexadecimal,

then break any tie by reference TCA and stable/numeric conjunction ID. Traverse that order and select an event only if neither object has appeared in any previously selected G5 event. Carry the used-object set across block boundaries. Do not refill one block from another. All block quotas remain exact.

The hash priority is a study-allocation rule only. It does not enter propagation, reconstruction, TCA search, association, uncertainty estimation, or scientific prediction.

## 6. Effective unit and dependence interpretation

One selected object-disjoint conjunction event is one primary G5 planning/analysis unit because no selected event shares either spacecraft with another selected event. This removes the shared-spacecraft dependence that caused the v1 giant-component pathology.

Object-disjointness does not prove independence from common temporal, environmental, catalog-generation, observation/update, or propagation-model systematics. Temporal-block variation must be reported. The 177 events must not be described as perfectly IID.

## 7. Wilson precision rule

The nominal target is 90% empirical absolute-error coverage. The primary planning interval is the two-sided 95% Wilson interval with required half-width at most 0.05 at `p0=0.90`.

`h(n,p0) = z/(1+z^2/n) * sqrt[p0(1-p0)/n + z^2/(4n^2)]`, with `z=1.959963984540054`.

- `h(140,0.90) = 0.050176422903`;
- `h(141,0.90) = 0.049995086961`;
- frozen threshold: `n=141`.

The threshold may not be lowered later.

## 8. Forward out-of-fold architecture

The five temporal blocks remain strictly forward:

- Block 1: training only.
- Block 2: train on Block 1; validate Block 2.
- Block 3: train on Blocks 1–2; validate Block 3.
- Block 4: train on Blocks 1–3; validate Block 4.
- Block 5: train on Blocks 1–4; validate Block 5.

No future-block training and no random event-fold reassignment are allowed. The maximum potential OOF validation count is `36+35+35+35=141`. Actual evaluable count may be lower because causal availability, parsing, propagation, candidate generation, or association can fail.

## 9. Frozen future G6 candidate-reserve rule

The reserve begins at `TCA >= 2025-01-04T12:00:12.363233Z`. Exclude every event containing any of the 354 frozen G5 objects. Traverse the remaining later eligible metadata events by reference TCA and stable/numeric conjunction ID, selecting an event only when neither object has appeared in another selected reserve event.

The canonical register is `docs/research/G5_V2_G6_Candidate_Reserve_Register.csv` (6,927 events, 13,854 distinct objects; SHA-256 `a44a56c0eb78e4b084155274dd54fc280691213dcdedc15d00e3825d870c7ee8`). G5/G6 object overlap is zero.

Status: `G6_CANDIDATE_RESERVE — OUTCOME UNOPENED`. This is not a final G6 confirmatory sample. No G6 protected outcome may be inspected in G5.

## 10. Primary inference target

The frozen primary inference target is exactly:

`object-disjoint catalog-derived IADC-LEO conjunction events selected under the frozen metadata-only calibration/confirmation allocation rule`.

The primary target is not all 267,245 dense TraCSS event rows. The full dense network remains descriptive and generalizability context. Source-population size alone does not establish population representativeness.

## 11. Metadata generalizability limitation

The object-disjoint G5 and later G6 candidate samples differ in allocation-safe metadata, including repeated-pair-origin frequency, full-population object-degree distribution, and high-degree-object prevalence. Increasing the diagnostic window did not eliminate these differences. Boundary-sensitive events are rare and must remain explicitly reported.

These differences do not invalidate the design, do not permit post-lock sample changes, and must be reported as validity-domain/generalizability evidence. They may never be corrected or tuned using protected outcomes.

## 12. L-star selection rule and addendum boundary

After separately authorized G5-only GP_HISTORY acquisition, evaluate `L in {72,48,24,12,6} h` in descending order. For each selected G5 event, use the latest causal replay satisfying `t_r <= t_ref-L`.

L selection may use only GP availability, parser validity, propagation validity, candidate-set existence, reference-association status, and effective event count. It may not use miss-distance error, TCA error, favorable predicted distance, stability magnitude, or any CAL result.

The eligible planning count is fixed at 177. Select the greatest L with at least 141 valid-associated/evaluable G5 events. If none reaches 141, G5 is HOLD/REVISE. Do not lower the threshold. Record the result in a separate v2 L-star addendum after acquisition and before protected error-outcome access.

Current L-star status: **NOT YET DETERMINED**.

## 13. Future GP_HISTORY acquisition contract

No acquisition is authorized by this lock-creation continuation. After separate human authorization, query authenticated Space-Track GP_HISTORY only for the 354 G5 calibration objects. Do not acquire G6 objects during G5.

For each event/object, acquire `CREATION_DATE in [t_ref-180 d,t_ref)` plus the latest predecessor before the lower bound where available. No publication with `CREATION_DATE >= t_ref` may enter causal replay. Preserve raw responses locally and record query, object IDs, retrieval UTC, row count, byte count, SHA-256, path, and access restriction. Raw Space-Track responses remain local-only unless redistribution permission is explicitly established.

## 14. Frozen quality variables

Per object:

- state age;
- publication age;
- 30 d publication count;
- 30 d distinct-state count;
- median cadence;
- maximum publication gap;
- left-censor flag;
- propagation span.

Pair summaries:

- maximum state age;
- maximum publication age;
- minimum distinct-state count;
- maximum recent gap;
- maximum propagation span.

No scalar quality index and no hard GP-age censor are authorized. Frozen pair-maximum-state-age strata are `<1 d`, `1–<2 d`, `2–<3 d`, `3–<5 d`, `5–<7 d`, and `>=7 d`. No adaptive merging is permitted after error access.

## 15. Frozen residual definitions

For valid-associated G5 events only:

- signed TCA residual: `r_t=t_hat-t_ref [s]`;
- absolute TCA error: `E_t=|r_t| [s]`;
- signed miss-distance residual: `r_d=d_hat-d_ref [km]`;
- absolute miss-distance error: `E_d=|r_d| [km]`.

`INVALID`, `INSUFFICIENT INFORMATION`, and `UNASSOCIATED_REFERENCE_EVENT` remain status outcomes and receive no fabricated residual. Coverage/evaluability is reported before conditional error.

## 16. Frozen CAL-0

CAL-0 is a global empirical reconstruction-error envelope. For `n` training errors sorted ascending at nominal level `1-alpha`, use order statistic index

`k=min(n,ceil((n+1)*(1-alpha)))`.

Primary nominal coverage is 90%; 50%, 80%, and 95% are descriptive. Apply the rule primarily to `E_d` and secondarily to `E_t`. The result is an empirical reconstruction-error envelope, not covariance, a confidence ellipsoid, operational probability, or collision probability.

Current CAL-0 status: **NOT STARTED**.

## 17. Frozen CAL-AGE

CAL-AGE uses the same finite-sample empirical-envelope estimator separately within the frozen pair-maximum-state-age bins. No smoothing, pooling, interpolation, adaptive bins, machine-learning regression, or post-outcome bin merging is allowed. Unsupported strata are `INSUFFICIENT CALIBRATION SUPPORT`.

Retain CAL-AGE only if its primary OOF 90% `E_d` Wilson interval contains 0.90, every reported primary stratum reports its support explicitly, and its absolute calibration error is no worse than CAL-0. Otherwise retain CAL-0 only if CAL-0 itself is adequately calibrated. If CAL-0 fails, do not force an interval model; `NO CALIBRATED EMPIRICAL ERROR ENVELOPE RETAINED` is permitted.

Current CAL-AGE status: **NOT STARTED**.

## 18. Primary and secondary calibration metrics

Primary: OOF 90% miss-distance-envelope coverage. Report successes, denominator, observed coverage, two-sided 95% Wilson interval, `|coverage-0.90|`, and median predicted envelope radius. The phrase `CALIBRATED ON G5 DEVELOPMENT DATA` is permitted only if the Wilson interval contains 0.90. This is development calibration, not external confirmation.

Secondary: TCA-envelope coverage; 50/80/95% diagnostics; fold-by-fold variation.

## 19. Quality-usefulness analysis

Preserve transparent Spearman associations between `E_d` and pair maximum state age, pair minimum distinct-state count, pair maximum recent gap, and pair maximum propagation span. Use 2,000 bootstrap replicates with fixed seed `20260908`; one selected object-disjoint event is the resampling unit. Also report estimates separately by the five temporal blocks where support permits. Do not derive an operational classification threshold.

## 20. Temporal dependence and heterogeneity limitation

Object-disjointness removes shared-spacecraft overlap but does not rule out common environmental errors, catalog-generation systematics, propagation-model systematics, or temporally correlated observation/update conditions. Report OOF results by temporal block, variability across the four validation blocks, and any obvious temporal concentration of failures/errors.

The Wilson interval remains the prespecified primary event-level calibration interval, accompanied by this limitation.

## 21. Legacy baseline

The legacy sigma/trend proxy may be used only as a historical quality or rank baseline where dimensionally defensible. It is not covariance, uncertainty covariance, a Mahalanobis quantity, or probability. Do not force interval calibration from it.

## 22. B4 decision

`B4 NOT RETAINED — FAIR REPRODUCTION NOT ESTABLISHED`.

The frozen G1 evidence does not preserve complete equations, fitting inputs, weighting/outlier details, and evaluation conditions for exact reproduction without approximation. Do not repeat G1 searches merely to force B4 into the manuscript. B4 remains literature context.

## 23. Frame boundary and F06

G4 verified the primary scalar common-TEME path. Cross-frame vector/reference comparison and encounter-plane vector construction remain unauthorized.

`F06 — NOT APPLICABLE TO CURRENT PRIMARY G5 ANALYSIS` unless a later separately approved verification resolves the vector/frame problem. Do not manufacture RTN/RIC reference residuals.

## 24. Freeze condition

This document, the G5 allocation register, and the G6 candidate-reserve register become immutable when their SHA-256 values are recorded in `docs/research/G5_PreOutcome_Lock_v2_Hashes.txt`. A discovered error after hashing requires stopping and reporting it; no silent edit or rehash is permitted.
