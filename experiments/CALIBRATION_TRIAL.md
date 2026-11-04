# Isolated calibration experiment

This branch does not change engine predictions, app data or deployment. The branch-specific workflow has read-only repository permissions and uploads reports as Actions artifacts. It downloads historical results for Serie A and Premier League only; no news/AI API calls or deployment occur.

## Changes

- Fix the legacy Arena evaluator so metrics receive the same recalibrated markets used to select predictions. Its independent market calibration is still mathematically unconstrained; its 1X2 metric normalizes probabilities. The report now documents this limitation and the use of estimated odds.
- Add a distinct coherent candidate: three regularized adjustments to a joint score distribution (home wins, draws, total goals). All resulting markets share that distribution.
- Compare raw de-margined market probabilities, statistical model, 90% market blend and calibrated blend on identical matches with complete 1X2/O-U2.5 market odds.
- Fit calibration on the earlier season only. Statistical ratings are refitted weekly using dates strictly before that week. Refuse overlapping calibration/evaluation dates.
- Report actual-odds selection coverage and ROI, with no estimated payouts. Selection is restricted to five supported markets and differs from the production app's broad-market selection.
- Report paired daily-bootstrap intervals for the candidate's 1X2 LogLoss difference versus base and market. Negative differences favour the candidate; intervals crossing zero do not establish improvement.

## Run

Requires an existing SQLite history with the repository's `matches` schema:

```sh
python -m unittest discover -s experiments/tests -v
python experiments/calibration_trial.py --db /path/to/history.db --tune 2425 --test 2526 --out /path/to/trial-report.json
```

Database opens read-only. The experiment refuses output paths in docs/ or the input/production database. The workflow uses a temporary trial.db and never commits generated files.

## Validation and interpretation

Six local tests passed: score/market coherence, identity transform, bias recovery on independent synthetic samples, temporal overlap rejection, actual-odds filtering and metric response, full evaluator training isolation, and the legacy metric regression (some checks grouped into one test).

Synthetic tests validate software, not betting accuracy. Existing saved reports are not results of this new variant. No improvement is asserted before inspecting new historical runs. The branch workflow runs 2023/24 -> 2024/25 and 2024/25 -> 2025/26; these historical periods have already informed previous project decisions and are not a pristine final holdout. Confirm any selected approach on prospectively frozen forecasts before promotion.

Limits: historical news/absence snapshots are unavailable; odds execution timestamps are not verified; day bootstrap cannot remove every cross-day dependence; the joint score model truncates scores at 10 and reports excluded games. The 90% blend baseline excludes production news/manual/motivation adjustments. Both baselines and candidate use only matches with complete market quotes. Missing-source coverage and skipped weeks must be inspected before interpreting reports.
