"""
webapp.py — Shared Streamlit UI body, used by both the native app and the
in-browser (stlite) build.

render(allow_ai): when allow_ai is False (the in-browser build) the AI-triage
option is hidden, so no network egress is possible and httpx is never imported.
Kept free of version-specific Streamlit kwargs so it runs on the older Streamlit
that ships inside stlite as well as a current local install.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jereview.pipeline import run_pipeline
from jereview.schema import NormalizationError


def render(allow_ai: bool = True) -> None:
    st.set_page_config(page_title="JE Risk Review", page_icon="🧾", layout="wide")
    st.title("🧾 Journal Entry Risk Review")
    if allow_ai:
        st.caption("Runs on this machine. Your ledger does not leave it unless you turn on AI triage below.")
    else:
        st.caption("Runs entirely in your browser. Your ledger never leaves this computer — nothing is uploaded to any server.")

    with st.sidebar:
        st.header("Options")
        threshold = st.number_input("Approval threshold ($)", min_value=0, value=50000, step=1000,
                                    help="Used by the 'just below threshold' test.")
        st.divider()
        triage_choices = ["None", "Local (no data leaves)"]
        if allow_ai:
            triage_choices.append("AI-enhanced (sends flagged rows)")
        triage_mode = st.radio("Triage notes", triage_choices, index=1)
        top_n = st.slider("Triage how many top exceptions", 1, 25, 10)
        provider = model = api_key = None
        if allow_ai and triage_mode.startswith("AI"):
            st.warning("AI triage sends the **flagged exception rows** (not the full ledger) "
                       "to the provider you choose. Leave this off if any data is sensitive.")
            provider = st.selectbox("Provider", ["anthropic", "openai"])
            model = st.text_input("Model",
                                  value="claude-sonnet-4-20250514" if provider == "anthropic" else "gpt-4o")
            api_key = st.text_input("API key", type="password")
        st.divider()
        map_file = st.file_uploader("Column mapping (JSON, optional)", type=["json"],
                                    help="Only if your export's column names differ from the canonical schema.")

    uploaded = st.file_uploader("Upload a journal-entry export (.csv or .xlsx)", type=["csv", "xlsx", "xlsm"])

    if uploaded is None or not st.button("Run review", type="primary"):
        return

    try:
        if uploaded.name.lower().endswith((".xlsx", ".xlsm")):
            raw = pd.read_excel(uploaded, dtype=str)
        else:
            raw = pd.read_csv(uploaded, dtype=str)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read the file: {e}")
        return

    mapping = None
    if map_file is not None:
        try:
            data = json.load(map_file)
            mapping = data.get("mapping", data)
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not read the mapping file: {e}")
            return

    mode = {"None": "none", "Local (no data leaves)": "local",
            "AI-enhanced (sends flagged rows)": "ai"}[triage_mode]

    try:
        with st.spinner("Running risk tests…"):
            out = run_pipeline(raw, mapping=mapping, threshold=float(threshold),
                               triage=mode, top_n=top_n, provider=provider or "anthropic",
                               model=model or "claude-sonnet-4-20250514",
                               api_key=api_key or "", source_name=uploaded.name)
    except NormalizationError as e:
        st.error(f"Mapping problem: {e}")
        return

    s = out.result.stats
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Entries tested", s["total_entries"])
    c2.metric("Lines tested", s["total_lines"])
    c3.metric("Entries flagged", s["flagged_entries"])
    c4.metric("Flagged %", f"{s['flagged_pct']}%")

    if out.result.skipped_rules:
        st.info("Tests skipped (your export is missing the fields they need): "
                + ", ".join(out.result.skipped_rules))

    b = out.result.benford
    if b.get("mad") is not None:
        st.caption(f"Benford first-digit MAD {b['mad']} → {b['verdict']} (n={b['n']}).")

    st.download_button("⬇️ Download Excel workpaper", out.xlsx_bytes,
                       file_name=Path(uploaded.name).stem + "_exceptions.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.download_button("⬇️ Download CSV", out.csv_bytes,
                       file_name=Path(uploaded.name).stem + "_exceptions.csv", mime="text/csv")

    st.subheader("Flagged entries (ranked by risk)")
    if out.result.entries.empty:
        st.success("No exceptions flagged.")
    else:
        show = out.result.entries[["je_id", "risk_score", "tests_fired", "test_ids",
                                    "entry_date", "net_amount", "entered_by",
                                    "approved_by", "source", "reasons"]]
        st.dataframe(show, hide_index=True)

    if out.triage_md:
        st.subheader("Triage notes" + ("  ·  AI-generated" if out.used_ai else "  ·  local"))
        st.markdown(out.triage_md)

    st.caption("Exceptions are leads for auditor judgement, not findings. Investigate and document each.")
