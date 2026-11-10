# Frozen predictions

Each file here is a model's predictions for one offseason, **frozen before the outcomes were public**. Files are never edited after freezing; each is tagged `freeze/<year>-<version>`, and its sha256 is recorded in the `.meta.json` and the tag message.

## How freezes work
- `uv run rift-rumors freeze` refuses to run with uncommitted code, and refuses to overwrite an existing freeze.
- Every freeze records its as-of date, the exact code commit, and when each data source was last updated (e.g. per-region GCD tab stamps).
- Players whose move was already recorded on the as-of date are included but marked `scored=false`. Predicting a public move proves nothing.
- **Later versions don't replace earlier ones.** v1 gets its own file and is scored only on players still unresolved at *its* freeze time. Comparing v0 and v1 on the same players then measures what the new signals (stats, rumors) add.

## Grading
Scored rows are graded against `offseason_outcomes` once Feb 1 of the next year passes, with the same label rules as the backtest: stayed / moved / no_team. Metrics are Brier and leave-Brier (lower is better), log loss, and calibration.

| Freeze | As of | Players scored | Backtest leave-Brier (base rate 0.256) | Live result |
|---|---|---|---|---|
| [`2026/v0-contract-only`](2026/v0-contract-only.csv) | 2026-11-10 | 121 | 0.218 | pending (Feb 1, 2027) |
