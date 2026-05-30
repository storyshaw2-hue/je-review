"""
pipeline.py — One call that the UI (or any caller) uses: take a raw export
frame, run the full review, and return everything needed to render and download.
Framework-agnostic and headlessly testable.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .schema import ColumnMapping, normalize
from .rules import RuleContext
from .engine import run, RunResult
from .report import write_excel, write_csv
from .triage import local_triage, llm_triage


@dataclass
class Output:
    result: RunResult
    triage_md: str | None
    used_ai: bool
    xlsx_bytes: bytes
    csv_bytes: bytes


def run_pipeline(raw_df: pd.DataFrame, *, mapping: dict | None = None,
                 threshold: float | None = None, triage: str = "none",
                 top_n: int = 10, provider: str = "anthropic",
                 model: str = "claude-sonnet-4-20250514", api_key: str = "",
                 source_name: str = "upload") -> Output:
    cm = ColumnMapping(mapping=mapping) if mapping else ColumnMapping.identity(list(raw_df.columns))
    df = normalize(raw_df, cm)

    ctx = RuleContext(available=set())
    if threshold is not None:
        ctx.approval_threshold = threshold
    result = run(df, ctx)

    triage_md, used_ai = None, False
    if triage == "local":
        triage_md = local_triage(result.entries, top_n)
    elif triage == "ai":
        triage_md, used_ai = llm_triage(result.entries, top_n, provider, model, api_key)

    import io
    xbuf, cbuf = io.BytesIO(), io.StringIO()
    write_excel(result, xbuf, source_name=source_name, triage_md=triage_md)
    write_csv(result, cbuf)
    xlsx_bytes = xbuf.getvalue()
    csv_bytes = cbuf.getvalue().encode("utf-8")

    return Output(result=result, triage_md=triage_md, used_ai=used_ai,
                  xlsx_bytes=xlsx_bytes, csv_bytes=csv_bytes)


def workpaper_bytes(result: RunResult, *, source_name: str = "upload",
                    triage_md: str | None = None, dispositions: dict | None = None):
    """Regenerate the Excel + CSV workpaper bytes from a RunResult, optionally
    folding in auditor dispositions. Used by the UI to refresh downloads after
    the reviewer edits dispositions, without re-running the engine."""
    import io
    xbuf, cbuf = io.BytesIO(), io.StringIO()
    write_excel(result, xbuf, source_name=source_name, triage_md=triage_md, dispositions=dispositions)
    write_csv(result, cbuf, dispositions=dispositions)
    return xbuf.getvalue(), cbuf.getvalue().encode("utf-8")
