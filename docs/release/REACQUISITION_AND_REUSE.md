# Source reacquisition and reproducibility boundaries

## Frozen source distinction

This repository provides code and permitted derived outputs, but not the multi-gigabyte local source history originally used for the 2026 study. The approved frozen R1 release excludes original GP_HISTORY downloads and historical SQLite work states. Do not claim that all source records can be reacquired without provider accounts or that dynamic re-queries necessarily return identical historical publications.

- **Space-Track GP/GP_HISTORY:** authenticated access, original user agreement, and original release/redistribution terms apply. `CREATION_DATE` is the historical-availability filter; `EPOCH` is not an access timestamp. The historical program held raw histories locally and does not grant a blanket right to redistribute them.
- **NOAA TraCSS verification dataset:** the recorded G2 register classified the official source release as CC0. Verify the relevant provider release/version and metadata for any independent download; acknowledge NOAA/TraCSS and the exact data version. The current repository does not reproduce or host raw source archives.
- **Derived results:** check both author ownership and any upstream restrictions before extending or republishing derived rows. Source object identifiers, reference values, and generated geometry appear in some frozen results; their inclusion is not a license for unbounded rehosting of the upstream raw orbital data.

## Computational integrity of the public release

All original scientific scripts in this publication package are copied unchanged from an
integrity-verified sanitized source candidate. The public manifest covers only files in this
curated release. The original frozen R1 manifest, nonpublic supporting registers, local
source snapshots and full clean-replay workspace remain with the authors. The included
Mode A comparison JSON is author-side post-compute evidence, not independent replication.

Full historical replay is **not one-command runnable from these public files alone**:
some historical control/input registers and source snapshots are excluded. They must be
supplied from legitimately accessed, provenance-consistent sources before attempting the
complete scientific pipeline. Aggregate results can be inspected but do not replace full
event-level inputs for independent statistical recomputation.
