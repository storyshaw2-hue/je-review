"""
report.py — Write the exceptions workpaper.

Excel workbook with three sheets:
  Summary     — run metadata, per-test counts, Benford diagnostic, disclaimer
  Exceptions  — one row per flagged entry, ranked by risk score (auto-filter,
                frozen header, colour scale on score)
  Test Catalog— every rule, its weight, what it needs, whether it ran

Also writes a flat CSV of the Exceptions sheet for downstream analysis.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.utils import get_column_letter

from .engine import RunResult

FONT = "Arial"
HEAD_FILL = PatternFill("solid", fgColor="1F3864")
HEAD_FONT = Font(name=FONT, bold=True, color="FFFFFF", size=11)
TITLE_FONT = Font(name=FONT, bold=True, size=14, color="1F3864")
NOTE_FONT = Font(name=FONT, italic=True, size=9, color="595959")
BASE_FONT = Font(name=FONT, size=10)


def _style_header(ws, row, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)


def _autofit(ws, df, start_row, widths=None):
    for i, col in enumerate(df.columns, start=1):
        if widths and col in widths:
            w = widths[col]
        else:
            sample = df[col].astype(str)
            w = min(max(len(str(col)) + 2, sample.map(len).max() + 2 if len(sample) else 10), 60)
        ws.column_dimensions[get_column_letter(i)].width = w


def write_excel(result: RunResult, out_path, source_name: str = "",
                triage_md: str | None = None):
    target = out_path if hasattr(out_path, "write") else Path(out_path)
    wb = Workbook()

    # ---------------- Summary ----------------
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Journal Entry Risk Review"
    ws["A1"].font = TITLE_FONT
    meta = [
        ("Source file", source_name or "(in-memory)"),
        ("Run timestamp", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Period(s)", result.stats["periods"]),
        ("Total entries tested", result.stats["total_entries"]),
        ("Total lines tested", result.stats["total_lines"]),
        ("Entries flagged", result.stats["flagged_entries"]),
        ("Share of entries flagged", f"{result.stats['flagged_pct']}%"),
    ]
    r = 3
    for k, v in meta:
        ws.cell(row=r, column=1, value=k).font = Font(name=FONT, bold=True, size=10)
        ws.cell(row=r, column=2, value=v).font = BASE_FONT
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="Test results").font = Font(name=FONT, bold=True, size=12, color="1F3864")
    r += 1
    hdr = ["Test", "Weight", "Ran?", "Entries flagged", "What it needs"]
    for i, h in enumerate(hdr, start=1):
        ws.cell(row=r, column=i, value=h)
    _style_header(ws, r, len(hdr))
    head_row = r
    r += 1
    rs = result.rule_summary
    for _, row in rs.iterrows():
        ws.cell(row=r, column=1, value=row["label"]).font = BASE_FONT
        ws.cell(row=r, column=2, value=int(row["weight"])).font = BASE_FONT
        ws.cell(row=r, column=3, value="Yes" if row["ran"] else "No — field missing").font = BASE_FONT
        # live formula counting this test's ID on the Exceptions sheet
        ws.cell(row=r, column=4,
                value=f'=COUNTIF(Exceptions!E:E,"*"&"{row["rule_id"]}"&"*")').font = BASE_FONT
        ws.cell(row=r, column=5, value=row["requires"]).font = BASE_FONT
        r += 1

    r += 1
    b = result.benford
    ws.cell(row=r, column=1, value="Benford first-digit diagnostic (population)").font = Font(name=FONT, bold=True, size=11, color="1F3864")
    r += 1
    if b.get("mad") is None:
        ws.cell(row=r, column=1, value=b["verdict"]).font = BASE_FONT
        r += 1
    else:
        ws.cell(row=r, column=1, value=f"Mean abs. deviation (MAD): {b['mad']}  →  {b['verdict']}").font = BASE_FONT
        r += 1
        ws.cell(row=r, column=1, value=f"Based on {b['n']} line amounts ≥ 1. MAD bands per Nigrini: <0.006 close, <0.012 acceptable, <0.015 marginal, else investigate.").font = NOTE_FONT
        r += 1

    r += 2
    disclaimer = ("These are RISK INDICATORS for auditor judgement, not findings of error or fraud. "
                  "Expect false positives — percentile and round-amount tests in particular flag normal "
                  "items. Investigate flagged entries; clear or document each. Tool runs entirely locally; "
                  "no ledger data leaves this machine.")
    ws.cell(row=r, column=1, value=disclaimer).font = NOTE_FONT
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
    ws.cell(row=r, column=1).alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[r].height = 60
    for col, w in {"A": 30, "B": 9, "C": 18, "D": 16, "E": 40}.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"

    # ---------------- Exceptions ----------------
    we = wb.create_sheet("Exceptions")
    cols = ["je_id", "risk_score", "tests_fired", "test_ids", "reasons",
            "entry_date", "period", "lines", "total_debit", "total_credit",
            "net_amount", "entered_by", "approved_by", "source", "description"]
    nice = ["JE ID", "Risk Score", "# Tests", "Test IDs", "Why Flagged",
            "Entry Date", "Period", "Lines", "Total Debit", "Total Credit",
            "Net", "Preparer", "Approver", "Source", "Description"]
    # NOTE: keep "Test IDs" at column E so the Summary COUNTIF(E:E,...) works.
    df = result.entries.copy()
    if df.empty:
        df = pd.DataFrame(columns=cols)
    df = df[cols]
    # reorder so test_ids lands in column E (index 5): je_id,risk_score,tests_fired,reasons,test_ids,...
    order = ["je_id", "risk_score", "tests_fired", "reasons", "test_ids",
             "entry_date", "period", "lines", "total_debit", "total_credit",
             "net_amount", "entered_by", "approved_by", "source", "description"]
    nice = ["JE ID", "Risk Score", "# Tests", "Why Flagged", "Test IDs",
            "Entry Date", "Period", "Lines", "Total Debit", "Total Credit",
            "Net", "Preparer", "Approver", "Source", "Description"]
    df = df[order]
    we.append(nice)
    _style_header(we, 1, len(nice))
    for _, row in df.iterrows():
        vals = []
        for c in order:
            v = row[c]
            if c == "entry_date" and pd.notna(v):
                v = pd.Timestamp(v).strftime("%Y-%m-%d")
            vals.append("" if pd.isna(v) else v)
        we.append(vals)
    for rr in range(2, we.max_row + 1):
        for cc in range(1, len(nice) + 1):
            we.cell(row=rr, column=cc).font = BASE_FONT
            we.cell(row=rr, column=cc).alignment = Alignment(vertical="top", wrap_text=(order[cc-1] in ("reasons", "description")))
    for c in ["I", "J", "K"]:
        for rr in range(2, we.max_row + 1):
            we[f"{c}{rr}"].number_format = "#,##0.00;(#,##0.00);-"
    if we.max_row >= 2:
        we.conditional_formatting.add(
            f"B2:B{we.max_row}",
            ColorScaleRule(start_type="min", start_color="FFFFFF",
                           end_type="max", end_color="C00000"))
    widths = {"JE ID": 15, "Risk Score": 11, "# Tests": 9, "Why Flagged": 60,
              "Test IDs": 22, "Entry Date": 12, "Period": 9, "Lines": 7,
              "Total Debit": 14, "Total Credit": 14, "Net": 12, "Preparer": 12,
              "Approver": 12, "Source": 10, "Description": 30}
    for i, h in enumerate(nice, start=1):
        we.column_dimensions[get_column_letter(i)].width = widths.get(h, 14)
    we.freeze_panes = "A2"
    we.auto_filter.ref = f"A1:{get_column_letter(len(nice))}{max(we.max_row,1)}"

    # ---------------- Test Catalog ----------------
    wc = wb.create_sheet("Test Catalog")
    chead = ["Test ID", "Test", "Weight", "Scope-fields needed", "Ran?", "Description"]
    wc.append(chead)
    _style_header(wc, 1, len(chead))
    for _, row in result.rule_summary.iterrows():
        wc.append([row["rule_id"], row["label"], int(row["weight"]),
                   row["requires"], "Yes" if row["ran"] else "No", row["description"]])
    for rr in range(2, wc.max_row + 1):
        for cc in range(1, len(chead) + 1):
            wc.cell(row=rr, column=cc).font = BASE_FONT
            wc.cell(row=rr, column=cc).alignment = Alignment(vertical="top", wrap_text=(cc == 6))
    for col, w in {"A": 22, "B": 28, "C": 8, "D": 22, "E": 7, "F": 70}.items():
        wc.column_dimensions[col].width = w
    wc.freeze_panes = "A2"

    # ---------------- Triage (optional) ----------------
    if triage_md:
        wt = wb.create_sheet("Triage")
        wt["A1"] = "Triage Notes"
        wt["A1"].font = TITLE_FONT
        row = 3
        for line in triage_md.splitlines():
            cell = wt.cell(row=row, column=1, value=line)
            if line.startswith("## "):
                cell.value = line[3:]
                cell.font = Font(name=FONT, bold=True, size=11, color="1F3864")
            elif line.startswith("# "):
                cell.value = line[2:]
                cell.font = Font(name=FONT, bold=True, size=12)
            elif line.startswith("**") or line.startswith("_"):
                cell.font = Font(name=FONT, italic=line.startswith("_"),
                                 bold=line.startswith("**"), size=10)
            else:
                cell.font = BASE_FONT
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            row += 1
        wt.column_dimensions["A"].width = 110
        wt.freeze_panes = "A2"

    wb.save(target)
    return target


def write_csv(result: RunResult, out_path):
    target = out_path if hasattr(out_path, "write") else Path(out_path)
    result.entries.to_csv(target, index=False)
    return target
