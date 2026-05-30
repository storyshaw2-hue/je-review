"""
cli.py — Command-line entry point.

    python -m jereview run --input ledger.csv [--map mapping.json] \
        [--out report.xlsx] [--threshold 50000]

Runs entirely locally. With --map you translate your export's columns to the
canonical schema; without it, the tool assumes your columns already use the
canonical names (as the sample data does).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from .schema import ColumnMapping, normalize, NormalizationError
from .rules import RuleContext
from .engine import run
from .report import write_excel, write_csv


def _load(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        return pd.read_excel(path, dtype=str)
    return pd.read_csv(path, dtype=str)


def cmd_run(args):
    src = Path(args.input)
    if not src.exists():
        sys.exit(f"Input not found: {src}")
    raw = _load(src)

    mapping = ColumnMapping.from_file(args.map) if args.map else ColumnMapping.identity(list(raw.columns))
    try:
        df = normalize(raw, mapping)
    except NormalizationError as e:
        sys.exit(f"Mapping error: {e}")

    ctx = RuleContext(available=set())
    if args.threshold is not None:
        ctx.approval_threshold = args.threshold

    result = run(df, ctx)

    triage_md = None
    if args.triage and args.triage != "none":
        from .triage import local_triage, llm_triage
        if args.triage == "ai":
            import os
            key = os.environ.get("ANTHROPIC_API_KEY" if args.provider == "anthropic" else "OPENAI_API_KEY", "")
            triage_md, used_ai = llm_triage(result.entries, args.triage_top, args.provider, args.model, key)
            print(f"Triage: {'AI' if used_ai else 'local (AI unavailable/no key)'}")
        else:
            triage_md = local_triage(result.entries, args.triage_top)
            print("Triage: local (no data egress)")

    out = Path(args.out) if args.out else src.with_name(src.stem + "_exceptions.xlsx")
    write_excel(result, out, source_name=src.name, triage_md=triage_md)
    if args.csv:
        write_csv(result, Path(args.csv))

    s = result.stats
    print(f"Tested {s['total_entries']} entries / {s['total_lines']} lines for {s['periods']}")
    print(f"Flagged {s['flagged_entries']} entries ({s['flagged_pct']}%).")
    if result.skipped_rules:
        print(f"Skipped (missing fields): {', '.join(result.skipped_rules)}")
    print(f"Workpaper → {out}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="jereview", description="Local journal-entry risk testing.")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("run", help="Run the risk tests over a JE export.")
    pr.add_argument("--input", required=True, help="JE export (.csv or .xlsx)")
    pr.add_argument("--map", help="Column mapping JSON (canonical -> source column)")
    pr.add_argument("--out", help="Output .xlsx path")
    pr.add_argument("--csv", help="Also write flat CSV of exceptions to this path")
    pr.add_argument("--threshold", type=float, help="Approval threshold for the below-threshold test")
    pr.add_argument("--triage", choices=["none", "local", "ai"], default="none",
                    help="Add triage notes: 'local' (no egress) or 'ai' (opt-in, sends flagged rows only)")
    pr.add_argument("--triage-top", type=int, default=10, help="How many top exceptions to triage")
    pr.add_argument("--provider", choices=["anthropic", "openai"], default="anthropic",
                    help="AI triage provider (reads ANTHROPIC_API_KEY / OPENAI_API_KEY from env)")
    pr.add_argument("--model", default="claude-sonnet-4-20250514", help="AI triage model name")
    pr.set_defaults(func=cmd_run)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
