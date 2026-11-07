# Preparation/ownership model compatibility

The existing model identity hashes runtime source ASTs, Python/platform identity and dependency versions, while full build identity separately qualifies SIM and saved evidence. The November 7 audit found 18 standalone benchmark scripts included in model identity despite no current production imports. Editing them could reject prepared work without changing a runtime calculation.

Policy 2 omits only the exact paths in AUDITED_BENCHMARKS when unreferenced by strict source. This is not a blanket scripts-directory or benchmark-prefix exemption: new/unknown modules stay strict. Static imports are followed transitively, including relative imports. Imported benchmark code is hashed again. Recognized literal dynamic imports are retained; ambiguous, relative dynamic or executable imports conservatively keep all audited benchmarks. Syntax errors in unused audited benchmarks are irrelevant; imported or strict-module syntax errors still reject qualification.

Presentation-only handling remains as before. Runtime workers/helpers, unknown nested modules, installed dependency versions, Python/platform inputs and actual imported benchmark code stay strict. Candidate loaders still validate slate membership, current rules, identities, salaries and roster legality. Full code_id behavior, scenario-cache integrity and historical-evidence qualification are unchanged. No scoring, ranking, ownership calculation, projection or generation behavior changes.

The policy change itself has a different source/policy identity and deliberately requires a new stamp once. It does not migrate old metadata, approve an older library, or relax a mismatched saved-model gate even when full source hashes match. Frozen builds use their embedded stamp; unidentified older builds keep the strict whole-build fallback. Only compatible future changes limited to the audited unused benchmarks avoid this particular preparation/ownership invalidation.

## Validation

Seven new synthetic tests cover exact allowlist versus unknown filenames; unused/imported syntax errors; dependency changes; static and relative transitive imports; literal and aliased dynamic imports; opaque/relative dynamic execution; and real library resume/load after a benchmark edit with subsequent runtime-change rejection.

Final focused acceptance: 65 tests in 13.296s, zero unexpected network attempts, including model/prefilter, CSV loading, player-table batching/tag refresh and task dashboard/attention checks. Existing tests preserve algorithm changes, frozen/legacy stamps, current-salary revalidation and same-source/different-model rejection.

A read-only source-tree audit found all 18 audited scripts present and zero required by strict production imports. Source identity took 3.555s in one local diagnostic, not a performance benchmark; packaged builds use their embedded identity. Full regression and Windows packaging remain gates for this follow-up. Original user history, libraries and backups were not edited. Track delivery and remaining work in ACTIVE_CHECKLIST.md.
