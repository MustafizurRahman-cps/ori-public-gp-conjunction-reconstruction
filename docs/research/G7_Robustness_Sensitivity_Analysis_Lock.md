# G7 Robustness / Sensitivity Analysis Lock

Status: **FROZEN BEFORE G7 ANALYSIS EXECUTION**

Parent G6 closure:

9b0d78ddddb0924771f3fceac34f8701162b5cf6

Parent tag:

g6-pass-with-constraints

## 1. Scientific role

G7 is a post-confirmatory robustness, sensitivity, baseline, and ablation stage.

The G6 protected outcomes have already been exposed and frozen. Therefore:

- G7 does not provide a second pristine confirmation on the G6 sample;
- G7 cannot rescue, replace, recalibrate, or redefine the frozen G6 result;
- any G6-outcome-informed analysis is explicitly post-confirmatory;
- the frozen G6 primary result remains authoritative for the frozen method.

## 2. Inherited G6 result

Frozen G6 primary result:

- events: 6,927
- evaluable: 6,906
- VALID_ASSOCIATED: 6,839
- fixed miss-distance radius: 78.75554652312711 km
- primary coverage: 5,609 / 6,839
- observed coverage: 0.8201491446117853
- Wilson 95%: [0.8108678292831654, 0.8290710069959210]
- interpretation:
  NOMINAL 90% COVERAGE NOT SUPPORTED — UNDERCOVERAGE

These quantities are not tunable in G7.

Inherited source hashes:

- T09: c41a4165a269891fc76a64884fb46d3d37c1c9f2fe0055618653eeb2a8fcc211
- G6 metric summary: 0b057d16ee25ce8beb6a729fc51d0aa330f980375bb23dcb7a5be51160dbd976
- G6 event metrics: e92553698deba0643e5c11ad48ddccab33c2f9a0fd9ce4298a6ee167860c0a79
- G3 baseline/ablation register: 0121bc6c6f0cd41f7abc0780d7402627121208c772032bc3dd1c5fea0f01c16b
- G3 parameter lock register: c2fd9823e8b1ee466ead268f2a9634497b3216cd65fa9fd48efb7dc464723f42
- G3 leakage specification: c6d9c613a52d336bfe3f863f3a3756d918d0294a85d7bca96472a2124da284d4

## 3. Analysis unit and denominator discipline

The event remains the primary analysis unit.

Failures are never assigned artificial distance errors.

Every robustness result must report:

1. applicable total support;
2. evaluable count/rate;
3. valid-associated count/rate;
4. conditional error support where applicable.

Conditional accuracy must never be presented without process/evaluability context.

## 4. Frozen G7 robustness families

### R1 — candidate-search horizon

Primary/frozen:

H = 5 d.

Sensitivity only:

H = 3 d
H = 7 d.

No additional horizon may be introduced after result inspection.

All other G4 numerical rules remain unchanged.

### R2 — association tolerance

Frozen primary:

delta_assoc = 5 min.

Descriptive robustness grid:

5, 15, 30, 60 min.

The 5-min G6 result remains authoritative.

Larger tolerances may quantify association sensitivity only and may not replace
the primary result.

### R3 — GP state-age sensitivity

Use only the frozen strata:

- <1 d
- 1–<2 d
- 2–<3 d
- 3–<5 d
- 5–<7 d
- >=7 d

No adaptive merging, threshold search, or outcome-driven regrouping.

Report support before error summaries.

### R4 — history-quality sensitivity

Retained causal descriptors:

- pair maximum state age;
- pair maximum publication age;
- pair minimum distinct-state count;
- pair maximum publication gap;
- pair maximum propagation span;
- publication/history count;
- left-censor status.

Primary quality window:

30 d.

Descriptive window sensitivity only:

14 d
60 d.

No scalar composite quality index is introduced.

### R5 — temporal robustness

Retain exactly:

G6-T1
G6-T2
G6-T3
G6-T4
G6-T5.

No new temporal bins may be selected using observed G6 errors.

The existing G6 temporal result is reused rather than recomputed unless a
specific G7 sensitivity requires a changed configuration.

### R6 — baselines and ablations

M1:
frozen pair-conditioned causal GP replay.

B0:
latest-causal-GP direct candidate geometry.

B1:
proximity-only output using the same reconstructed event.

B2:
frozen historical legacy ORI baseline only.

B2 may enter a quantitative like-for-like G7 table only if exact event/input
mapping is demonstrated without reconstructing undocumented assumptions.
Otherwise report historical-context-only status.

B4:
NOT RETAINED — FAIR REPRODUCTION NOT ESTABLISHED.

A0:
noncausal EPOCH-only leakage diagnostic; permanently nonconfirmatory.

A1:
causal latest-GP geometry.

A2:
sequential/history diagnostics.

A3:
explicit insufficient-information / association-status handling.

No new baseline family is introduced during G7.

## 5. Statistical reporting

G7 is primarily robustness/descriptive evidence.

Binary proportions:

- exact success / denominator;
- observed proportion;
- two-sided 95% Wilson interval.

Continuous absolute-error summaries where applicable:

- median;
- P90;
- P95;
- P99;
- maximum.

For prespecified quality associations:

- Spearman rho;
- bootstrap interval only where the frozen dependence/resampling design is
  scientifically valid;
- use seed 20260908 and 2,000 replicates where the inherited G5 procedure is
  reused.

No p-value fishing, multiple subgroup search, or threshold optimization.

## 6. Computational-feasibility evidence

Record, for each executed G7 configuration:

- event count;
- propagated event/object count if propagation is required;
- candidate count;
- failure/status counts;
- elapsed wall time;
- maximum memory only if readily available;
- output byte size.

Computational feasibility is descriptive; it is not a software benchmark.

## 7. Execution economy

Reuse frozen G6 intermediates whenever mathematically valid.

Preferred order:

1. zero-propagation analyses from frozen event/history outputs;
2. reassociation-only sensitivity from frozen candidate sets where possible;
3. new propagation only for the prespecified H=3 d / H=7 d sensitivity.

Do not repeat H=5 d G6 propagation merely to regenerate existing evidence.

Do not rerun acquisition.

Do not redownload TraCSS or GP_HISTORY.

## 8. Prohibited G7 adaptations

G7 must not:

- change L* = 48 h;
- recalibrate CAL-0;
- alter the 78.75554652312711 km frozen radius;
- alter the 7.105892 s frozen TCA radius;
- change G6 membership;
- replace failures;
- optimize thresholds against G6;
- create a new primary metric;
- redefine temporal strata after viewing errors;
- present a changed method as confirmed on G6;
- claim covariance accuracy or Pc;
- claim live catalog-screening precision/recall;
- make maneuver or operational-safety claims.

## 9. Publication assets

Candidate G7 assets:

- T10 — baseline comparison, only for scientifically fair executable baselines;
- T11 — ablation results;
- T12 — sensitivity/robustness summary;
- F11 — baseline/ablation comparison if it adds information beyond T10/T11;
- F12 — sensitivity/robustness figure if supported;
- F08/F10 may be reused or extended only where scientifically consistent with
  their existing definitions.

No asset is mandatory merely because an ID exists in the plan.

## 10. G7 interpretation rule

G7 asks whether the substantive conclusions are stable across the prespecified
reasonable assumptions and where fragility occurs.

A sensitivity result may strengthen or weaken interpretation, but may not
replace the frozen G6 confirmatory result.

Fragility must be disclosed rather than tuned away.

## 11. Gate criterion

G7 may pass when:

- the prespecified feasible robustness analyses are completed;
- the main conclusions are characterized across reasonable assumptions;
- any fragile component is explicitly disclosed or removed from later claims;
- all comparisons are fair and traceable;
- no post-confirmatory result is mislabeled confirmatory;
- publication assets are generated only where scientifically supported.

No G7 scientific execution is authorized by this document alone until the
design-freeze commit/tag is completed.