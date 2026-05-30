"""
triage.py — Plain-English triage notes for the top exceptions.

Two modes:
  local_triage()  — deterministic notes built from the fired tests. NO data
                    leaves the machine. Always available; this is the default.
  llm_triage()    — opt-in. Sends ONLY the flagged exception rows (never the
                    full ledger) to an OpenAI/Anthropic model for a more fluent
                    write-up. Requires an explicit provider + API key. Falls
                    back to local_triage() on any error.

Triage notes are investigative leads for auditor judgement, not conclusions.
"""

from __future__ import annotations

import json

import pandas as pd

# What an auditor should do when each test fires.
SUGGESTED_PROCEDURE = {
    "WEEKEND_POSTING": "Confirm the business reason for a weekend posting; agree to support and approval.",
    "AFTER_PERIOD_END": "Verify the entry belongs to the stated period and was not back-dated to shift results; check approval timing.",
    "PERIOD_END_CONCENTRATION": "Assess whether late-period timing is normal cutoff or pressure to hit targets; review support.",
    "OFFHOURS_POSTING": "Corroborate why the entry was posted outside business hours and that it ties to a legitimate process.",
    "ROUND_AMOUNT": "Round amounts often signal estimates or plugs — trace to primary documentation, not a calculation.",
    "BELOW_THRESHOLD": "Determine whether the amount was structured to stay under an approval level; look for split/related entries.",
    "LARGE_OUTLIER": "Obtain and inspect primary support and approval at the appropriate authority level.",
    "SELDOM_ACCOUNT": "Understand the purpose of a rarely-used account and confirm the entry is appropriate and properly classified.",
    "SENSITIVE_ACCOUNT": "Scrutinize entries to suspense/reserve/equity/revenue/IC accounts — common manipulation vehicles.",
    "MANUAL_SOURCE": "Manual entries bypass system controls — verify preparer authority, independent review, and support.",
    "SOD_CONFLICT": "Same preparer and approver — treat as a control deficiency and obtain independent re-approval.",
    "BLANK_DESCRIPTION": "Require a business rationale and support before clearing an undocumented entry.",
    "SUSPICIOUS_KEYWORD": "Language like 'plug'/'to balance' suggests a forced entry — investigate what is being balanced and why.",
    "UNBALANCED_ENTRY": "An out-of-balance entry indicates a posting/data error or partial entry — reconcile both sides before relying on it.",
}

TRIAGE_FIELDS = ["je_id", "risk_score", "tests_fired", "test_ids", "reasons",
                 "entry_date", "period", "net_amount", "total_debit",
                 "entered_by", "approved_by", "source"]


def _top(entries: pd.DataFrame, top_n: int) -> pd.DataFrame:
    if entries.empty:
        return entries
    cols = [c for c in TRIAGE_FIELDS if c in entries.columns]
    return entries.head(top_n)[cols].copy()


def local_triage(entries: pd.DataFrame, top_n: int = 10) -> str:
    """Deterministic triage notes — no data leaves the machine."""
    if entries.empty:
        return "No exceptions were flagged."
    top = _top(entries, top_n)
    lines = [f"# Triage notes — top {len(top)} exception(s)", "",
             "_Investigative leads for auditor judgement, not conclusions. Generated locally; no data left this machine._", ""]
    for _, r in top.iterrows():
        ed = r.get("entry_date")
        ed = pd.Timestamp(ed).strftime("%Y-%m-%d") if pd.notna(ed) else "—"
        lines.append(f"## {r['je_id']}  ·  risk {int(r['risk_score'])}  ·  {int(r['tests_fired'])} test(s)")
        lines.append(f"Entry date {ed}, period {r.get('period','—')}, net ${float(r.get('net_amount',0)):,.2f}, "
                     f"preparer {r.get('entered_by') or '—'}, approver {r.get('approved_by') or '—'}, source {r.get('source') or '—'}.")
        lines.append(f"**Why flagged:** {r['reasons']}")
        procs = []
        for tid in str(r["test_ids"]).split(", "):
            p = SUGGESTED_PROCEDURE.get(tid)
            if p and p not in procs:
                procs.append(p)
        lines.append("**Suggested procedures:**")
        for p in procs:
            lines.append(f"- {p}")
        lines.append("")
    return "\n".join(lines)


def build_payload(entries: pd.DataFrame, top_n: int) -> list[dict]:
    """Compact, flagged-rows-only payload for the LLM. Never the full ledger."""
    top = _top(entries, top_n)
    recs = []
    for _, r in top.iterrows():
        ed = r.get("entry_date")
        recs.append({
            "je_id": r["je_id"],
            "risk_score": int(r["risk_score"]),
            "tests": str(r["test_ids"]),
            "reasons": str(r["reasons"]),
            "entry_date": pd.Timestamp(ed).strftime("%Y-%m-%d") if pd.notna(ed) else None,
            "period": r.get("period"),
            "net_amount": float(r.get("net_amount", 0)),
            "preparer": r.get("entered_by"),
            "approver": r.get("approved_by"),
            "source": r.get("source"),
        })
    return recs


_SYSTEM = (
    "You are a senior internal auditor writing concise triage notes on flagged "
    "journal entries. For each entry, in 2-3 sentences: state the risk, what "
    "specifically to check, and how urgent it is. Then add a short overall "
    "paragraph. These are investigative leads, not conclusions — do not assert "
    "that fraud or error has occurred. Output GitHub-flavored markdown."
)


def llm_triage(entries: pd.DataFrame, top_n: int, provider: str, model: str,
               api_key: str) -> tuple[str, bool]:
    """
    Opt-in AI triage. Returns (markdown, used_ai). Sends ONLY flagged rows.
    Falls back to local_triage on any error (used_ai=False).
    """
    if entries.empty:
        return "No exceptions were flagged.", False
    if not api_key:
        return local_triage(entries, top_n), False

    payload = build_payload(entries, top_n)
    user = ("Write triage notes for these flagged journal entries (JSON below). "
            "These are the only records shared with you.\n\n" + json.dumps(payload, indent=2))
    try:
        import httpx
        if provider.lower() == "anthropic":
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": model, "max_tokens": 2000, "system": _SYSTEM,
                      "messages": [{"role": "user", "content": user}]},
                timeout=120,
            )
            resp.raise_for_status()
            text = "".join(b.get("text", "") for b in resp.json().get("content", [])
                           if b.get("type") == "text")
        else:  # openai-compatible
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "temperature": 0, "max_tokens": 2000,
                      "messages": [{"role": "system", "content": _SYSTEM},
                                   {"role": "user", "content": user}]},
                timeout=120,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
        header = "_AI-generated triage. Only the flagged exception rows above were sent; the full ledger was not._\n\n"
        return header + text, True
    except Exception as e:  # noqa: BLE001
        return (f"_AI triage unavailable ({e}); showing local notes instead._\n\n"
                + local_triage(entries, top_n)), False
