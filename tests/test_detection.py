"""
Detection regression test.

Every planted exception in the main sample must be caught by the rule it was
designed to trip, the controlled clean set stays below a false-positive ceiling,
and the cross-entry rules fire on the dedicated advanced demo. Run:
    python -m pytest -q          (or run this file directly with python)
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


def _fired(path):
    raw = pd.read_csv(path, dtype=str)
    df = normalize(raw, ColumnMapping.identity(list(raw.columns)))
    res = run(df, RuleContext(available=set()))
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
    answer = json.loads(KEY.read_text())
    assert set(answer).issubset(set(res.entries["je_id"]))


def test_clean_false_positive_rate_under_ceiling():
    res, fired = _fired(SAMPLE)
    planted = set(json.loads(KEY.read_text()))
    flagged = set(res.entries["je_id"])
    clean_total = res.stats["total_entries"] - len(planted)
    rate = len(flagged - planted) / clean_total
    assert rate <= 0.05, f"Clean false-positive rate {rate:.1%} exceeds 5% ceiling"


def test_cross_entry_rules_fire_on_advanced_demo():
    res, fired = _fired(ADVANCED)
    expect = {
        "ADV-2024-0003": "DUPLICATE_ENTRY",
        "ADV-2024-0011": "DUPLICATE_ENTRY",
        "ADV-2024-0005": "QUICK_REVERSAL",
        "ADV-2024-0006": "QUICK_REVERSAL",
        "ADV-2024-0010": "NUMBERING_GAP",
        "ADV-2024-0013": "RARE_USER",
    }
    missed = [(je, rid) for je, rid in expect.items() if rid not in fired.get(je, set())]
    assert not missed, f"Advanced demo missed: {missed}"


def test_cross_entry_rules_quiet_on_clean_main_sample():
    res, fired = _fired(SAMPLE)
    for rid in ("QUICK_REVERSAL", "NUMBERING_GAP", "RARE_USER"):
        hits = [je for je, s in fired.items() if rid in s]
        assert not hits, f"{rid} should not fire on the clean sample, hit: {hits}"


if __name__ == "__main__":
    for fn in [test_all_planted_rule_hits_detected, test_every_planted_entry_flagged,
               test_clean_false_positive_rate_under_ceiling,
               test_cross_entry_rules_fire_on_advanced_demo,
               test_cross_entry_rules_quiet_on_clean_main_sample]:
        fn(); print(f"PASS  {fn.__name__}")
    print("All detection tests passed.")
