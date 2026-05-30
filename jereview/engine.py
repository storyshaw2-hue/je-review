"""
engine.py — Run the rule library over a normalized JE frame and aggregate
per-entry findings, a composite risk score, and population diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .rules import RULES, RuleContext
from .schema import available_fields


@dataclass
class RunResult:
    entries: pd.DataFrame          # one row per flagged entry, ranked by risk_score
    rule_summary: pd.DataFrame     # per-rule counts + whether it ran
    benford: dict                  # first-digit diagnostic
    stats: dict                    # population stats
    skipped_rules: list = field(default_factory=list)


def _benford(amounts: pd.Series) -> dict:
    """First-digit Benford diagnostic (population-level). Returns MAD + verdict."""
    a = amounts.abs()
    a = a[a >= 1]
    lead = a.astype(str).str.replace(".", "", regex=False).str.lstrip("0").str[:1]
    lead = pd.to_numeric(lead, errors="coerce").dropna()
    lead = lead[(lead >= 1) & (lead <= 9)]
    n = len(lead)
    if n < 50:
        return {"n": n, "mad": None, "verdict": "Too few amounts for a reliable Benford read"}
    observed = lead.value_counts(normalize=True).reindex(range(1, 10), fill_value=0.0)
    expected = pd.Series({d: np.log10(1 + 1 / d) for d in range(1, 10)})
    mad = float((observed - expected).abs().mean())
    # Nigrini's conformity bands for first-digit MAD
    if mad < 0.006:
        verdict = "Close conformity"
    elif mad < 0.012:
        verdict = "Acceptable conformity"
    elif mad < 0.015:
        verdict = "Marginal conformity"
    else:
        verdict = "Nonconformity — investigate"
    return {"n": n, "mad": round(mad, 5), "verdict": verdict,
            "observed": observed.round(4).to_dict(), "expected": expected.round(4).to_dict()}


def run(df: pd.DataFrame, ctx: RuleContext | None = None) -> RunResult:
    available = available_fields(df)
    ctx = ctx or RuleContext(available=available)
    ctx.available = available

    # per-entry accumulation
    reasons: dict[str, list[str]] = {}
    tests: dict[str, list[str]] = {}
    score: dict[str, int] = {}
    rule_rows = []
    skipped = []

    for r in RULES:
        if not r.runnable(available):
            skipped.append(r.id)
            rule_rows.append({"rule_id": r.id, "label": r.label, "weight": r.weight,
                              "ran": False, "entries_flagged": 0,
                              "requires": ",".join(r.requires), "description": r.description})
            continue
        hits = r.func(df, ctx) or {}
        for je, reason in hits.items():
            reasons.setdefault(je, []).append(f"[{r.id}] {reason}")
            tests.setdefault(je, []).append(r.id)
            score[je] = score.get(je, 0) + r.weight
        rule_rows.append({"rule_id": r.id, "label": r.label, "weight": r.weight,
                          "ran": True, "entries_flagged": len(hits),
                          "requires": ",".join(r.requires), "description": r.description})

    # entry-level rollup of identity fields
    agg = df.groupby("je_id").agg(
        entry_date=("entry_date", "first"),
        period=("period", "first"),
        entered_by=("entered_by", "first"),
        approved_by=("approved_by", "first"),
        source=("source", "first"),
        lines=("account", "size"),
        total_debit=("debit", "sum"),
        total_credit=("credit", "sum"),
        net_amount=("amount", "sum"),
        description=("description", lambda s: " | ".join(sorted({str(x) for x in s.dropna() if str(x).strip()}))),
    )

    rows = []
    for je in score:
        a = agg.loc[je]
        rows.append({
            "je_id": je,
            "risk_score": score[je],
            "tests_fired": len(tests[je]),
            "test_ids": ", ".join(tests[je]),
            "reasons": " ; ".join(reasons[je]),
            "entry_date": a["entry_date"],
            "period": a["period"],
            "lines": int(a["lines"]),
            "total_debit": round(float(a["total_debit"]), 2),
            "total_credit": round(float(a["total_credit"]), 2),
            "net_amount": round(float(a["net_amount"]), 2),
            "entered_by": a["entered_by"],
            "approved_by": a["approved_by"],
            "source": a["source"],
            "description": a["description"],
        })
    entries = pd.DataFrame(rows)
    if not entries.empty:
        entries = entries.sort_values(["risk_score", "tests_fired"], ascending=False).reset_index(drop=True)

    stats = {
        "total_entries": int(df["je_id"].nunique()),
        "total_lines": int(len(df)),
        "flagged_entries": int(len(entries)),
        "flagged_pct": round(100 * len(entries) / max(df["je_id"].nunique(), 1), 1),
        "periods": ", ".join(sorted(set(df["period"].dropna()))),
    }
    return RunResult(
        entries=entries,
        rule_summary=pd.DataFrame(rule_rows),
        benford=_benford(df["amount"]),
        stats=stats,
        skipped_rules=skipped,
    )
