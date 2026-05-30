"""
schema.py — Canonical journal-entry schema, column mapping, and normalization.

A company's GL export rarely matches our field names (SAP, Oracle, NetSuite,
QuickBooks all differ). The mapping layer translates their columns into a
canonical line-level frame inspired by the AICPA Audit Data Standard for
journal entries. Rules then run against the canonical names, so the engine
never has to know about a specific ERP.

Canonical line-level fields (one row per debit/credit line):
    je_id           journal entry / document number (groups lines)   [required]
    line_no         line number within the entry                     [optional]
    entry_date      date the entry was recorded/posted               [required]
    effective_date  accounting/period date the entry affects         [optional]
    period          period label, e.g. "2024-11" (derived if absent) [optional]
    posted_at       full posting timestamp (for off-hours test)      [optional]
    account         GL account number                                [required]
    account_name    GL account description                           [optional]
    description     line/header narrative                            [optional]
    amount          signed amount (debit +, credit -)                [required*]
    debit           debit amount                                     [required*]
    credit          credit amount                                    [required*]
    entered_by      preparer / user who posted                       [optional]
    approved_by     approver                                         [optional]
    source          'Manual' / 'System' / 'Auto' / etc.              [optional]

  *Provide EITHER `amount` OR both `debit`/`credit`. The normalizer derives
   whichever is missing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

CANONICAL_FIELDS = [
    "je_id", "line_no", "entry_date", "effective_date", "period", "posted_at",
    "account", "account_name", "description", "amount", "debit", "credit",
    "entered_by", "approved_by", "source",
]

REQUIRED_FIELDS = ["je_id", "entry_date", "account"]


def _clean_numeric(s: pd.Series) -> pd.Series:
    """Coerce a possibly-messy money column to float: handles $/£/€, thousands
    separators, whitespace, and parenthesised negatives like '(1,234.56)'."""
    if pd.api.types.is_numeric_dtype(s):
        return pd.to_numeric(s, errors="coerce")
    t = s.astype("string").str.strip()
    neg = (t.str.startswith("(") & t.str.endswith(")")).fillna(False)
    t = t.str.replace(r"^\((.*)\)$", r"\1", regex=True)
    t = t.str.replace(",", "", regex=False)
    t = t.str.replace(r"[^0-9.\-]", "", regex=True)
    t = t.replace({"": pd.NA, "-": pd.NA, ".": pd.NA})
    num = pd.to_numeric(t, errors="coerce")
    return num.where(~neg, -num.abs())


def _to_datetime(s: pd.Series) -> pd.Series:
    """Parse dates tolerantly across mixed formats."""
    try:
        return pd.to_datetime(s, errors="coerce", format="mixed")
    except (ValueError, TypeError):
        return pd.to_datetime(s, errors="coerce")


@dataclass
class ColumnMapping:
    """Maps canonical field name -> source column name in the export."""
    mapping: dict

    @classmethod
    def from_file(cls, path: str | Path) -> "ColumnMapping":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        # allow either {"mapping": {...}} or a bare {...}
        return cls(mapping=data.get("mapping", data))

    @classmethod
    def identity(cls, columns: list[str]) -> "ColumnMapping":
        """Assume the export already uses canonical names (for our sample data)."""
        cols = [str(c).strip() for c in columns]
        return cls(mapping={c: c for c in CANONICAL_FIELDS if c in cols})


class NormalizationError(ValueError):
    pass


def normalize(df_raw: pd.DataFrame, mapping: ColumnMapping) -> pd.DataFrame:
    """
    Translate a raw export into the canonical line-level frame.

    Missing OPTIONAL fields are added as empty columns so rules can detect
    their absence and skip gracefully. Missing REQUIRED fields raise.
    """
    df_raw = df_raw.rename(columns=lambda c: str(c).strip())
    inv = {str(src).strip(): canon for canon, src in mapping.mapping.items()}
    df = df_raw.rename(columns=inv).copy()
    df = df.dropna(how="all")            # drop fully-blank rows some exports include

    missing_required = [f for f in REQUIRED_FIELDS if f not in df.columns]
    if missing_required:
        raise NormalizationError(
            f"Required field(s) not mapped: {missing_required}. "
            f"Fix your mapping file. Available source columns: {list(df_raw.columns)}"
        )

    # amount vs debit/credit reconciliation
    has_amount = "amount" in df.columns
    has_dc = "debit" in df.columns and "credit" in df.columns
    if not has_amount and not has_dc:
        raise NormalizationError(
            "Provide either an `amount` column or both `debit` and `credit`."
        )
    if not has_amount:
        df["amount"] = _clean_numeric(df["debit"]).fillna(0) - _clean_numeric(df["credit"]).fillna(0)
    else:
        df["amount"] = _clean_numeric(df["amount"])
    if not has_dc:
        amt = df["amount"]
        df["debit"] = amt.clip(lower=0)
        df["credit"] = (-amt).clip(lower=0)
    else:
        df["debit"] = _clean_numeric(df["debit"]).fillna(0)
        df["credit"] = _clean_numeric(df["credit"]).fillna(0)

    # add any absent optional fields as empty so rule guards work uniformly
    for f in CANONICAL_FIELDS:
        if f not in df.columns:
            df[f] = pd.NA

    # types
    for dcol in ["entry_date", "effective_date", "posted_at"]:
        df[dcol] = _to_datetime(df[dcol])
    df["account"] = df["account"].astype("string").str.strip()
    df["je_id"] = df["je_id"].astype("string").str.strip()
    df = df[df["je_id"].notna() & (df["je_id"].str.len() > 0)]
    for tcol in ["account_name", "description", "entered_by", "approved_by", "source"]:
        df[tcol] = df[tcol].astype("string")

    # derive period from effective_date (fallback entry_date) if absent/empty
    base_date = df["effective_date"].fillna(df["entry_date"])
    derived_period = base_date.dt.to_period("M").astype("string")
    df["period"] = df["period"].astype("string")
    df["period"] = df["period"].where(df["period"].notna() & (df["period"].str.len() > 0), derived_period)

    return df[CANONICAL_FIELDS].reset_index(drop=True)


def available_fields(df: pd.DataFrame) -> set[str]:
    """Canonical fields that are actually populated (not all-null)."""
    return {c for c in df.columns if df[c].notna().any()}
