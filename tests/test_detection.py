"""
Detection regression test.

Covers: planted risk exceptions in the main sample, the false-positive ceiling,
the cross-entry rules on the advanced demo, and the Accuracy/correctness rules on
the errors demo (including chart-of-accounts validation). Run:
    python -m pytest -q          (or run this file directly)
"""

import json
from pathlib import Path

import pandas as pd

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jereview import ColumnMapping, normalize, RuleContext, run  # noqa: E402

SAMPLE = ROOT / "sample_data" / "sample_je.csv"
KEY = ROOT / "sample_data" / "sample_answer_key.json"
ADVANCED = ROOT / "sample_data" / "sample_je_advanced.csv"
ERRORS = ROOT / "sample_data" / "sample_je_errors.csv"


def _fired(path, ctx=None):
    raw = pd.read_csv(path, dtype=str)
    df = normalize(raw, ColumnMapping.identity(list(raw.columns)))
    res = run(df, ctx or RuleContext(available=set()))
    fired = {je: set(ids.split(", ")) for je, ids in
             zip(res.entries["je_id"], res.entries["test_ids"])}
    return res, fired


def test_all_planted_rule_hits_detected():
    res, fired = _fired(SAMPLE)
    answer = json.loads(KEY.read_text())
    missed = [(je, rid) for je, exp in answer.items() for rid in exp
              if rid not in fired.get(je, set())]
    assert not missed, f"Missed planted detections: {missed}"


def test_every_planted_entry_flagged():
    res, fired = _fired(SAMPLE)
    assert set(json.loads(KEY.read_text())).issubset(set(res.entries["je_id"]))


def test_clean_false_positive_rate_under_ceiling():
    res, fired = _fired(SAMPLE)
    planted = set(json.loads(KEY.read_text()))
    flagged = set(res.entries["je_id"])
    clean_total = res.stats["total_entries"] - len(planted)
    rate = len(flagged - planted) / clean_total
    assert rate <= 0.05, f"Clean false-positive rate {rate:.1%} exceeds 5% ceiling"


def test_cross_entry_rules_fire_on_advanced_demo():
    res, fired = _fired(ADVANCED)
    expect = {"ADV-2024-0003": "DUPLICATE_ENTRY", "ADV-2024-0011": "DUPLICATE_ENTRY",
              "ADV-2024-0005": "QUICK_REVERSAL", "ADV-2024-0006": "QUICK_REVERSAL",
              "ADV-2024-0010": "NUMBERING_GAP", "ADV-2024-0013": "RARE_USER"}
    missed = [(je, rid) for je, rid in expect.items() if rid not in fired.get(je, set())]
    assert not missed, f"Advanced demo missed: {missed}"


def test_accuracy_rules_fire_on_errors_demo():
    res, fired = _fired(ERRORS)
    expect = {
        "ERR-0001": "SINGLE_SIDED_ENTRY",
        "ERR-0002": "DEBIT_AND_CREDIT_SAME_LINE",
        "ERR-0003": "BLANK_LINE_AMOUNT",
        "ERR-0004": "NEGATIVE_DR_CR",
        "ERR-0005": "MISSING_ACCOUNT",
        "ERR-0006": "ACCOUNT_NAME_INCONSISTENT",
        "ERR-0007": "ACCOUNT_NAME_INCONSISTENT",
        "ERR-0008": "PERIOD_DATE_MISMATCH",
        "ERR-0009": "EXCESS_PRECISION",
        "ERR-0010": "FUTURE_DATE",
    }
    missed = [(je, rid) for je, rid in expect.items() if rid not in fired.get(je, set())]
    assert not missed, f"Errors demo missed: {missed}"
    # clean entries should carry no accuracy flag
    acc_ids = {"SINGLE_SIDED_ENTRY", "DEBIT_AND_CREDIT_SAME_LINE", "BLANK_LINE_AMOUNT",
               "NEGATIVE_DR_CR", "MISSING_ACCOUNT", "ACCOUNT_NAME_INCONSISTENT",
               "PERIOD_DATE_MISMATCH", "EXCESS_PRECISION", "FUTURE_DATE", "UNBALANCED_ENTRY"}
    for clean in ("ERR-0011", "ERR-0012"):
        assert not (fired.get(clean, set()) & acc_ids), f"{clean} unexpectedly has an accuracy flag"


def test_unknown_account_requires_coa():
    # without a chart of accounts, UNKNOWN_ACCOUNT stays silent
    res, fired = _fired(ERRORS)
    assert not any("UNKNOWN_ACCOUNT" in s for s in fired.values())
    # with a chart of accounts that omits 7000, the 7000 postings are flagged
    ctx = RuleContext(available=set())
    ctx.known_accounts = {"6000", "2000"}
    res2, fired2 = _fired(ERRORS, ctx)
    assert "UNKNOWN_ACCOUNT" in fired2.get("ERR-0006", set())


def test_cross_entry_rules_quiet_on_clean_main_sample():
    res, fired = _fired(SAMPLE)
    for rid in ("QUICK_REVERSAL", "NUMBERING_GAP", "RARE_USER",
                "SINGLE_SIDED_ENTRY", "NEGATIVE_DR_CR", "FUTURE_DATE"):
        hits = [je for je, s in fired.items() if rid in s]
        assert not hits, f"{rid} should not fire on the clean sample, hit: {hits}"


if __name__ == "__main__":
    for fn in [test_all_planted_rule_hits_detected, test_every_planted_entry_flagged,
               test_clean_false_positive_rate_under_ceiling,
               test_cross_entry_rules_fire_on_advanced_demo,
               test_accuracy_rules_fire_on_errors_demo,
               test_unknown_account_requires_coa,
               test_cross_entry_rules_quiet_on_clean_main_sample]:
        fn(); print(f"PASS  {fn.__name__}")
    print("All detection tests passed.")
