"""
report.py — Write the exceptions workpaper.

Excel workbook with four sheets:
  Summary     — run metadata, population shape, per-test counts, Benford diagnostic
  Exceptions  — one row per flagged entry, ranked by risk score (auto-filter,
                frozen header, risk band, disposition dropdown, hyperlinks to Triage)
  Test Catalog— every rule, its weight, what it needs, whether it ran
  Triage      — plain-English triage notes for the top exceptions (optional)

Also writes a flat CSV of the Exceptions sheet for downstream analysis.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.formatting.rule import ColorScaleRule, CellIsRule, FormulaRule
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .engine import RunResult

FONT = "Arial"
HEAD_FILL = PatternFill("solid", fgColor="1F3864")
HEAD_FONT = Font(name=FONT, bold=True, color="FFFFFF", size=11)
TITLE_FONT = Font(name=FONT, bold=True, size=14, color="1F3864")
SUBHEAD_FONT = Font(name=FONT, bold=True, size=11, color="1F3864")
NOTE_FONT = Font(name=FONT, italic=True, size=9, color="595959")
BASE_FONT = Font(name=FONT, size=10)
MUTED_FONT = Font(name=FONT, size=10, color="9A9A9A")  # for skipped rows / N/A
BOLD_RED_FONT = Font(name=FONT, size=10, bold=True, color="C00000")

# Disposition palette
DISP_FILLS = {
    "Open":                 PatternFill("solid", fgColor="FFF4D6"),  # amber
    "Cleared":              PatternFill("solid", fgColor="E2EFDA"),  # green
    "Misstatement":         PatternFill("solid", fgColor="FCE4D6"),  # red-orange
    "Control deficiency":   PatternFill("solid", fgColor="DDEBF7"),  # blue
    "Referred to manager":  PatternFill("solid", fgColor="EDEDED"),  # grey
}
DISP_CHOICES = list(DISP_FILLS.keys())

SKIPPED_ROW_FILL = PatternFill("solid", fgColor="F5F5F5")


def _style_header(ws, row, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True, horizontal="left")


def _risk_band(score) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return ""
    if s >= 5:
        return "High"
    if s >= 3:
        return "Medium"
    if s >= 1:
        return "Low"
    return ""


def write_excel(result: RunResult, out_path, source_name: str = "",
                triage_md: str | None = None, dispositions: dict | None = None):
    target = out_path if hasattr(out_path, "write") else Path(out_path)
    wb = Workbook()

    df_entries = result.entries.copy() if not result.entries.empty else pd.DataFrame()

    # =========================================================
    # Summary
    # =========================================================
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Journal Entry Risk Review"
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A1:E1")

    # --- Run metadata ---
    meta = [
        ("Source file", source_name or "(in-memory)"),
        ("Run timestamp", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Period(s)", result.stats.get("periods", "")),
        ("Total entries tested", result.stats.get("total_entries", 0)),
        ("Total lines tested", result.stats.get("total_lines", 0)),
        ("Entries flagged", result.stats.get("flagged_entries", 0)),
        ("Share of entries flagged", f"{result.stats.get('flagged_pct', 0)}%"),
    ]
    r = 3
    for k, v in meta:
        ws.cell(row=r, column=1, value=k).font = Font(name=FONT, bold=True, size=10)
        ws.cell(row=r, column=2, value=v).font = BASE_FONT
        r += 1

    # --- Population shape (NEW) ---
    r += 1
    ws.cell(row=r, column=1, value="Population shape").font = SUBHEAD_FONT
    r += 1
    pop_rows = _population_shape(df_entries, result)
    for k, v in pop_rows:
        ws.cell(row=r, column=1, value=k).font = Font(name=FONT, bold=True, size=10)
        cell = ws.cell(row=r, column=2, value=v)
        cell.font = BASE_FONT
        if isinstance(v, (int, float)) and not isinstance(v, bool) and "amount" in k.lower():
            cell.number_format = "$#,##0;($#,##0);-"
        r += 1

    # --- Test results table ---
    r += 1
    ws.cell(row=r, column=1, value="Test results").font = SUBHEAD_FONT
    r += 1
    hdr = ["Test", "Weight", "Ran?", "Entries flagged", "% of flagged", "What it needs"]
    for i, h in enumerate(hdr, start=1):
        ws.cell(row=r, column=i, value=h)
    _style_header(ws, r, len(hdr))
    head_row = r
    r += 1
    rs = result.rule_summary
    flagged_total = int(result.stats.get("flagged_entries", 0)) or 0

    test_start_row = r
    for _, row in rs.iterrows():
        ran = bool(row["ran"])
        label_cell = ws.cell(row=r, column=1, value=row["label"])
        weight_cell = ws.cell(row=r, column=2, value=int(row["weight"]))
        ran_cell = ws.cell(row=r, column=3, value="Yes" if ran else "No — field missing")
        count_cell = ws.cell(row=r, column=4,
                             value=f'=COUNTIF(Exceptions!E:E,"*"&"{row["rule_id"]}"&"*")')
        pct_formula = (
            f'=IFERROR(D{r}/{flagged_total},0)' if flagged_total > 0 else '=""'
        )
        pct_cell = ws.cell(row=r, column=5, value=pct_formula)
        pct_cell.number_format = "0.0%"
        needs_cell = ws.cell(row=r, column=6, value=row["requires"])

        if ran:
            label_cell.font = BASE_FONT
            weight_cell.font = BASE_FONT
            ran_cell.font = BASE_FONT
            count_cell.font = BASE_FONT
            pct_cell.font = BASE_FONT
            needs_cell.font = BASE_FONT
        else:
            for c in (label_cell, weight_cell, ran_cell, count_cell, pct_cell, needs_cell):
                c.font = MUTED_FONT
                c.fill = SKIPPED_ROW_FILL
        weight_cell.alignment = Alignment(horizontal="center")
        ran_cell.alignment = Alignment(horizontal="center")
        count_cell.alignment = Alignment(horizontal="center")
        pct_cell.alignment = Alignment(horizontal="center")
        r += 1
    test_end_row = r - 1

    # Conditional formatting: bold red where count > 0
    if test_end_row >= test_start_row:
        red_font_rule = FormulaRule(
            formula=[f'$D{test_start_row}>0'],
            font=Font(name=FONT, size=10, bold=True, color="C00000"),
        )
        # Apply to count column for the data rows
        ws.conditional_formatting.add(
            f"D{test_start_row}:D{test_end_row}",
            CellIsRule(operator="greaterThan", formula=["0"],
                       font=Font(name=FONT, size=10, bold=True, color="C00000")),
        )

    # --- Benford section ---
    r += 1
    b = result.benford or {}
    has_benford = b.get("mad") is not None
    if has_benford:
        ws.cell(row=r, column=1, value="Benford first-digit diagnostic (population)").font = SUBHEAD_FONT
        r += 1
        ws.cell(row=r, column=1,
                value=f"Mean abs. deviation (MAD): {b['mad']}  →  {b['verdict']}").font = BASE_FONT
        r += 1
        ws.cell(row=r, column=1,
                value=f"Based on {b['n']} line amounts ≥ 1. MAD bands per Nigrini: "
                      "<0.006 close, <0.012 acceptable, <0.015 marginal, else investigate."
                ).font = NOTE_FONT
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        ws.cell(row=r, column=1).alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[r].height = 30
        r += 1
    else:
        # NEW: collapse Benford to a single grey line when n is too small
        msg = b.get("verdict") or "Too few amounts for a reliable Benford read"
        ws.cell(row=r, column=1, value=f"Benford diagnostic: {msg} (need ≥ 50 amounts).").font = NOTE_FONT
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        ws.cell(row=r, column=1).alignment = Alignment(wrap_text=True, vertical="top")
        r += 1

    # --- Disclaimer ---
    r += 2
    disclaimer = ("These are RISK INDICATORS for auditor judgement, not findings of error or fraud. "
                  "Expect false positives — percentile and round-amount tests in particular flag normal "
                  "items. Investigate flagged entries; clear or document each. Tool runs entirely locally; "
                  "no ledger data leaves this machine.")
    ws.cell(row=r, column=1, value=disclaimer).font = NOTE_FONT
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
    ws.cell(row=r, column=1).alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[r].height = 60

    for col, w in {"A": 32, "B": 10, "C": 20, "D": 16, "E": 14, "F": 42}.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"

    # =========================================================
    # Exceptions
    # =========================================================
    we = wb.create_sheet("Exceptions")
    # Column layout: JE ID, Risk Score, Risk Band, # Tests, Why Flagged, Test IDs, ...
    # IMPORTANT: Test IDs must remain on column F so Summary COUNTIF can find it.
    # (We shifted from E to F to fit Risk Band. Summary formulas updated to match.)
    order = ["je_id", "risk_score", "__band", "tests_fired", "reasons", "test_ids",
             "entry_date", "period", "lines", "total_debit", "total_credit",
             "net_amount", "entered_by", "approved_by", "source", "description"]
    nice = ["JE ID", "Risk Score", "Risk Band", "# Tests", "Why Flagged", "Test IDs",
            "Entry Date", "Period", "Lines", "Total Debit", "Total Credit",
            "Net", "Preparer", "Approver", "Source", "Description"]

    # We just changed the test_ids column to F. Patch Summary COUNTIFs.
    for rr in range(test_start_row, test_end_row + 1):
        cell = ws.cell(row=rr, column=4)
        if isinstance(cell.value, str) and cell.value.startswith("=COUNTIF(Exceptions!E:E"):
            cell.value = cell.value.replace("Exceptions!E:E", "Exceptions!F:F")

    df = df_entries
    if df.empty:
        df = pd.DataFrame(columns=[c for c in order if not c.startswith("__")])

    df = df.assign(__band=df.get("risk_score", pd.Series([], dtype=float)).map(_risk_band)
                   if "risk_score" in df.columns else "")

    # Ensure all expected columns exist
    for c in order:
        if c not in df.columns:
            df[c] = ""

    _dm = dispositions or {}
    df["__disp"] = df["je_id"].map(lambda j: str((_dm.get(str(j)) or {}).get("disposition", "") or ""))
    df["__note"] = df["je_id"].map(lambda j: str((_dm.get(str(j)) or {}).get("note", "") or ""))
    full_order = order + ["__disp", "__note"]
    full_nice = nice + ["Disposition", "Auditor Note"]

    we.append(full_nice)
    _style_header(we, 1, len(full_nice))

    for _, row in df.iterrows():
        vals = []
        for c in full_order:
            v = row.get(c, "")
            if c == "entry_date" and pd.notna(v) and v != "":
                try:
                    v = pd.Timestamp(v).strftime("%Y-%m-%d")
                except Exception:
                    pass
            vals.append("" if (isinstance(v, float) and pd.isna(v)) or v is None else v)
        we.append(vals)

    # Cell-level formatting
    je_col_letter = get_column_letter(full_order.index("je_id") + 1)  # A
    band_col_letter = get_column_letter(full_order.index("__band") + 1)  # C
    disp_col_idx = full_order.index("__disp") + 1
    disp_col_letter = get_column_letter(disp_col_idx)

    triage_je_rows = _index_triage_je_rows(triage_md) if triage_md else {}

    for rr in range(2, we.max_row + 1):
        for cc in range(1, len(full_nice) + 1):
            cell = we.cell(row=rr, column=cc)
            cell.font = BASE_FONT
            field = full_order[cc-1]
            cell.alignment = Alignment(
                vertical="top",
                wrap_text=(field in ("reasons", "description", "__note"))
            )
        # Number formatting
        for col_field in ("total_debit", "total_credit", "net_amount"):
            idx = full_order.index(col_field) + 1
            we.cell(row=rr, column=idx).number_format = "#,##0.00;(#,##0.00);-"

        # Risk band centered
        we.cell(row=rr, column=full_order.index("__band") + 1).alignment = \
            Alignment(horizontal="center", vertical="top")

        # Hyperlink JE ID to its row on the Triage sheet (if triage exists)
        if triage_md:
            je_val = we.cell(row=rr, column=full_order.index("je_id") + 1).value
            if je_val and str(je_val) in triage_je_rows:
                target_row = triage_je_rows[str(je_val)]
                cell = we.cell(row=rr, column=full_order.index("je_id") + 1)
                cell.hyperlink = f"#Triage!A{target_row}"
                cell.font = Font(name=FONT, size=10, color="0563C1", underline="single")

    # Color scale on Risk Score (column B)
    if we.max_row >= 2:
        we.conditional_formatting.add(
            f"B2:B{we.max_row}",
            ColorScaleRule(start_type="min", start_color="FFFFFF",
                           end_type="max", end_color="C00000"))

    # Risk Band conditional fills
    if we.max_row >= 2:
        band_range = f"{band_col_letter}2:{band_col_letter}{we.max_row}"
        we.conditional_formatting.add(
            band_range,
            CellIsRule(operator="equal", formula=['"High"'],
                       fill=PatternFill("solid", fgColor="F4B5B5"),
                       font=Font(name=FONT, size=10, bold=True, color="9C1B1B")))
        we.conditional_formatting.add(
            band_range,
            CellIsRule(operator="equal", formula=['"Medium"'],
                       fill=PatternFill("solid", fgColor="FFE4A8"),
                       font=Font(name=FONT, size=10, bold=True, color="9C6A1B")))
        we.conditional_formatting.add(
            band_range,
            CellIsRule(operator="equal", formula=['"Low"'],
                       fill=PatternFill("solid", fgColor="E2EFDA"),
                       font=Font(name=FONT, size=10, color="375623")))

    # Disposition dropdown + conditional fills
    if we.max_row >= 2:
        disp_range = f"{disp_col_letter}2:{disp_col_letter}{we.max_row}"
        formula = '"' + ",".join(DISP_CHOICES) + '"'
        dv = DataValidation(type="list", formula1=formula, allow_blank=True,
                            showDropDown=False, errorStyle="warning")
        dv.error = "Pick one of: " + ", ".join(DISP_CHOICES)
        dv.errorTitle = "Disposition"
        dv.prompt = "Auditor disposition for this exception."
        dv.promptTitle = "Disposition"
        dv.add(disp_range)
        we.add_data_validation(dv)
        # Per-choice conditional fill
        for choice, fill in DISP_FILLS.items():
            we.conditional_formatting.add(
                disp_range,
                CellIsRule(operator="equal", formula=[f'"{choice}"'], fill=fill),
            )

    widths = {"JE ID": 15, "Risk Score": 10, "Risk Band": 11, "# Tests": 9,
              "Why Flagged": 60, "Test IDs": 22, "Entry Date": 12, "Period": 9,
              "Lines": 7, "Total Debit": 14, "Total Credit": 14, "Net": 12,
              "Preparer": 12, "Approver": 12, "Source": 10, "Description": 30,
              "Disposition": 20, "Auditor Note": 40}
    for i, h in enumerate(full_nice, start=1):
        we.column_dimensions[get_column_letter(i)].width = widths.get(h, 14)
    we.freeze_panes = "A2"
    we.auto_filter.ref = f"A1:{get_column_letter(len(full_nice))}{max(we.max_row,1)}"

    # =========================================================
    # Test Catalog
    # =========================================================
    wc = wb.create_sheet("Test Catalog")
    chead = ["Test ID", "Test", "Weight", "Scope-fields needed", "Ran?", "Description"]
    wc.append(chead)
    _style_header(wc, 1, len(chead))
    for _, row in result.rule_summary.iterrows():
        ran = bool(row["ran"])
        wc.append([row["rule_id"], row["label"], int(row["weight"]),
                   row["requires"], "Yes" if ran else "No", row["description"]])
        rr = wc.max_row
        if not ran:
            for cc in range(1, len(chead) + 1):
                wc.cell(row=rr, column=cc).fill = SKIPPED_ROW_FILL
                wc.cell(row=rr, column=cc).font = MUTED_FONT
    for rr in range(2, wc.max_row + 1):
        for cc in range(1, len(chead) + 1):
            if wc.cell(row=rr, column=cc).font.color is None or wc.cell(row=rr, column=cc).font.color.rgb != MUTED_FONT.color.rgb:
                wc.cell(row=rr, column=cc).font = BASE_FONT
            wc.cell(row=rr, column=cc).alignment = Alignment(vertical="top", wrap_text=(cc == 6))
    for col, w in {"A": 22, "B": 30, "C": 8, "D": 24, "E": 8, "F": 70}.items():
        wc.column_dimensions[col].width = w
    wc.freeze_panes = "A2"

    # =========================================================
    # Triage (optional)
    # =========================================================
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


def _population_shape(df_entries: pd.DataFrame, result: RunResult) -> list[tuple]:
    """Build a small list of population-shape metrics for the Summary sheet."""
    rows = []
    stats = result.stats or {}
    # Unique counts pulled from rule_summary inputs or entries
    if not df_entries.empty:
        if "entered_by" in df_entries.columns:
            unique_preparers = df_entries["entered_by"].nunique()
            rows.append(("Unique preparers (flagged)", int(unique_preparers)))
        if "approved_by" in df_entries.columns:
            unique_approvers = df_entries["approved_by"].nunique()
            rows.append(("Unique approvers (flagged)", int(unique_approvers)))
        if "entry_date" in df_entries.columns:
            try:
                dates = pd.to_datetime(df_entries["entry_date"], errors="coerce").dropna()
                if not dates.empty:
                    rows.append(("Earliest flagged entry date", dates.min().strftime("%Y-%m-%d")))
                    rows.append(("Latest flagged entry date", dates.max().strftime("%Y-%m-%d")))
            except Exception:
                pass
        for amt_col in ("total_debit", "net_amount"):
            if amt_col in df_entries.columns:
                try:
                    series = pd.to_numeric(df_entries[amt_col], errors="coerce").dropna().abs()
                    if not series.empty:
                        label = "Total flagged $ (debits)" if amt_col == "total_debit" else "Total flagged net $"
                        rows.append((label, round(float(series.sum()), 2)))
                        break
                except Exception:
                    pass
    # Fall back to whatever the engine put in stats
    for key in ("avg_entry_amount", "max_entry_amount"):
        if key in stats:
            rows.append((key.replace("_", " ").capitalize(), stats[key]))
    if not rows:
        rows.append(("(no flagged entries)", "—"))
    return rows


def _index_triage_je_rows(triage_md: str) -> dict:
    """Find which row each JE ID appears on in the Triage sheet output.

    Triage notes use header lines like 'ADV-2024-0005  ·  risk 3  ·  2 test(s)'.
    Returns {je_id: triage_sheet_row_number}.
    """
    je_rows: dict[str, int] = {}
    # Triage sheet writes starting at row 3, one row per source line.
    row = 3
    for line in triage_md.splitlines():
        stripped = line.strip()
        # Header lines look like "ADV-2024-0005  ·  risk 3  ·  2 test(s)"
        if "  ·  risk " in stripped or " · risk " in stripped:
            # Strip markdown header markers (##, ###) before extracting JE ID
            cleaned = stripped.lstrip("#").strip()
            je_id = cleaned.split()[0] if cleaned else ""
            if je_id and je_id not in je_rows:
                je_rows[je_id] = row
        row += 1
    return je_rows


def write_csv(result: RunResult, out_path, dispositions: dict | None = None):
    target = out_path if hasattr(out_path, "write") else Path(out_path)
    df = result.entries.copy()
    if "risk_score" in df.columns:
        df.insert(df.columns.get_loc("risk_score") + 1, "risk_band",
                  df["risk_score"].map(_risk_band))
    if dispositions:
        df["disposition"] = df["je_id"].map(lambda j: str((dispositions.get(str(j)) or {}).get("disposition", "") or ""))
        df["auditor_note"] = df["je_id"].map(lambda j: str((dispositions.get(str(j)) or {}).get("note", "") or ""))
    df.to_csv(target, index=False)
    return target
