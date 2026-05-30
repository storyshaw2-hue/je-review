"""Generate sample_je_advanced.csv — a small ledger that demonstrates the
cross-entry rules (duplicate, quick reversal, numbering gap, rare user) while
keeping other tests quiet, so each new detection is easy to see."""
import csv, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
COLS = ["je_id","line_no","entry_date","effective_date","period","account",
        "account_name","description","debit","credit","entered_by","approved_by","source"]

# (je_id, date, acct, acct_name, desc, debit_acct_amount, user)  -> 2 balanced lines vs 2000 Trade Payables
# For reversals we hand-craft the two lines explicitly.
def pair(jeid, date, acct, name, desc, amt, user, approver="mgr_lee"):
    return [
        [jeid,1,date,date,"2024-11",acct,name,desc,f"{amt:.2f}","0.00",user,approver,"System"],
        [jeid,2,date,date,"2024-11","2000","Trade Payables",desc,"0.00",f"{amt:.2f}",user,approver,"System"],
    ]

rows = []
rows += pair("ADV-2024-0001","2024-11-04","6100","Consulting Expense","Consultant invoice",4200.50,"arivera")
rows += pair("ADV-2024-0002","2024-11-05","6200","Marketing Expense","Ad spend",3120.00,"bchen")
rows += pair("ADV-2024-0003","2024-11-06","6300","Software Expense","SaaS renewal",7777.25,"arivera")   # DUP A
rows += pair("ADV-2024-0004","2024-11-07","6100","Consulting Expense","Consultant invoice",2510.75,"bchen")
# quick reversal pair (0005 booked, 0006 backs it out 3 days later)
rows += [["ADV-2024-0005",1,"2024-11-08","2024-11-08","2024-11","7000","Accrued Expense","Accrue bonus","8150.00","0.00","arivera","mgr_lee","System"],
         ["ADV-2024-0005",2,"2024-11-08","2024-11-08","2024-11","2000","Trade Payables","Accrue bonus","0.00","8150.00","arivera","mgr_lee","System"]]
rows += [["ADV-2024-0006",1,"2024-11-11","2024-11-11","2024-11","2000","Trade Payables","Reverse bonus accrual","8150.00","0.00","arivera","mgr_lee","System"],
         ["ADV-2024-0006",2,"2024-11-11","2024-11-11","2024-11","7000","Accrued Expense","Reverse bonus accrual","0.00","8150.00","arivera","mgr_lee","System"]]
rows += pair("ADV-2024-0007","2024-11-12","6200","Marketing Expense","Ad spend",1999.99,"bchen")
# (gap: 0008 and 0009 intentionally missing)
rows += pair("ADV-2024-0010","2024-11-13","6100","Consulting Expense","Consultant invoice",3400.00,"bchen")  # AFTER GAP
rows += pair("ADV-2024-0011","2024-11-14","6300","Software Expense","SaaS renewal",7777.25,"bchen")   # DUP B (== 0003)
rows += pair("ADV-2024-0012","2024-11-15","6200","Marketing Expense","Ad spend",5300.25,"arivera")
rows += pair("ADV-2024-0013","2024-11-18","6200","Marketing Expense","Ad spend",4250.00,"tempx")      # RARE user
rows += pair("ADV-2024-0014","2024-11-19","6100","Consulting Expense","Consultant invoice",2755.00,"arivera")

out = ROOT / "sample_data" / "sample_je_advanced.csv"
with out.open("w", newline="") as f:
    w = csv.writer(f); w.writerow(COLS); w.writerows(rows)
print("wrote", out, "with", len({r[0] for r in rows}), "entries /", len(rows), "lines")
