"""
review.py — the support-review data model for JE Review.

This is the layer that turns the automated triage (rules engine) into a
human-driven *correctness* review. The engine surfaces entries to look at;
this module records what the reviewer concluded and whether the support they
looked at agrees with the entry's amount / date / account / description.

Privacy-first: NO support-file binaries are stored here. Only metadata
(filename/label, type, what it covers) and the reviewer's conclusions. The
whole review state serialises to a single local JSON ("review memory") the
user exports and re-imports — there is no server.

Imports stdlib only, so it loads cleanly in the in-browser (Pyodide) build.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field, asdict
from datetime import date

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

# Entry outcome chosen by the reviewer.
DISPOSITIONS = [
    "Open",
    "Recorded correctly",
    "Expected business activity",
    "Needs support / explanation",
    "Needs correction",
    "False positive",
    "Escalate",
]
DISPOSITION_DEFAULT = "Open"

# Lifecycle of the supporting documentation for an entry.
SUPPORT_STATUSES = [
    "Not requested",
    "Requested",
    "Received",
    "Under review",
    "Reviewed - supports",
    "Reviewed - partial/discrepancy",
    "Reviewed - not supported",
    "No support required",
]
SUPPORT_STATUS_DEFAULT = "Not requested"

# The four assertions support is tested against, and the per-assertion verdict.
ASSERTIONS = ["amount", "date", "account", "description"]
ASSERTION_OUTCOMES = ["Not tested", "Agrees", "Discrepancy", "Not in support"]
ASSERTION_DEFAULT = "Not tested"

# Kinds of support a reviewer might attach (label only — no file is stored).
SUPPORT_TYPES = [
    "Invoice",
    "Contract",
    "Purchase order",
    "Bank statement",
    "Receipt",
    "Email / Approval",
    "Calculation / Schedule",
    "System report",
    "Other",
]

# Outcome groupings used by the scorecard / coverage maths.
PENDING_DISPOSITIONS = {"Open", "Needs support / explanation"}
ACCEPTED_DISPOSITIONS = {"Recorded correctly", "Expected business activity", "False positive"}
ISSUE_DISPOSITIONS = {"Needs correction", "Escalate"}


def default_assertions() -> dict:
    return {a: ASSERTION_DEFAULT for a in ASSERTIONS}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SupportItem:
    """One piece of support attached to an entry. Metadata only."""
    label: str = ""                                  # filename or short description
    support_type: str = "Other"
    covers: list = field(default_factory=list)       # subset of ASSERTIONS
    agrees: str = ASSERTION_DEFAULT                   # this item's overall verdict
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SupportItem":
        d = d or {}
        return cls(
            label=str(d.get("label", "")),
            support_type=str(d.get("support_type", "Other")),
            covers=[a for a in (d.get("covers") or []) if a in ASSERTIONS],
            agrees=d.get("agrees", ASSERTION_DEFAULT) if d.get("agrees") in ASSERTION_OUTCOMES else ASSERTION_DEFAULT,
            note=str(d.get("note", "")),
        )


@dataclass
class ReviewRecord:
    """Everything a reviewer concludes about a single entry."""
    je_id: str
    signature: str = ""                              # recurrence key (cross-period)
    disposition: str = DISPOSITION_DEFAULT
    support_status: str = SUPPORT_STATUS_DEFAULT
    assertions: dict = field(default_factory=default_assertions)
    support_items: list = field(default_factory=list)  # list[SupportItem]
    correction: str = ""                             # proposed correct value / fix
    reviewer: str = ""
    review_date: str = ""                            # ISO date string
    conclusion: str = ""                             # free-text rationale

    # --- derived ---
    @property
    def is_reviewed(self) -> bool:
        return self.disposition != "Open"

    @property
    def is_resolved(self) -> bool:
        return self.disposition not in PENDING_DISPOSITIONS

    @property
    def support_reviewed(self) -> bool:
        return self.support_status.startswith("Reviewed") or self.support_status == "No support required"

    @property
    def discrepancies(self) -> list:
        return [a for a, v in self.assertions.items() if v == "Discrepancy"]

    def stamp(self, reviewer: str = "") -> None:
        """Record reviewer + today's date when a conclusion is set."""
        if reviewer:
            self.reviewer = reviewer
        if not self.review_date:
            self.review_date = date.today().isoformat()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["support_items"] = [si.to_dict() if isinstance(si, SupportItem) else SupportItem.from_dict(si).to_dict()
                              for si in self.support_items]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ReviewRecord":
        d = d or {}
        assertions = default_assertions()
        for a, v in (d.get("assertions") or {}).items():
            if a in ASSERTIONS and v in ASSERTION_OUTCOMES:
                assertions[a] = v
        rec = cls(
            je_id=str(d.get("je_id", "")),
            signature=str(d.get("signature", "")),
            disposition=d.get("disposition", DISPOSITION_DEFAULT) if d.get("disposition") in DISPOSITIONS else DISPOSITION_DEFAULT,
            support_status=d.get("support_status", SUPPORT_STATUS_DEFAULT) if d.get("support_status") in SUPPORT_STATUSES else SUPPORT_STATUS_DEFAULT,
            assertions=assertions,
            support_items=[SupportItem.from_dict(si) for si in (d.get("support_items") or [])],
            correction=str(d.get("correction", "")),
            reviewer=str(d.get("reviewer", "")),
            review_date=str(d.get("review_date", "")),
            conclusion=str(d.get("conclusion", "")),
        )
        return rec


def new_record(je_id: str, signature: str = "") -> ReviewRecord:
    return ReviewRecord(je_id=str(je_id), signature=signature)


# ---------------------------------------------------------------------------
# Recurrence signature  (recognise the same recurring entry across periods)
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z]+")


def _norm_desc(desc: str, keep: int = 4) -> str:
    """Lower-case, drop digits/punctuation, keep the first few word tokens —
    enough to identify a counterparty/purpose without overfitting to specifics."""
    toks = _WORD_RE.findall(str(desc or "").lower())
    stop = {"the", "and", "for", "of", "to", "inv", "invoice", "payment", "re", "ref"}
    toks = [t for t in toks if t not in stop]
    return " ".join(toks[:keep])


def _amount_bucket(amount) -> str:
    try:
        a = abs(float(amount or 0))
    except (TypeError, ValueError):
        return "na"
    if a < 1:
        return "0"
    return f"1e{int(math.floor(math.log10(a)))}"   # order of magnitude band


def entry_signature(accounts, description: str = "", preparer: str = "", amount=0) -> str:
    """Deterministic, explainable recurrence key. Same recurring entry (e.g.
    monthly depreciation, recurring rent) hashes the same across periods even
    though the JE ID changes."""
    if isinstance(accounts, (list, set, tuple)):
        acct = "+".join(sorted(str(a).strip() for a in accounts if str(a).strip()))
    else:
        acct = str(accounts or "").strip()
    basis = "|".join([acct, _norm_desc(description), str(preparer or "").strip().lower(), _amount_bucket(amount)])
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Review memory  (current state + cross-period history) and learning helpers
# ---------------------------------------------------------------------------

@dataclass
class ReviewMemory:
    version: int = 1
    records: dict = field(default_factory=dict)      # je_id -> ReviewRecord
    history: dict = field(default_factory=dict)      # signature -> list[dict]
    updated: str = ""

    # ---- (de)serialisation ----
    def to_json(self, indent: int = 2) -> str:
        payload = {
            "version": self.version,
            "updated": date.today().isoformat(),
            "records": {jid: rec.to_dict() for jid, rec in self.records.items()},
            "history": self.history,
        }
        return json.dumps(payload, indent=indent)

    @classmethod
    def from_json(cls, text: str) -> "ReviewMemory":
        try:
            d = json.loads(text) if isinstance(text, str) else dict(text or {})
        except (ValueError, TypeError):
            d = {}
        records = {jid: ReviewRecord.from_dict(rd) for jid, rd in (d.get("records") or {}).items()}
        history = {sig: list(rows) for sig, rows in (d.get("history") or {}).items()}
        return cls(version=int(d.get("version", 1)), records=records,
                   history=history, updated=str(d.get("updated", "")))

    # ---- learning ----
    def fold_resolved_into_history(self, period: str = "") -> int:
        """Append concluded records to the cross-period history (append-only).
        Returns how many were folded in."""
        n = 0
        for rec in self.records.values():
            if rec.is_resolved and rec.signature:
                rows = self.history.setdefault(rec.signature, [])
                stampn = {"je_id": rec.je_id, "period": period,
                          "disposition": rec.disposition,
                          "date": rec.review_date or date.today().isoformat()}
                if stampn not in rows:
                    rows.append(stampn)
                    n += 1
        self.updated = date.today().isoformat()
        return n

    def suggest_disposition(self, signature: str):
        """If this recurring entry was consistently concluded benign before,
        return (suggested_disposition, provenance). Never auto-applies."""
        rows = self.history.get(signature or "", [])
        accepted = [r for r in rows if r.get("disposition") in ACCEPTED_DISPOSITIONS]
        if not accepted:
            return None
        disps = {r["disposition"] for r in accepted}
        if len(disps) != 1:
            return None
        last = accepted[-1]
        prov = f"Previously '{last['disposition']}'"
        if last.get("period"):
            prov += f" in {last['period']}"
        prov += f" ({len(accepted)}x)"
        return last["disposition"], prov


def build_scorecard(records: dict, rule_ids_by_je: dict) -> dict:
    """How useful is each triage check? For every rule, count the resolved
    entries it surfaced that were accepted (benign) vs real issues, so noisy
    checks can be retuned or muted. Deterministic and explainable.

    records: {je_id -> ReviewRecord}; rule_ids_by_je: {je_id -> [rule_id,...]}.
    """
    score: dict[str, dict] = {}
    for jid, ids in rule_ids_by_je.items():
        rec = records.get(jid)
        disp = rec.disposition if rec else "Open"
        for rid in ids:
            s = score.setdefault(rid, {"surfaced": 0, "accepted": 0, "issue": 0, "pending": 0})
            s["surfaced"] += 1
            if disp in ACCEPTED_DISPOSITIONS:
                s["accepted"] += 1
            elif disp in ISSUE_DISPOSITIONS:
                s["issue"] += 1
            else:
                s["pending"] += 1
    for rid, s in score.items():
        decided = s["accepted"] + s["issue"]
        s["noise_rate"] = round(s["accepted"] / decided, 2) if decided else None
        s["hit_rate"] = round(s["issue"] / decided, 2) if decided else None
        if decided >= 5 and s["noise_rate"] is not None and s["noise_rate"] >= 0.8:
            s["recommendation"] = "Mostly benign — consider retuning or muting"
        elif s["issue"] > 0 and s["hit_rate"] is not None and s["hit_rate"] >= 0.5:
            s["recommendation"] = "High-value check"
        else:
            s["recommendation"] = ""
    return score
