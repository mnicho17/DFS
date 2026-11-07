# Build explanation evidence map

Source inspection: November 7, 2026. RL-07B observational archive capture; no optimizer changes.

## Available evidence

- build_explanations.archive_receipt validates freshly captured archive bytes, the exact frozen snapshot revision, completed status and roster occurrences. It supports output membership, not a rejection cause or contest submission.
- build_explanations.local_check rechecks supported frozen salary, requirements, exclusions and groups. A local conflict does not prove production rejected that roster for this reason.
- ScreeningAudit.__init__ records generated, salary-eligible, retained and screening-input ordered indices, frozen players/context and input ID. Retained inputs have separate provenance. Absence from this bank does not prove search infeasibility.
- ScreeningAudit.screened records coarse rows/scalar metrics, shortlist and Captain/feasibility reservations. Complete scoring compares requested/completed scenario counts and coarse/input occurrence counters. It does not establish exhaustive generation, independent-validation completion or portfolio optimality.
- ScreeningAudit.save records selected indices, model version and a payload fingerprint. The original separate file can now be embedded in its generated archive and validated by the explanation capture; an unbound separate file alone remains insufficient.

The audit is constructed in showdown_simulation.run_deep_showdown after search, before coarse scoring. It preserves generated and retained origins separately. Limits are 100,000 candidates, 64 MiB decompressed and 16 MiB compressed. Capture/storage/cancellation failures return a not-saved receipt rather than publishing a truncated bank. The loader validates integrity, indices, several stage relationships and frozen roster identities. Integrity alone does not certify pregame timing or forecast quality.

## Missing evidence

Independent validation happens after shortlist construction but is not recorded per roster in this audit. Per-roster portfolio rejection causes, recovery/refinement decisions and exact solver replay are absent. Aggregate blocker counts cannot be assigned to an individual witness.

build_explanations.explain returns recorded membership only when the original embedded audit passes source-bound capture. Independent-validation and portfolio-selection rejection causes remain not_recorded. Classic and older builds do not acquire Showdown evidence by inference.

## Acceptance boundaries

1. Capture the referenced original audit bytes with the build occurrence. Verify receipt fingerprint, input identity, frozen player and Captain/FLEX identities, relevant context and selected output agreement. Matching input ID alone is insufficient: repeated builds can share inputs.
2. Preserve captured bytes/digest through source revalidation and reconciliation. Missing, altered, conflicting, unsupported or cancelled evidence stays unavailable. Never silently choose a convenient matching file.
3. Expose recorded membership and occurrence counts, distinguishing retained from generated inputs and partial from complete scoring. Absence alone remains insufficient to claim rejection.
4. Keep independent-validation and rejection causes unavailable until originally captured evidence supports them. Keep snapshot checks separate from observed pipeline decisions.
5. Test repeated-input/different-build audits, identity conflicts, changed bytes, missing/legacy audits, partial scoring, duplicates, cancellation and unchanged optimizer outputs.

This is observational work. Generation, ranking, ownership, SIM, saved-evidence qualification and historical/reconciliation safeguards remain outside the change.

## Archive capture implementation

New generated archives can include the original screening-audit.json.gz member. build_screening_evidence.capture reads the exact receipt filename once, validates those compressed bytes, requires the receipt fingerprint/byte count, validates the snapshot, checks frozen players/rules and salary/style/ownership/request context, and compares selected roster occurrence counters. The manifest checksums the captured member and metadata records its digest. There is no fallback file search or legacy evidence reconstruction. Cancelled, missing, malformed or conflicting optional evidence remains unavailable without preventing the generated-output archive.

Existing archive readers accept the optional checksummed member while keeping the established tuple API. The source-bound explanation reader now validates the member receipt, snapshot/context, chronology, whole-pool role IDs/salaries and selected occurrence counts, then exposes recorded stage membership. It checks cancellation during bounded decompression and roster adaptation; expanded bytes consume the same shared capture budget. The original archive is rehashed before a frozen capture is published. Public reports are aggregate; the private view includes per-witness counts. Stage absence is not rejection evidence, and coarse completion is not independent-validation or exhaustive-generation proof. Matching checksums establish consistency, not authenticity.
