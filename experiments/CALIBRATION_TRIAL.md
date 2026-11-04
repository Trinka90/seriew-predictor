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

## Executed historical results — 2026-11-04

Run: https://github.com/Trinka90/seriew-predictor/actions/runs/41928595835
Code commit: 28d335d0b54d9b214cf12c013f4cb7d2445f8774

Six tests passed locally and on GitHub. Both historical runs completed successfully. Each season has 760 test matches (Serie A + Premier League), 760 calibration matches, and no skipped matches/weeks. Reports are preserved in trial_results/.

| Test season | Base 1X2 LL | Candidate 1X2 LL | Market 1X2 LL | Base O/U LL | Candidate O/U LL |
|---|---:|---:|---:|---:|---:|
| 2024/25 | 0.961533 | 0.961052 | 0.961214 | 0.678768 | 0.680201 |
| 2025/26 | 0.998682 | 0.997952 | 0.997705 | 0.692429 | 0.694178 |

Lower is better. Candidate-minus-base paired daily-bootstrap 95% intervals:
- 2024/25: [-0.005072, 0.003994]
- 2025/26: [-0.005931, 0.004271]

Both include zero. The small 1X2 improvement is not established statistically. Over/Under worsens in both seasons. Real-odds selection ROI (restricted five-market selection, not the production recommendation): base -5.32% vs candidate -6.04% in 2024/25; base -9.98% vs candidate -6.25% in 2025/26. All remain negative.

Decision: DO NOT promote this candidate. Keep the evaluation correction and isolated experiment available for research. The run does not establish a reliable predictive or economic advantage, and does not test news effects.
