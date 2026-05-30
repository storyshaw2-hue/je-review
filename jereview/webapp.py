"""
webapp.py — Shared Streamlit UI for both the native app and the in-browser
(stlite) build.

The product is a JE *review* tool: an automated first-pass screen surfaces
entries to look at, then a reviewer confirms whether each was recorded
correctly using support. Everything runs locally; no ledger or support data
leaves the machine. Support files are never uploaded or stored — the reviewer
records support *metadata and conclusions* only.

render(allow_ai): when False (the in-browser build) the AI option is hidden, so
there is no network egress. Kept free of version-specific Streamlit kwargs so it
runs on the older Streamlit bundled in stlite as well as a current local install.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jereview.pipeline import run_pipeline, workpaper_bytes
from jereview.schema import NormalizationError
from jereview.review import (
    ReviewRecord, SupportItem, ReviewMemory, entry_signature, build_scorecard,
    DISPOSITIONS, SUPPORT_STATUSES, ASSERTION_OUTCOMES, SUPPORT_TYPES, ASSERTIONS,
)

# review-table columns (scalar fields, one row per surfaced entry)
SCALAR_COLS = ["je_id", "Disposition", "Support status",
               "Amount?", "Date?", "Account?", "Description?",
               "Correction", "Reviewer", "Conclusion"]
ASSERT_LABELS = [("Amount?", "amount"), ("Date?", "date"),
                 ("Account?", "account"), ("Description?", "description")]
# support-items table columns (dynamic, many per entry)
SUP_COLS = ["JE ID", "Support", "Type", "Covers", "Agrees?", "Note"]


def render(allow_ai: bool = True) -> None:
    st.set_page_config(
        page_title="JE Review & Support Validation",
        page_icon="🧾",
        layout="wide",
        menu_items={
            "Get Help": "https://github.com/storyshaw2-hue/je-review",
            "Report a bug": "https://github.com/storyshaw2-hue/je-review/issues",
            "About": (
                "**JE Risk Review** — a privacy-first journal-entry review tool. "
                "All processing happens in your browser; nothing is uploaded. "
                "Built by Story Shaw · v0.2.0 · "
                "[GitHub](https://github.com/storyshaw2-hue/je-review)"
            ),
        },
    )
    # Hide Streamlit's default chrome (header, footer, deploy button) for a cleaner product feel
    st.markdown(
        """
        <style>
          #MainMenu { visibility: visible; }
          header[data-testid="stHeader"] { background: transparent; }
          footer { visibility: hidden; }
          .stDeployButton { display: none; }
          div[data-testid="stToolbar"] { right: 8px; }
          /* tighten top padding so the header is closer to the top */
          .block-container { padding-top: 2rem; }
          /* custom footer */
          .je-footer {
            position: fixed; bottom: 0; left: 0; right: 0;
            background: #F5F7FB; border-top: 1px solid #E5E9F0;
            padding: 8px 16px; font-size: 12px; color: #555;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
            display: flex; justify-content: space-between; z-index: 100;
          }
          .je-footer a { color: #1F3864; text-decoration: none; }
          .je-footer a:hover { text-decoration: underline; }
          /* make sample-data callout pop */
          .stAlert[data-baseweb="notification"] { border-radius: 8px; }
        </style>
        <div class="je-footer">
          <span>🔒 All data stays in your browser — nothing is uploaded</span>
          <span>Built by <a href="https://github.com/storyshaw2-hue" target="_blank">Story Shaw</a> · v0.2.0 · <a href="https://github.com/storyshaw2-hue/je-review" target="_blank">Source on GitHub</a></span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.title("🧾 Journal Entry Review")
    st.caption("Review journal entries and confirm they were recorded correctly using supporting documentation.")
    if allow_ai:
        st.caption("Runs on this machine. Nothing leaves it unless you turn on AI triage below.")
    else:
        st.caption("Runs entirely in your browser. Your ledger and support never leave this computer.")

    with st.sidebar:
        st.header("Review Settings")
        threshold = st.number_input("Approval threshold ($)", min_value=0, value=50000, step=1000,
                                    help="Used by the 'just below threshold' check.")
        st.divider()
        triage_choices = ["None", "Local (no data leaves)"]
        if allow_ai:
            triage_choices.append("AI-enhanced (sends surfaced rows)")
        triage_mode = st.radio("Triage notes", triage_choices, index=1)
        top_n = st.slider("Triage how many top entries", 1, 25, 10)
        provider = model = api_key = None
        if allow_ai and triage_mode.startswith("AI"):
            st.warning("AI triage sends the **surfaced rows** (not the full ledger) to the provider "
                       "you choose. Leave off if any data is sensitive.")
            provider = st.selectbox("Provider", ["anthropic", "openai"])
            model = st.text_input("Model",
                                  value="claude-sonnet-4-20250514" if provider == "anthropic" else "gpt-4o")
            api_key = st.text_input("API key", type="password")
        st.divider()
        map_file = st.file_uploader("Column mapping (JSON, optional)", type=["json"],
                                    help="Only if your export's column names differ from the canonical schema.")
        coa_file = st.file_uploader("Chart of accounts (CSV/JSON, optional)", type=["csv", "json"],
                                    help="If provided, flags postings to accounts not on your chart of accounts.")
        mem_file = st.file_uploader("Review memory (JSON, optional)", type=["json"],
                                    help="A prior export. Restores in-progress reviews and powers "
                                         "'previously concluded' suggestions for recurring entries.")

    # ----- Onboarding callout: first-time users get a sample-data path -----
    if "sample_loaded" not in st.session_state:
        st.session_state.sample_loaded = False

    with st.expander("👋 First time here? Try the sample data", expanded=not st.session_state.sample_loaded):
        col_a, col_b = st.columns([3, 1])
        with col_a:
            st.markdown(
                "**No file? Click below to load a sample journal-entry export with 10+ planted errors** "
                "(IC reciprocity gaps, cutoff issues, wrong-account postings, premature revenue). "
                "You'll see exactly what the tool surfaces."
            )
            st.caption(
                "Sample includes: `sample_je_errors.csv` (the ledger) + an optional chart-of-accounts file. "
                "Real exports just need columns: `je_id`, `entry_date`, `account`, `amount` (or `debit`/`credit`)."
            )
        with col_b:
            if st.button("✨ Load sample", type="secondary", use_container_width=True):
                st.session_state.sample_loaded = True
                st.rerun()

    # Expected schema reference (collapsed by default)
    with st.expander("📋 Expected column schema"):
        st.markdown(
            """
            Your export should have these columns (case-insensitive, common synonyms auto-mapped):

            | Field | Required? | Notes |
            |---|---|---|
            | `je_id` | ✅ yes | Journal entry / document number (groups debit & credit lines) |
            | `entry_date` | ✅ yes | Date the entry was posted |
            | `account` | ✅ yes | GL account number |
            | `amount` | ⚠️ either | Signed amount (debit +, credit −) |
            | `debit` / `credit` | ⚠️ or both | Use instead of `amount` |
            | `account_name` | optional | GL account description |
            | `description` | optional | Line or header narrative |
            | `entered_by` | optional | Preparer / user who posted (powers segregation-of-duties check) |
            | `approved_by` | optional | Approver (powers SoD check) |
            | `posted_at` | optional | Full timestamp (powers off-hours check) |
            | `source` | optional | 'Manual' / 'System' / 'Auto' |

            **If your columns don't match these names**, upload a Column Mapping JSON in the sidebar. "
            [See example mapping](https://github.com/storyshaw2-hue/je-review/blob/main/mapping.example.json).
            """
        )

    uploaded = st.file_uploader(
        "Upload a journal-entry export (.csv or .xlsx)",
        type=["csv", "xlsx", "xlsm"],
        help="CSV, XLSX, or XLSM. Stays in your browser — not uploaded anywhere.",
    )

    # If user clicked sample, swap in the bundled sample file
    if st.session_state.sample_loaded and uploaded is None:
        try:
            from pathlib import Path as _Path
            sample_path = _Path(__file__).parent.parent / "sample_data" / "sample_je_errors.csv"
            if sample_path.exists():
                st.info(f"✨ Using sample data: `{sample_path.name}` — click **Run review** to see the output.")
                uploaded = sample_path.open("rb")
            else:
                st.warning("Sample data not found in this build. Upload your own file instead.")
        except Exception as e:  # noqa: BLE001
            st.warning(f"Could not load sample: {e}")

    run_clicked = st.button("Run review", type="primary")

    if run_clicked:
        if uploaded is None:
            st.warning("Upload a journal-entry file first, then click Run review. (Or click ‘Load sample’ above.)")
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

            coa = None
            if coa_file is not None:
                try:
                    if coa_file.name.lower().endswith(".json"):
                        cd = json.load(coa_file)
                        if isinstance(cd, dict):
                            cd = cd.get("accounts", list(cd.keys()))
                        coa = {str(x).strip() for x in cd if str(x).strip()}
                    else:
                        cdf = pd.read_csv(coa_file, dtype=str)
                        col = "account" if "account" in cdf.columns else cdf.columns[0]
                        coa = {str(x).strip() for x in cdf[col].dropna() if str(x).strip()}
                except Exception as e:  # noqa: BLE001
                    st.error(f"Could not read the chart of accounts: {e}")
                    return

            mode = {"None": "none", "Local (no data leaves)": "local",
                    "AI-enhanced (sends surfaced rows)": "ai"}[triage_mode]
            try:
                with st.spinner("Running first-pass checks…"):
                    out = run_pipeline(raw, mapping=mapping, threshold=float(threshold),
                                       triage=mode, top_n=top_n, provider=provider or "anthropic",
                                       model=model or "claude-sonnet-4-20250514",
                                       api_key=api_key or "", source_name=uploaded.name, coa=coa)
            except NormalizationError as e:
                st.error(f"Mapping problem: {e}")
                return

            st.session_state["jr_result"] = out.result
            st.session_state["jr_triage_md"] = out.triage_md
            st.session_state["jr_used_ai"] = out.used_ai
            st.session_state["jr_source"] = uploaded.name
            # fresh run: clear in-progress review tables
            st.session_state.pop("jr_review_scalar", None)
            st.session_state.pop("jr_support", None)

            # load review memory (history for suggestions + records to resume)
            mem_obj = ReviewMemory()
            if mem_file is not None:
                try:
                    mem_obj = ReviewMemory.from_json(mem_file.getvalue().decode("utf-8"))
                except Exception as e:  # noqa: BLE001
                    st.error(f"Could not read the review memory: {e}")
                    return
            st.session_state["jr_memory"] = mem_obj
            # resume any records whose je_id appears in this run
            ids_now = set(out.result.entries["je_id"])
            if mem_obj.records:
                srows, suprows = [], []
                for je, rec in mem_obj.records.items():
                    if je not in ids_now:
                        continue
                    row = {"je_id": je, "Disposition": rec.disposition,
                           "Support status": rec.support_status,
                           "Correction": rec.correction, "Reviewer": rec.reviewer,
                           "Conclusion": rec.conclusion}
                    for lbl, key in ASSERT_LABELS:
                        row[lbl] = rec.assertions.get(key, "Not tested")
                    srows.append(row)
                    for si in rec.support_items:
                        suprows.append({"JE ID": je, "Support": si.label, "Type": si.support_type,
                                        "Covers": ", ".join(si.covers), "Agrees?": si.agrees, "Note": si.note})
                if srows:
                    st.session_state["jr_review_scalar"] = pd.DataFrame(srows, columns=SCALAR_COLS)
                if suprows:
                    st.session_state["jr_support"] = pd.DataFrame(suprows, columns=SUP_COLS)

    if "jr_result" not in st.session_state:
        return

    result = st.session_state["jr_result"]
    triage_md = st.session_state.get("jr_triage_md")
    used_ai = st.session_state.get("jr_used_ai", False)
    source = st.session_state.get("jr_source", "upload")
    mem = st.session_state.get("jr_memory") or ReviewMemory()
    ent_all = result.entries
    period = result.stats.get("periods", "")

    # ---- headline metrics ----
    s = result.stats
    store0 = st.session_state.get("jr_review_scalar")
    reviewed_n = int((~store0["Disposition"].astype(str).isin(["", "Open"])).sum()) \
        if store0 is not None and not store0.empty else 0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Entries", s["total_entries"])
    c2.metric("Lines", s["total_lines"])
    c3.metric("Surfaced for review", s["flagged_entries"])
    c4.metric("Reviewed", f"{reviewed_n} of {s['flagged_entries']}")
    a1, a2 = st.columns(2)
    a1.metric("Likely recording errors", s.get("accuracy_entries", 0),
              help="Entries with a likely recording error (accuracy checks).")
    a2.metric("Anomalies to confirm", s.get("risk_entries", 0),
              help="Entries surfaced as unusual (risk checks) — confirm with support.")

    if result.skipped_rules:
        st.info("Checks skipped (your export is missing the fields they need): "
                + ", ".join(result.skipped_rules))
    b = result.benford
    if b.get("mad") is not None:
        st.caption(f"Benford first-digit MAD {b['mad']} → {b['verdict']} (n={b['n']}).")

    # ---- signatures + recurrence suggestions ----
    sig_by_je, suggestion_by_je = {}, {}
    for r in ent_all.itertuples():
        sig = entry_signature(getattr(r, "accounts", ""), getattr(r, "description", ""),
                              getattr(r, "entered_by", ""), getattr(r, "net_amount", 0))
        sig_by_je[r.je_id] = sig
        sug = mem.suggest_disposition(sig)
        if sug:
            suggestion_by_je[r.je_id] = f"{sug[0]} — {sug[1]}"

    # ---- review queue ----
    st.subheader("Review queue — confirm each entry was recorded correctly")
    if ent_all.empty:
        st.success("Nothing surfaced. No entries need review.")
        reviews: dict = {}
    else:
        st.caption("The checks below only *surface* entries. You decide if each was recorded correctly. "
                   "Set a disposition, record whether support agrees on amount / date / account / "
                   "description, and attach support in the table underneath. Nothing leaves your browser.")
        if suggestion_by_je:
            st.info(f"{len(suggestion_by_je)} entr{'y' if len(suggestion_by_je)==1 else 'ies'} match a "
                    "previously concluded recurring entry — see the **Suggested** column. Confirm; never auto-applied.")

        view = st.radio("Show", ["All", "Likely errors", "Anomalies"], horizontal=True)
        ent = ent_all
        if view == "Likely errors":
            ent = ent[ent["flag_type"].isin(["Accuracy", "Both"])]
        elif view == "Anomalies":
            ent = ent[ent["flag_type"].isin(["Risk", "Both"])]

        base = ent[["je_id", "flag_type", "risk_score", "reasons"]].copy()
        base = base.rename(columns={"flag_type": "Flag type", "risk_score": "Priority",
                                    "reasons": "Why surfaced"})
        base["Suggested"] = base["je_id"].map(suggestion_by_je).fillna("")

        store = st.session_state.get("jr_review_scalar")
        if store is None or store.empty:
            store = pd.DataFrame(columns=SCALAR_COLS)
        smap = store.set_index("je_id") if not store.empty else None
        defaults = {"Disposition": "Open", "Support status": "Not requested",
                    "Amount?": "Not tested", "Date?": "Not tested", "Account?": "Not tested",
                    "Description?": "Not tested", "Correction": "", "Reviewer": "", "Conclusion": ""}
        for col, dflt in defaults.items():
            if smap is not None and col in smap.columns:
                base[col] = base["je_id"].map(smap[col]).fillna(dflt).replace("", dflt)
            else:
                base[col] = dflt

        readonly = ["je_id", "Flag type", "Priority", "Why surfaced", "Suggested"]
        try:
            cfg = {
                "Why surfaced": st.column_config.TextColumn("Why surfaced", width="large"),
                "Suggested": st.column_config.TextColumn("Suggested", width="medium"),
                "Disposition": st.column_config.SelectboxColumn("Disposition", options=DISPOSITIONS, required=True),
                "Support status": st.column_config.SelectboxColumn("Support status", options=SUPPORT_STATUSES, required=True),
                "Correction": st.column_config.TextColumn("Correction"),
                "Conclusion": st.column_config.TextColumn("Conclusion", width="large"),
            }
            for lbl, _ in ASSERT_LABELS:
                cfg[lbl] = st.column_config.SelectboxColumn(lbl, options=ASSERTION_OUTCOMES, required=True)
            edited = st.data_editor(base, hide_index=True, disabled=readonly, column_config=cfg, key="jr_editor")
        except Exception:  # older Streamlit
            edited = st.data_editor(base, hide_index=True)
        edited = edited.fillna("")

        # upsert visible edits into the cumulative scalar store
        sidx = store.set_index("je_id") if not store.empty else pd.DataFrame(columns=SCALAR_COLS[1:]).rename_axis("je_id")
        for _, rr in edited.iterrows():
            sidx.loc[str(rr["je_id"])] = {c: str(rr.get(c, "")) for c in SCALAR_COLS[1:]}
        store = sidx.reset_index()
        st.session_state["jr_review_scalar"] = store

        # ---- support items ----
        st.markdown("**Support attached** — one row per document. Metadata only; files are never uploaded.")
        sup = st.session_state.get("jr_support")
        if sup is None or sup.empty:
            sup = pd.DataFrame(columns=SUP_COLS)
        ids = [""] + list(ent_all["je_id"])
        try:
            supcfg = {
                "JE ID": st.column_config.SelectboxColumn("JE ID", options=ids, required=False),
                "Support": st.column_config.TextColumn("Support (filename/label)"),
                "Type": st.column_config.SelectboxColumn("Type", options=[""] + SUPPORT_TYPES),
                "Covers": st.column_config.TextColumn("Covers", help="e.g. amount, date, account, description"),
                "Agrees?": st.column_config.SelectboxColumn("Agrees?", options=[""] + ASSERTION_OUTCOMES),
                "Note": st.column_config.TextColumn("Note", width="large"),
            }
            edited_sup = st.data_editor(sup, num_rows="dynamic", hide_index=True,
                                        column_config=supcfg, key="jr_support_editor")
        except Exception:
            edited_sup = st.data_editor(sup, num_rows="dynamic", hide_index=True)
        edited_sup = edited_sup.fillna("")
        st.session_state["jr_support"] = edited_sup

        # ---- assemble ReviewRecords ----
        reviews = {}
        for _, row in store.iterrows():
            je = str(row["je_id"])
            rec = ReviewRecord(je_id=je, signature=sig_by_je.get(je, ""))
            rec.disposition = row.get("Disposition") or "Open"
            rec.support_status = row.get("Support status") or "Not requested"
            for lbl, key in ASSERT_LABELS:
                v = row.get(lbl) or "Not tested"
                rec.assertions[key] = v if v in ASSERTION_OUTCOMES else "Not tested"
            rec.correction = row.get("Correction", "")
            rec.reviewer = row.get("Reviewer", "")
            rec.conclusion = row.get("Conclusion", "")
            reviews[je] = rec
        for _, sr in edited_sup.iterrows():
            je = str(sr.get("JE ID", "")).strip()
            if not je:
                continue
            rec = reviews.setdefault(je, ReviewRecord(je_id=je, signature=sig_by_je.get(je, "")))
            covers = [c.strip() for c in str(sr.get("Covers", "")).replace(";", ",").split(",")
                      if c.strip() in ASSERTIONS]
            label = str(sr.get("Support", "")).strip()
            note = str(sr.get("Note", "")).strip()
            if not (label or note):
                continue
            rec.support_items.append(SupportItem(
                label=label, support_type=(str(sr.get("Type", "")).strip() or "Other"),
                covers=covers, agrees=(str(sr.get("Agrees?", "")).strip() or "Not tested"), note=note))
        for rec in reviews.values():
            if rec.disposition != "Open":
                rec.stamp(rec.reviewer)

        resolved = sum(1 for rc in reviews.values() if rc.is_resolved)
        st.caption(f"{resolved} of {len(ent_all)} surfaced entries concluded "
                   f"(disposition set to something other than Open / Needs support).")

    # ---- downloads ----
    sc = build_scorecard(reviews, {je: ids2.split(", ") for je, ids2 in
                                   zip(ent_all["je_id"], ent_all["test_ids"])}) if reviews else {}
    xlsx_bytes, csv_bytes = workpaper_bytes(result, source_name=source, triage_md=triage_md,
                                            reviews=reviews, scorecard=sc)
    stem = Path(source).stem
    d1, d2, d3 = st.columns(3)
    d1.download_button("⬇️ Excel workpaper", xlsx_bytes, file_name=stem + "_review.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    d2.download_button("⬇️ CSV", csv_bytes, file_name=stem + "_review.csv", mime="text/csv")
    # review memory export (records + folded history) for next period / to resume
    exp = ReviewMemory(history={k: list(v) for k, v in mem.history.items()})
    exp.records = reviews
    exp.fold_resolved_into_history(period=period)
    d3.download_button("⬇️ Save review memory", exp.to_json(), file_name=stem + "_review_memory.json",
                       mime="application/json",
                       help="Re-upload next period to resume and to suggest dispositions for recurring entries.")

    # ---- scorecard ----
    if sc and any(v.get("accepted", 0) or v.get("issue", 0) for v in sc.values()):
        with st.expander("Check signal — which first-pass checks are paying off"):
            rows = [{"Check": rid, "Surfaced": v["surfaced"], "Benign": v["accepted"],
                     "Issues": v["issue"], "Pending": v["pending"], "Signal": v["recommendation"]}
                    for rid, v in sorted(sc.items(), key=lambda kv: -kv[1]["surfaced"])]
            st.dataframe(pd.DataFrame(rows), hide_index=True)

    if triage_md:
        st.subheader("Triage notes" + ("  ·  AI-generated" if used_ai else "  ·  local"))
        st.markdown(triage_md)

    st.caption("The first-pass checks surface entries to review; they are not conclusions. "
               "A reviewer confirms correctness using support, all on this machine.")
