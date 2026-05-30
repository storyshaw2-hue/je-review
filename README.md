# jereview — local journal-entry risk testing

A small, **local-only** tool that runs the standard AU-C 240 journal-entry risk
battery over a company's general-ledger export and produces a ranked exceptions
**workpaper** (Excel + CSV) for internal audit / controllership to triage.

- **Runs entirely on your machine.** No GL data leaves the building, no API
  calls, no account required. That is the point.
- **Maps to your export.** Point a small JSON mapping at your SAP / Oracle /
  NetSuite / QuickBooks column names; rules that need a field you don't have are
  skipped automatically (and noted).
- **Output is a workpaper, not a verdict.** Each flagged entry shows which tests
  fired and why, with a composite risk score for triage. These are *risk
  indicators for auditor judgement* — expect, and clear, false positives.

> Scope: this finds risky entries in **your own ledger**. It is not a benchmark
> that scores an AI model. (That is a different project.)

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Run

If your export already uses the canonical column names (the sample data does):

```bash
python -m jereview run --input sample_data/sample_je.csv --out exceptions.xlsx
```

With a real export whose columns differ, supply a mapping (see
`mapping.example.json`):

```bash
python -m jereview run --input ledger.csv --map my_mapping.json \
    --out exceptions.xlsx --threshold 50000
```

`--threshold` sets the approval limit used by the just-below-threshold test.
`.csv` and `.xlsx` inputs are both accepted.

## What it tests

| Test ID | Looks for | Needs |
|---|---|---|
| WEEKEND_POSTING | Entry recorded on a Sat/Sun | entry_date |
| AFTER_PERIOD_END | Posted after the period it affects (back-dating) | entry_date, period |
| PERIOD_END_CONCENTRATION | Posted in the last days of the month | entry_date |
| OFFHOURS_POSTING | Posted late night / early morning | posted_at |
| ROUND_AMOUNT | Exact multiple of 1,000 | amount |
| BELOW_THRESHOLD | Just under an approval limit | amount |
| LARGE_OUTLIER | Top-percentile amount | amount |
| SELDOM_ACCOUNT | Account used by only one entry in the period | account |
| SENSITIVE_ACCOUNT | Suspense / reserve / equity / revenue / IC accounts | account_name |
| MANUAL_SOURCE | Manually posted (vs system-generated) | source |
| SOD_CONFLICT | Same user prepared and approved | entered_by, approved_by |
| BLANK_DESCRIPTION | No narrative | description |
| SUSPICIOUS_KEYWORD | "plug", "to balance", "reclass", "per CFO", … | description |
| UNBALANCED_ENTRY | Debits ≠ credits within the entry | amount |

Plus a population-level **Benford** first-digit diagnostic (MAD + Nigrini
conformity band) reported on the Summary sheet.

The risk score for an entry is the sum of the weights of the tests it trips —
used only to rank items for triage. Tune thresholds, weights, and keyword lists
in `jereview/rules.py` (`RuleContext`).

## Input format

Canonical line-level schema (one row per debit/credit line), inspired by the
AICPA Audit Data Standard for journal entries. Required: `je_id`, `entry_date`,
`account`, and either a signed `amount` or both `debit`/`credit`. Everything
else is optional and improves coverage. See `jereview/schema.py`.

## Extending

Add a test by writing one function in `jereview/rules.py`:

```python
@rule("MY_TEST", "Human label", "entry", 2, ["amount"], "What it looks for.")
def r_my_test(df, ctx):
    return {je_id: "reason string", ...}   # one entry per flag
```

It's picked up automatically; the Summary and Test Catalog sheets update.

## Validation

`sample_data/` ships a synthetic, privacy-safe November-2024 population (155
entries) with **15 planted exceptions** and an answer key. `tests/test_detection.py`
turns that key into a regression check:

```bash
python -m pytest -q                       # all tests
python tests/test_detection.py            # detection recall / false-positive ceiling
python tests/test_ui_smoke.py             # drives the Streamlit UI headlessly (AppTest)
```

On the current build it detects 19/19 planted rule-hits, catches all 15 planted
entries, and flags 0 clean entries. **That 0% false-positive rate is a property
of the controlled synthetic set — a real ledger will produce more noise**, which
is expected for a first-pass screen.

## What this tool will NOT do

Be explicit about this before sending the workbook to a partner or client:

- **It will not conclude on fraud.** Hits are *risk indicators for auditor
  judgement* — every exception still needs corroboration, documentation, and
  professional skepticism.
- **It will not replace your JE testing workpaper.** It feeds it. Reasoning,
  selections rationale, and dispositions still come from the engagement team.
- **It will not test population completeness.** Reconcile your export to the
  trial balance / GL control totals *before* you run this — otherwise you're
  testing an incomplete file.
- **It will not validate your column mapping.** If you mis-map `posted_at` to
  `entry_date`, off-hours and back-dating tests will silently lie. Spot-check
  the first run against a known JE.
- **It will not phone home.** No telemetry, no API calls (unless you explicitly
  opt in to AI triage with your own key), no account. The browser build runs
  100% in-WASM; the CLI runs 100% on your machine.
- **It will not replace ASC 250 / ASC 740 / SOX judgement.** Risk weights and
  thresholds in `rules.py` are starting points, not GAAP.

## Limitations (read before relying on it)

- Outputs are leads to investigate, not conclusions. Document the disposition of
  each exception.
- Percentile, round-amount, and seldom-account tests are inherently noisy on
  real data; tune them to your population.
- It tests the entries you give it — it does not verify completeness of the
  population against the GL/TB. Reconcile your export to the trial balance first.
- No AI is involved by default. An optional LLM layer (e.g. narrative triage of
  the top exceptions) could sit on top, but should stay opt-in for privacy.

## Upload UI (for non-coders)

A local drag-and-drop interface for internal audit who don't want a command line:

```bash
streamlit run ui/streamlit_app.py
```

Drop in a `.csv`/`.xlsx` export, optionally a column-mapping JSON, set the
approval threshold, and download the workpaper. It still runs entirely on the
analyst's machine — Streamlit serves a local web page; no data is uploaded
anywhere.

## Triage notes

The Summary/CLI/UI can attach plain-English triage notes for the top exceptions.

- **Local** (default in the UI): notes are generated from the fired tests using a
  built-in suggested-procedure library. **No data leaves the machine.**
- **AI-enhanced** (opt-in): sends **only the flagged exception rows** — never the
  full ledger — to an OpenAI/Anthropic model for a more fluent write-up, and falls
  back to local notes if the call fails or no key is set.

CLI:

```bash
# local, no egress
python -m jereview run --input ledger.csv --triage local --out exceptions.xlsx

# opt-in AI (reads ANTHROPIC_API_KEY or OPENAI_API_KEY from the environment)
python -m jereview run --input ledger.csv --triage ai --provider anthropic \
    --model claude-sonnet-4-20250514 --triage-top 10 --out exceptions.xlsx
```

When triage is on, a **Triage** sheet is added to the workbook.

## Deploy as a website (privacy-preserving, in-browser)

The public tool runs **entirely in the visitor's browser** via
[stlite](https://github.com/whitphx/stlite) (Streamlit on WebAssembly). Uploaded
ledgers are processed client-side — nothing is sent to any server. AI triage is
disabled in this build (no network egress); local triage runs fully in-browser.

The whole site is a single self-contained file, `index.html`, at the repo root
(rebuild it with `python scripts/build_web.py` after any code change).

Host it free on **GitHub Pages**:
1. Push or upload this repo to GitHub (keep it **public**).
2. Repo **Settings → Pages → Build and deployment → Source: Deploy from a branch**.
3. Branch: **main**, folder: **/ (root)** → **Save**.
4. After ~1 minute your site is live at `https://<your-username>.github.io/<repo>/`.

Test locally first (must be served over http, not opened as file://):

```bash
python -m http.server 8000   # from the repo root → open http://localhost:8000
```

First load downloads a Python runtime into the browser (~30–60s), then it's
cached. `.github/workflows/tests.yml` runs the test suite on every push.
