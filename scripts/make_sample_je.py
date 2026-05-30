"""
make_sample_je.py — Generate a synthetic, privacy-safe journal-entry population
for November 2024 with planted exceptions, plus an answer key.

Most entries are "clean" (system-generated, balanced, business hours, weekday,
benign accounts/descriptions, preparer != approver). A set of entries are
planted to trip specific rules. The answer key records, per entry, which rule
IDs it was designed to trigger — used to validate detection recall.

Outputs (into sample_data/):
    sample_je.csv            canonical-named line-level export
    sample_answer_key.json   {je_id: [expected rule_ids], ...}
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pandas as pd

SEED = 42
PERIOD = "2024-11"
OUT = Path(__file__).resolve().parent.parent / "sample_data"

# Normal (non-sensitive) accounts, reused often so they are NOT "seldom".
NORMAL_ACCOUNTS = {
    "5010": "COGS - Materials",
    "5020": "Freight Expense",
    "6010": "Wages Expense",
    "6110": "Office Rent Expense",
    "6120": "Utilities Expense",
    "6310": "Office Supplies Expense",
    "2010": "Trade Payables",
    "1010": "Cash - Operating",
    "1110": "Trade Receivables",
    "1310": "Inventory - Materials",
}
PREPARERS = ["jdoe", "asmith", "bwong", "rkhan", "lmartin"]
APPROVERS = ["mgr_chen", "mgr_diaz", "mgr_olsen"]
BENIGN_DESC = [
    "Monthly accrual per schedule", "Vendor invoice posting", "Payroll allocation",
    "Inventory receipt", "Customer billing", "Utility payment", "Rent payment",
    "Freight charge allocation", "Supplies purchase", "Bank fee posting",
]
WEEKDAYS_NOV24 = [d for d in pd.date_range("2024-11-01", "2024-11-27")  # avoid last 3 days
                  if d.dayofweek < 5]


def _clean_amount(rng: random.Random) -> float:
    """Non-round, outside the below-threshold band, below outlier range."""
    while True:
        v = round(rng.uniform(200, 42000), 2)
        if v % 1000 == 0:          # not round-thousand
            continue
        if 47500 <= v < 50000:     # not in below-threshold band
            continue
        return v


def build():
    rng = random.Random(SEED)
    rows = []
    answer: dict[str, list[str]] = {}
    je_counter = 1

    def new_id():
        nonlocal je_counter
        jid = f"JE-2024-{je_counter:04d}"
        je_counter += 1
        return jid

    def clean_entry(n_clean_extra_ok=True):
        jid = new_id()
        d = rng.choice(WEEKDAYS_NOV24)
        posted = d.replace(hour=rng.randint(9, 17), minute=rng.randint(0, 59))
        amt = _clean_amount(rng)
        prep = rng.choice(PREPARERS)
        appr = rng.choice(APPROVERS)
        desc = rng.choice(BENIGN_DESC)
        da, ca = rng.sample(list(NORMAL_ACCOUNTS), 2)
        common = dict(je_id=jid, entry_date=d.date().isoformat(),
                      effective_date=d.date().isoformat(), period=PERIOD,
                      posted_at=posted.isoformat(sep=" "),
                      entered_by=prep, approved_by=appr, source="System",
                      description=desc)
        rows.append({**common, "line_no": 1, "account": da,
                     "account_name": NORMAL_ACCOUNTS[da], "debit": amt, "credit": 0})
        rows.append({**common, "line_no": 2, "account": ca,
                     "account_name": NORMAL_ACCOUNTS[ca], "debit": 0, "credit": amt})
        return jid

    # ---- 140 clean entries ----
    for _ in range(140):
        clean_entry()

    # ---- planted exceptions ----
    def planted(expected, *, date=None, eff=None, posted=None, prep=None, appr=None,
                source="System", desc="Vendor invoice posting",
                da="6310", ca="2010", da_name=None, ca_name=None,
                debit=12345.67, credit=None, balance=True):
        jid = new_id()
        d = pd.Timestamp(date) if date else rng.choice(WEEKDAYS_NOV24)
        eff_d = pd.Timestamp(eff) if eff else d
        if posted is None:
            posted = d.replace(hour=rng.randint(9, 17), minute=rng.randint(0, 59))
        else:
            posted = pd.Timestamp(posted)
        credit = debit if (balance and credit is None) else (credit or 0)
        common = dict(je_id=jid, entry_date=pd.Timestamp(d).date().isoformat(),
                      effective_date=pd.Timestamp(eff_d).date().isoformat(),
                      period=pd.Timestamp(eff_d).to_period("M").strftime("%Y-%m"),
                      posted_at=pd.Timestamp(posted).isoformat(sep=" "),
                      entered_by=prep or rng.choice(PREPARERS),
                      approved_by=appr or rng.choice(APPROVERS),
                      source=source, description=desc)
        rows.append({**common, "line_no": 1, "account": da,
                     "account_name": da_name or NORMAL_ACCOUNTS.get(da, da), "debit": debit, "credit": 0})
        rows.append({**common, "line_no": 2, "account": ca,
                     "account_name": ca_name or NORMAL_ACCOUNTS.get(ca, ca), "debit": 0, "credit": credit})
        answer[jid] = expected
        return jid

    # 1. Weekend posting (Nov 23 2024 = Saturday)
    planted(["WEEKEND_POSTING"], date="2024-11-23")
    # 2. After period end (Nov transaction posted Dec 4)
    planted(["AFTER_PERIOD_END"], date="2024-12-04", eff="2024-11-30")
    # 3. Period-end concentration (Nov 29)
    planted(["PERIOD_END_CONCENTRATION"], date="2024-11-29")
    # 4. Off-hours posting (23:40)
    planted(["OFFHOURS_POSTING"], date="2024-11-14", posted="2024-11-14 23:40:00")
    # 5. Round-dollar amount
    planted(["ROUND_AMOUNT"], debit=25000.00)
    # 6. Just below approval threshold ($49,200 < $50,000)
    planted(["BELOW_THRESHOLD"], debit=49200.00)
    # 7. Large outlier
    planted(["LARGE_OUTLIER"], debit=812450.55)
    # 8. Seldom-used account (unique account number)
    planted(["SELDOM_ACCOUNT"], da="9999", da_name="Other Clearing 9999")
    # 9. Sensitive account (suspense)
    planted(["SENSITIVE_ACCOUNT"], da="2900", da_name="Suspense Account")
    # 10. Manual entry
    planted(["MANUAL_SOURCE"], source="Manual")
    # 11. Preparer = approver (SoD)
    planted(["SOD_CONFLICT"], prep="jdoe", appr="jdoe")
    # 12. Blank description
    planted(["BLANK_DESCRIPTION"], desc="")
    # 13. Suspicious keyword
    planted(["SUSPICIOUS_KEYWORD"], desc="Manual reclass to balance the GL plug")
    # 14. Unbalanced entry (one-sided)
    planted(["UNBALANCED_ENTRY"], debit=15000.00, credit=9000.00, balance=False)
    # 15. Composite high-risk (several at once) — realistic fraud-pattern entry
    planted(["WEEKEND_POSTING", "MANUAL_SOURCE", "SOD_CONFLICT", "SENSITIVE_ACCOUNT",
             "SUSPICIOUS_KEYWORD"],
            date="2024-11-30", source="Manual", prep="bwong", appr="bwong",
            da="2900", da_name="Suspense Account",
            desc="Per CFO - true up reserve, reverse later", debit=48750.00)

    df = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "sample_je.csv", index=False)
    (OUT / "sample_answer_key.json").write_text(json.dumps(answer, indent=2), encoding="utf-8")
    print(f"Wrote {len(df)} lines across {df['je_id'].nunique()} entries → sample_data/sample_je.csv")
    print(f"Planted {len(answer)} exception entries → sample_data/sample_answer_key.json")


if __name__ == "__main__":
    build()
