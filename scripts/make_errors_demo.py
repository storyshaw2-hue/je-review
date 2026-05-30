"""Generate sample_je_errors.csv — a small ledger of recording ERRORS so each
Accuracy/correctness check fires on a known entry. Accounts are reused so the
risk-side SELDOM_ACCOUNT test stays quiet and the accuracy errors stand out."""
import csv, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
COLS = ["je_id","line_no","entry_date","effective_date","period","account",
        "account_name","description","debit","credit","entered_by","approved_by","source"]
rows = []
def L(je,ln,date,acct,name,desc,dr,cr,user,eff=None,per="2024-11"):
    rows.append([je,ln,date,eff or date,per,acct,name,desc,dr,cr,user,"mgr_ortiz","System"])

# ERR-0001 single-sided (two debits, no credit)  [+ unbalanced, round]
L("ERR-0001",1,"2024-11-04","6000","Office Expense","Two debits no credit","1000.00","0.00","ksmith")
L("ERR-0001",2,"2024-11-04","6000","Office Expense","Two debits no credit","500.00","0.00","ksmith")
# ERR-0002 debit AND credit on one line  [+ unbalanced]
L("ERR-0002",1,"2024-11-05","6000","Office Expense","Both DR and CR on a line","800.00","800.00","rlee")
L("ERR-0002",2,"2024-11-05","2000","Trade Payables","Both DR and CR on a line","0.00","800.00","rlee")
# ERR-0003 blank line (no amount)
L("ERR-0003",1,"2024-11-06","6000","Office Expense","Has a blank line","1200.00","0.00","ksmith")
L("ERR-0003",2,"2024-11-06","2000","Trade Payables","Has a blank line","0.00","1200.00","ksmith")
L("ERR-0003",3,"2024-11-06","6000","Office Expense","Has a blank line","0.00","0.00","ksmith")
# ERR-0004 negative debit/credit
L("ERR-0004",1,"2024-11-07","6000","Office Expense","Negative posted","-500.00","0.00","rlee")
L("ERR-0004",2,"2024-11-07","2000","Trade Payables","Negative posted","0.00","-500.00","rlee")
# ERR-0005 missing account
L("ERR-0005",1,"2024-11-08","","","Missing account number","700.00","0.00","ksmith")
L("ERR-0005",2,"2024-11-08","2000","Trade Payables","Missing account number","0.00","700.00","ksmith")
# ERR-0006 / ERR-0007 account 7000 under two names
L("ERR-0006",1,"2024-11-11","7000","Bank","Account named Bank","900.00","0.00","rlee")
L("ERR-0006",2,"2024-11-11","2000","Trade Payables","Account named Bank","0.00","900.00","rlee")
L("ERR-0007",1,"2024-11-12","7000","Cash","Same account named Cash","950.00","0.00","ksmith")
L("ERR-0007",2,"2024-11-12","2000","Trade Payables","Same account named Cash","0.00","950.00","ksmith")
# ERR-0008 effective date outside the labelled period
L("ERR-0008",1,"2024-11-13","6000","Office Expense","Wrong period","300.00","0.00","rlee",eff="2024-10-15")
L("ERR-0008",2,"2024-11-13","2000","Trade Payables","Wrong period","0.00","300.00","rlee",eff="2024-10-15")
# ERR-0009 sub-cent precision
L("ERR-0009",1,"2024-11-14","6000","Office Expense","Sub-cent amount","100.12345","0.00","ksmith")
L("ERR-0009",2,"2024-11-14","2000","Trade Payables","Sub-cent amount","0.00","100.12345","ksmith")
# ERR-0010 future-dated
L("ERR-0010",1,"2027-03-01","6000","Office Expense","Future dated","400.00","0.00","rlee",per="2027-03")
L("ERR-0010",2,"2027-03-01","2000","Trade Payables","Future dated","0.00","400.00","rlee",per="2027-03")
# ERR-0011 / ERR-0012 clean
L("ERR-0011",1,"2024-11-15","6000","Office Expense","Clean entry","642.50","0.00","ksmith")
L("ERR-0011",2,"2024-11-15","2000","Trade Payables","Clean entry","0.00","642.50","ksmith")
L("ERR-0012",1,"2024-11-18","6000","Office Expense","Clean entry","318.75","0.00","rlee")
L("ERR-0012",2,"2024-11-18","2000","Trade Payables","Clean entry","0.00","318.75","rlee")

out = ROOT/"sample_data"/"sample_je_errors.csv"
with out.open("w",newline="") as f:
    w=csv.writer(f); w.writerow(COLS); w.writerows(rows)
# a tiny chart of accounts (omits 7000 so UNKNOWN_ACCOUNT can demo)
coa = ROOT/"sample_data"/"sample_coa.csv"
with coa.open("w",newline="") as f:
    w=csv.writer(f); w.writerow(["account","account_name"]); w.writerow(["6000","Office Expense"]); w.writerow(["2000","Trade Payables"])
print("wrote", out.name, "and", coa.name, "—", len({r[0] for r in rows}), "entries")
