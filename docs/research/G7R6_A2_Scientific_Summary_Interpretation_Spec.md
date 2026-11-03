# G7 R6 A2 Scientific Summary / Interpretation Specification v1

Status: **PROPOSED — OFFLINE REVIEW REQUIRED BEFORE ANY SCIENTIFIC SUMMARY EXECUTION**

Parent execution/audit freeze:

`g7-r6-a2-primary-metric-execution-audited-frozen`

Parent commit:

`31a564acba63b9f99deba36ec967dde1d9a73acf`

## 1. Purpose and authorization boundary

This specification freezes how the already-computed primary A2 outputs may be
summarized and interpreted before any manuscript-facing A2 statistic is
calculated.

This stage does **not** calculate scientific summary values.

This stage does **not** authorize:

- orbital propagation or repropagation;
- candidate generation or refinement;
- reference reassociation;
- production-row rescue or reclassification;
- raw `GP_HISTORY` access;
- network access;
- CAL0/CAL-AGE refitting;
- secondary 14-day/60-day quality-window execution;
- A0 execution;
- threshold optimization;
- post-outcome subgroup creation;
- row-level bootstrap;
- operational warning, screening-recall, collision-probability, covariance,
  or maneuver-safety claims.

The A2 interpretation remains pair-conditioned causal reconstruction for
externally specified object pairs and reference events.

## 2. Frozen source semantics preserved

The scientific summary must preserve the frozen G3/G5/G7 semantics:

- event is the primary scientific unit;
- publication/update rows are nested within event;
- S05/S06 are stability metrics, not accuracy metrics;
- S07 is first reference-event reconstruction lead, not warning, discovery,
  screening, or operational alert lead;
- Q01-Q06 are information/propagation quality descriptors, not a scalar risk
  score or universal validity threshold;
- missing, insufficient, failed, and unassociated outcomes remain explicit;
- no numeric penalty is assigned to failed/unassociated states;
- no adaptive binning, outcome-guided regrouping, or threshold search is
  permitted.

## 3. Exact quantile convention

Every empirical quantile in the A2 scientific summary uses the inverse
empirical CDF convention:

For ordered observations `x_(1) <= ... <= x_(n)` and probability `p` in
`(0,1]`, the unweighted quantile is the smallest `x_(k)` whose empirical
cumulative probability is at least `p`.

For weighted distributions, sort by value, normalize nonnegative weights to
sum to one, and report the smallest observation whose cumulative normalized
weight is at least `p`.

No interpolation between adjacent observations is used.

Unless a metric-specific rule below states otherwise, continuous/count
distributions report:

- support count;
- minimum;
- P50 (median);
- P90;
- P95;
- P99;
- maximum.

## 4. Binary proportions

Every binary event-level proportion reports:

- numerator;
- denominator;
- observed proportion;
- two-sided 95% Wilson interval.

No normal/Wald interval is used.

## 5. S05 — TCA update-change stability

Input rows are the frozen S05/S06 change rows.

Eligibility for an S05 distribution:

- event status must be `VALID`;
- event must contain at least one frozen successive valid-associated change.

The full within-event S05 distribution is retained.

For any grouped S05 distribution, each eligible event receives equal total
weight. If event `e` contains `c_e` S05 changes, each change from that event
receives weight:

`1 / (N_events * c_e)`.

Therefore events with many publications do not dominate the grouped
distribution.

Primary overall S05 reporting:

- eligible event count;
- total change-row count;
- event-equal weighted minimum/P50/P90/P95/P99/maximum;
- 95% event-bootstrap interval for weighted P50;
- 95% event-bootstrap interval for weighted P90.

Bootstrap rule:

- resampling unit: event;
- retain every selected event's complete S05 change distribution;
- replicates: 2,000;
- seed: `20260908`;
- interval: percentile 2.5%/97.5% using the same inverse empirical-CDF
  quantile convention;
- no row-level resampling.

Temporal-stratum S05 reporting uses the frozen `g6_temporal_stratum` labels.
Every stratum reports event support first and then the same descriptive
event-equal distribution. Stratum summaries are descriptive; no stratum
winner/ranking or stratum-specific hypothesis test is authorized.

Interpretation:

Smaller absolute S05 indicates greater sequential TCA stability under the
frozen reconstruction process. It does **not** imply greater TCA accuracy.

## 6. S06 — miss-distance update-change stability

S06 uses exactly the same eligibility, event-equal weighting, quantile,
bootstrap, and temporal-stratum rules as S05.

Interpretation:

Smaller absolute S06 indicates greater sequential miss-distance stability
under the frozen reconstruction process. It does **not** imply greater
miss-distance accuracy.

S05 and S06 must never be combined into a new scalar stability score.

## 7. S07 — first reference-event reconstruction lead

S07 uses one frozen event-level row per event.

Primary status reporting uses the full event denominator:

- `VALID`;
- `NO PRE-TCA RECONSTRUCTION`.

Report exact counts, proportions, and two-sided 95% Wilson intervals.

For `VALID` events only, report the lead distribution in hours using:

- support;
- minimum;
- P50;
- P90;
- P95;
- P99;
- maximum.

Report a 95% event-bootstrap percentile interval for S07 P50 using:

- 2,000 event replicates;
- seed `20260908`;
- inverse empirical-CDF quantiles.

Frozen temporal-stratum summaries are descriptive and report support before
lead quantiles.

Interpretation:

Larger S07 means that the specified reference event was reconstructable from
an earlier public-information replay for the already supplied pair. It is not
warning lead, discovery lead, catalog-screening lead, alert lead, or maneuver
decision time.

## 8. Q01-Q06 object-event-update descriptive distributions

The complete local derived database contains the frozen object-event-update
rows.

Raw row counts may be reported for provenance, but raw rows are not treated
as independent inferential samples.

For Q01-Q05, grouped descriptive distributions use **event-equal weighting**:

- each event receives equal total weight;
- within an event, all of that event's object-event-update rows share the
  event's weight equally.

For Q06, the same rule is applied only to object-event-update rows with
`q06_status = VALID_ASSOCIATED`; support events and support rows are reported
explicitly.

No confidence interval is calculated from raw object-update rows.

Object-event-update distributions to retain:

- Q01 state age, hours;
- Q02 publication age, hours;
- Q03 30-day publication count;
- Q04 30-day distinct-state count;
- Q05 30-day median publication cadence, hours;
- Q05 30-day maximum publication gap, hours;
- Q06 propagation span, hours, only where valid-associated.

For Q05 cadence/gap, rows lacking enough within-window publication history for
the corresponding cadence statistic remain explicit `INSUFFICIENT_HISTORY`
support cases and are not zero-filled.

## 9. Prespecified pair-level quality descriptors

For each replay opportunity, derive only the previously frozen pair-level
descriptors:

1. pair maximum state age;
2. pair maximum publication age;
3. pair minimum distinct-state count;
4. pair maximum recent publication gap;
5. pair maximum propagation span, only for valid-associated replay
   opportunities.

Pair maximum recent publication gap is defined only when both object-level
maximum-gap values are defined. Otherwise that pair-replay descriptor is
`INSUFFICIENT_HISTORY`.

Pair maximum propagation span is defined only when both object Q06 values are
defined for the valid-associated replay.

No pair-level scalar quality score is authorized.

Q03 publication count and Q05 median cadence remain object-event-update
descriptors because no primary pair aggregation for those quantities was
previously frozen.

## 10. Pair-level quality distributions

For each prespecified pair descriptor, use event-equal weighting over replay
opportunities:

- each event receives equal total weight;
- within each event, its defined pair-replay values share the event weight
  equally.

Report support events, support replay opportunities, minimum/P50/P90/P95/P99/
maximum.

These are descriptive distributions only. No universal threshold is inferred.

## 11. Prespecified quality–stability association analysis

The only new A2 association analysis authorized by this specification uses
the same transparent quality-descriptor family already retained for
quality-usefulness analysis:

- pair maximum state age;
- pair minimum distinct-state count;
- pair maximum recent publication gap;
- pair maximum propagation span.

Publication age, Q03 publication count, and median cadence are reported
descriptively but are not added to the primary quality–stability association
family, avoiding an unfrozen expansion of the association set.

For each event with at least one S05/S06 change:

1. define event S05 stability as the within-event P50 absolute S05 change;
2. define event S06 stability as the within-event P50 absolute S06 change;
3. for every frozen change row, use the **current** replay endpoint;
4. at that current replay, form each available frozen pair-level quality
   descriptor;
5. for each descriptor separately, define the event quality value as the
   within-event P50 across the change endpoints where that descriptor is
   defined.

For each of S05 and S06, compute Spearman rank correlation with each of the
four event-level quality descriptors.

Thus the prespecified family contains exactly eight correlations.

Uncertainty rule:

- bootstrap unit: event;
- replicates: 2,000;
- seed: `20260908`;
- retain the paired event-level stability/quality values;
- two-sided 95% percentile bootstrap interval;
- report exact event support for every correlation.

No p-values, significance-star notation, multiple-testing discovery claim,
causal claim, threshold derivation, or predictor-ranking claim is authorized.

Interpretation is descriptive/noncausal only.

## 12. Temporal-stratum reporting

The frozen five G6 temporal strata may be used for descriptive S05, S06, and
S07 summaries.

No new temporal cut points are allowed.

For quality distributions and quality–stability associations, the primary
summary is pooled across the frozen A2 event set with event-equal weighting.
Temporal-stratum quality associations are not part of the primary A2 summary
and require a separate explicit authorization if later judged necessary.

## 13. Missingness and support

Every table must report support before conditional numerical summaries.

Required explicit states include:

- `INSUFFICIENT_SUCCESSIVE_VALID_ASSOCIATED_RECONSTRUCTIONS`;
- `NO PRE-TCA RECONSTRUCTION`;
- Q05 `INSUFFICIENT_HISTORY` where applicable;
- Q06 `NOT_APPLICABLE_NON_ASSOCIATED`.

No missing value is converted to zero.

No event lacking a required metric is silently inserted into a conditional
distribution.

## 14. Manuscript-facing interpretation boundaries

Permitted wording categories after later summary execution and audit:

- sequential reconstruction stability;
- first reference-event reconstruction lead;
- public-GP information age/history descriptors;
- descriptive association between quality descriptors and stability;
- explicit data/status support and missingness.

Prohibited wording categories:

- collision probability or operational collision risk;
- covariance realism from A2 sequence variability;
- maneuver safety or maneuver recommendation;
- warning/discovery/screening performance;
- false-alert or catalog-discovery performance;
- operational alert lead;
- evidence that stability equals accuracy;
- universal GP-age/history validity threshold;
- causal effect of quality descriptors on reconstruction behavior;
- superiority of A2 over A1 based solely on these sequence diagnostics.

A1 remains geometry-equivalent/redundant under the frozen R6 framework; A2
adds sequence/history characterization rather than a new geometry engine.

## 15. Planned scientific-summary outputs

A later separately authorized summary executor may produce only:

1. `G7R6_A2_Scientific_Summary.json`
2. `G7R6_A2_S05_S06_EventEqual_Summary.csv`
3. `G7R6_A2_S07_Summary.csv`
4. `G7R6_A2_Quality_Descriptor_Summary.csv`
5. `G7R6_A2_Quality_Stability_Associations.csv`
6. an execution-audit JSON;
7. an independent-audit JSON.

No figure is generated before the summary values are independently audited.

## 16. Current authorization state

Scientific summary values computed:

**NO**

Scientific interpretation performed:

**NO**

Secondary 14-day/60-day execution performed:

**NO**

A0 execution performed:

**NO**

Next safe action after this specification passes independent offline review:

**FREEZE THE A2 SCIENTIFIC-SUMMARY / INTERPRETATION SPECIFICATION BEFORE
SUMMARY EXECUTION**
