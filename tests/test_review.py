"""Unit tests for the support-review data model (review.py)."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jereview.review import (
    ReviewRecord, SupportItem, ReviewMemory, entry_signature, build_scorecard,
    DISPOSITIONS, SUPPORT_STATUSES, ASSERTIONS, default_assertions,
)


def test_record_roundtrip_preserves_everything():
    rec = ReviewRecord(je_id="JE-1", signature="abc123")
    rec.disposition = "Needs correction"
    rec.support_status = "Reviewed - partial/discrepancy"
    rec.assertions["amount"] = "Discrepancy"
    rec.assertions["date"] = "Agrees"
    rec.support_items.append(SupportItem(label="inv_4471.pdf", support_type="Invoice",
                                         covers=["amount", "date"], agrees="Discrepancy",
                                         note="Invoice is $900, JE posted $950"))
    rec.correction = "Amount should be 900.00"
    rec.stamp(reviewer="s.shaw")
    back = ReviewRecord.from_dict(rec.to_dict())
    assert back.disposition == "Needs correction"
    assert back.support_status == "Reviewed - partial/discrepancy"
    assert back.assertions["amount"] == "Discrepancy"
    assert back.support_items[0].label == "inv_4471.pdf"
    assert back.support_items[0].covers == ["amount", "date"]
    assert back.discrepancies == ["amount"]
    assert back.reviewer == "s.shaw" and back.review_date


def test_from_dict_rejects_bad_values():
    rec = ReviewRecord.from_dict({"je_id": "X", "disposition": "Bogus",
                                  "support_status": "nope",
                                  "assertions": {"amount": "Maybe", "date": "Agrees"}})
    assert rec.disposition == "Open"               # invalid -> default
    assert rec.support_status == "Not requested"
    assert rec.assertions["amount"] == "Not tested"  # invalid outcome dropped
    assert rec.assertions["date"] == "Agrees"


def test_signature_is_stable_across_periods():
    s1 = entry_signature(["6000", "2000"], "Monthly rent - Acme Towers Nov", "ksmith", 12000)
    s2 = entry_signature(["2000", "6000"], "Monthly rent Acme Towers Dec", "ksmith", 11800)
    assert s1 == s2                                  # same recurring entry, diff period
    s3 = entry_signature(["6000", "2000"], "Consulting fee Globex", "ksmith", 12000)
    assert s3 != s1


def test_memory_roundtrip_and_recurrence_suggestion():
    mem = ReviewMemory()
    sig = entry_signature(["6000", "2000"], "Monthly rent Acme", "ksmith", 12000)
    rec = ReviewRecord(je_id="JE-NOV-rent", signature=sig)
    rec.disposition = "Expected business activity"
    rec.stamp("s.shaw")
    mem.records[rec.je_id] = rec
    folded = mem.fold_resolved_into_history(period="2024-11")
    assert folded == 1
    # serialise -> deserialise
    mem2 = ReviewMemory.from_json(mem.to_json())
    assert sig in mem2.history
    # a new period's identical entry gets a suggestion
    suggestion = mem2.suggest_disposition(sig)
    assert suggestion is not None
    disp, prov = suggestion
    assert disp == "Expected business activity"
    assert "2024-11" in prov
    # an unknown signature gets nothing
    assert mem2.suggest_disposition("unknown") is None


def test_scorecard_flags_noisy_vs_valuable_checks():
    records = {}
    rule_ids = {}
    # NOISY_RULE: surfaced 6, all benign
    for i in range(6):
        jid = f"N{i}"
        r = ReviewRecord(je_id=jid); r.disposition = "False positive"
        records[jid] = r; rule_ids[jid] = ["NOISY_RULE"]
    # GOOD_RULE: surfaced 4, 3 real corrections
    for i in range(4):
        jid = f"G{i}"
        r = ReviewRecord(je_id=jid)
        r.disposition = "Needs correction" if i < 3 else "Recorded correctly"
        records[jid] = r; rule_ids[jid] = ["GOOD_RULE"]
    sc = build_scorecard(records, rule_ids)
    assert sc["NOISY_RULE"]["noise_rate"] == 1.0
    assert "muting" in sc["NOISY_RULE"]["recommendation"]
    assert sc["GOOD_RULE"]["hit_rate"] == 0.75
    assert sc["GOOD_RULE"]["recommendation"] == "High-value check"


def test_vocabularies_present():
    assert DISPOSITIONS[0] == "Open" and "Recorded correctly" in DISPOSITIONS
    assert "No support required" in SUPPORT_STATUSES
    assert set(ASSERTIONS) == set(default_assertions())


if __name__ == "__main__":
    for fn in [test_record_roundtrip_preserves_everything, test_from_dict_rejects_bad_values,
               test_signature_is_stable_across_periods, test_memory_roundtrip_and_recurrence_suggestion,
               test_scorecard_flags_noisy_vs_valuable_checks, test_vocabularies_present]:
        fn(); print(f"PASS  {fn.__name__}")
    print("All review-model tests passed.")
