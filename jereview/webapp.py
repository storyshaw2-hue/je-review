"""
webapp.py — Shared Streamlit UI body, used by both the native app and the
in-browser (stlite) build.

render(allow_ai): when allow_ai is False (the in-browser build) the AI-triage
option is hidden, so no network egress is possible and httpx is never imported.

Flow: a run is stored in session_state so the flagged table stays interactive
across reruns. The reviewer dispositions each entry in an editable table and
those dispositions flow straight into the downloaded workpaper — all locally.
Kept free of version-specific Streamlit kwargs (e.g. no use_container_width) so
it runs on the older Streamlit that ships inside stlite as well as a current
local install.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jereview.pipeline import run_pipeline, workpaper_bytes
from jereview.schema import NormalizationError

DISPOSITIONS = ["", "Confirmed exception", "Cleared – no issue", "Follow-up", "Not tested"]


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
    run_clicked = st.button("Run review", type="primary")

    # ---- run (only when the button is pressed) -> stash results in session ----
    if run_clicked:
        if uploaded is None:
            st.warning("Upload a journal-entry file first, then click Run review.")
        else:
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

            st.session_state["jr_result"] = out.result
            st.session_state["jr_triage_md"] = out.triage_md
            st.session_state["jr_used_ai"] = out.used_ai
            st.session_state["jr_source"] = uploaded.name
            st.session_state.pop("jr_disp", None)   # fresh run clears prior dispositions

    if "jr_result" not in st.session_state:
        return

    result = st.session_state["jr_result"]
    triage_md = st.session_state.get("jr_triage_md")
    used_ai = st.session_state.get("jr_used_ai", False)
    source = st.session_state.get("jr_source", "upload")

    # ---- headline metrics ----
    s = result.stats
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Entries tested", s["total_entries"])
    c2.metric("Lines tested", s["total_lines"])
    c3.metric("Entries flagged", s["flagged_entries"])
    c4.metric("Flagged %", f"{s['flagged_pct']}%")

    if result.skipped_rules:
        st.info("Tests skipped (your export is missing the fields they need): "
                + ", ".join(result.skipped_rules))

    b = result.benford
    if b.get("mad") is not None:
        st.caption(f"Benford first-digit MAD {b['mad']} → {b['verdict']} (n={b['n']}).")

    # ---- flagged entries: review & disposition ----
    st.subheader("Flagged entries — review & disposition")
    dispositions: dict = {}
    if result.entries.empty:
        st.success("No exceptions flagged.")
    else:
        st.caption("Mark each entry and add a note. Your dispositions flow straight into the "
                   "workpaper you download below — nothing leaves your browser.")
        base = result.entries[["je_id", "risk_score", "tests_fired", "test_ids",
                               "entry_date", "net_amount", "entered_by", "reasons"]].copy()
        base["entry_date"] = base["entry_date"].apply(
            lambda v: pd.Timestamp(v).strftime("%Y-%m-%d") if pd.notna(v) else "")
        base["Disposition"] = ""
        base["Auditor note"] = ""
        prev = st.session_state.get("jr_disp")
        if prev is not None and not prev.empty:
            m = prev.set_index("je_id")
            base["Disposition"] = base["je_id"].map(m["Disposition"]).fillna("")
            base["Auditor note"] = base["je_id"].map(m["Auditor note"]).fillna("")

        readonly = [c for c in base.columns if c not in ("Disposition", "Auditor note")]
        try:
            cfg = {
                "Disposition": st.column_config.SelectboxColumn("Disposition", options=DISPOSITIONS),
                "Auditor note": st.column_config.TextColumn("Auditor note"),
                "reasons": st.column_config.TextColumn("Why flagged", width="large"),
            }
            edited = st.data_editor(base, hide_index=True, disabled=readonly, column_config=cfg)
        except Exception:  # older Streamlit without column_config
            edited = st.data_editor(base, hide_index=True)

        edited = edited.fillna("")
        st.session_state["jr_disp"] = edited[["je_id", "Disposition", "Auditor note"]].copy()
        done = int((edited["Disposition"].astype(str).str.len() > 0).sum())
        st.caption(f"{done} of {len(edited)} flagged entries dispositioned.")

        for _, rr in edited.iterrows():
            d = str(rr.get("Disposition", "")).strip()
            n = str(rr.get("Auditor note", "")).strip()
            if d or n:
                dispositions[str(rr["je_id"])] = {"disposition": d, "note": n}

    # ---- downloads (regenerated to include current dispositions) ----
    xlsx_bytes, csv_bytes = workpaper_bytes(result, source_name=source,
                                            triage_md=triage_md, dispositions=dispositions)
    d1, d2 = st.columns(2)
    d1.download_button("⬇️ Download Excel workpaper", xlsx_bytes,
                       file_name=Path(source).stem + "_exceptions.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    d2.download_button("⬇️ Download CSV", csv_bytes,
                       file_name=Path(source).stem + "_exceptions.csv", mime="text/csv")

    if triage_md:
        st.subheader("Triage notes" + ("  ·  AI-generated" if used_ai else "  ·  local"))
        st.markdown(triage_md)

    st.caption("Exceptions are leads for auditor judgement, not findings. Investigate and document each.")
