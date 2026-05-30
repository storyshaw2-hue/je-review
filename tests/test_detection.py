"""
Detection regression test.

Turns the planted answer key into a check: every planted exception must be
caught by the rule it was designed to trip, and the controlled clean set must
stay below a false-positive ceiling. Run:  python -m pytest -q   (or run this
file directly with python).
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


def _result():
    raw = pd.read_csv(SAMPLE, dtype=str)
    df = normalize(raw, ColumnMapping.identity(list(raw.columns)))
    return run(df, RuleContext(available=set()))


def test_all_planted_rule_hits_detected():
    res = _result()
    answer = json.loads(KEY.read_text())
    fired = {je: set(ids.split(", ")) for je, ids in
             zip(res.entries["je_id"], res.entries["test_ids"])}
    missed = [(je, rid) for je, exp in answer.items() for rid in exp
              if rid not in fired.get(je, set())]
    assert not missed, f"Missed planted detections: {missed}"


def test_every_planted_entry_flagged():
    res = _result()
    answer = json.loads(KEY.read_text())
    flagged = set(res.entries["je_id"])
    assert set(answer).issubset(flagged)


def test_clean_false_positive_rate_under_ceiling():
    res = _result()
    answer = json.loads(KEY.read_text())
    planted = set(answer)
    flagged = set(res.entries["je_id"])
    clean_total = res.stats["total_entries"] - len(planted)
    clean_flagged = len(flagged - planted)
    rate = clean_flagged / clean_total
    assert rate <= 0.05, f"Clean false-positive rate {rate:.1%} exceeds 5% ceiling"


if __name__ == "__main__":
    for fn in [test_all_planted_rule_hits_detected,
               test_every_planted_entry_flagged,
               test_clean_false_positive_rate_under_ceiling]:
        fn()
        print(f"PASS  {fn.__name__}")
    print("All detection tests passed.")
