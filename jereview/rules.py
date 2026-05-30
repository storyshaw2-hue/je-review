"""
rules.py — The journal-entry risk test library.

Each rule is registered with @rule and receives the normalized line-level
DataFrame plus a RuleContext (config + which fields are available). It returns
a dict: {je_id: reason_string} for every entry it flags.

Rules are *risk indicators*, not conclusions. A flagged entry is an item for
auditor judgment; expect false positives. Weights drive a composite risk score
used only to rank items for triage.

Adding a rule = write a function and decorate it. Nothing else to wire.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

RULES: list["Rule"] = []


@dataclass
class Rule:
    id: str
    label: str
    scope: str          # 'entry' or 'line' (informational)
    weight: int         # contribution to composite risk score
    requires: list      # canonical fields needed; rule skipped if any missing
    description: str
    func: object

    def runnable(self, available: set) -> bool:
        return all(f in available for f in self.requires)


def rule(id, label, scope, weight, requires, description):
    def deco(fn):
        RULES.append(Rule(id, label, scope, weight, list(requires), description, fn))
        return fn
    return deco


@dataclass
class RuleContext:
    available: set
    # tunable thresholds (override via config)
    approval_threshold: float = 50_000.0
    below_threshold_band: float = 0.05      # within 5% under the threshold
    round_amount_min: float = 1_000.0       # only flag round amounts >= this
    seldom_account_max_entries: int = 1     # account used in <= N distinct entries
    large_outlier_pct: float = 0.99         # top 1% of |line amount|
    offhours_start: int = 20                # 8pm
    offhours_end: int = 6                   # 6am
    period_end_days: int = 3                # last N calendar days of the month
    materiality_floor: float = 0.0          # ignore amounts below this for outliers
    reversal_window_days: int = 5           # quick-reversal look-around window (days)
    rare_user_max_entries: int = 2          # user posting <= N entries counts as "rare"
    sensitive_account_keywords: list = field(default_factory=lambda: [
        "suspense", "clearing", "reserve", "accrual", "equity", "revenue",
        "intercompany", "ic ", "related party", "goodwill", "impairment",
        "deferred", "provision", "writeoff", "write-off", "topside", "top-side",
    ])
    suspicious_keywords: list = field(default_factory=lambda: [
        "plug", "to balance", "balancing", "reclass", "temp", "temporary",
        "misc", "miscellaneous", "correction", "adjust to", "true up", "true-up",
        "do not", "dummy", "reverse later", "write off to", "force",
        "per cfo", "per ceo", "per management", "per verbal", "as discussed",
    ])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry_dates(df: pd.DataFrame) -> pd.Series:
    """One entry_date per je_id (first non-null)."""
    return df.groupby("je_id")["entry_date"].first()


# ---------------------------------------------------------------------------
# TIMING rules
# ---------------------------------------------------------------------------

@rule("WEEKEND_POSTING", "Posted on a weekend", "entry", 2, ["entry_date"],
      "Entry recorded on a Saturday or Sunday — outside normal close cadence.")
def r_weekend(df, ctx):
    ed = _entry_dates(df).dropna()
    flagged = ed[ed.dt.dayofweek >= 5]
    return {je: f"Posted on {d:%A %Y-%m-%d}" for je, d in flagged.items()}


@rule("AFTER_PERIOD_END", "Posted after the period it affects", "entry", 3,
      ["entry_date", "period"],
      "Entry recorded after the close of the accounting period (possible back-dating).")
def r_after_period(df, ctx):
    out = {}
    g = df.groupby("je_id").agg(entry_date=("entry_date", "first"),
                                period=("period", "first"))
    for je, row in g.iterrows():
        try:
            pend = pd.Period(row["period"], freq="M").end_time
        except Exception:
            continue
        ed = row["entry_date"]
        if pd.notna(ed) and pd.notna(pend) and ed.normalize() > pend.normalize():
            out[je] = f"Period {row['period']} but posted {ed:%Y-%m-%d}"
    return out


@rule("PERIOD_END_CONCENTRATION", "Posted in the last days of the period", "entry", 1,
      ["entry_date"],
      "Entry recorded in the final days of the month — period-end pressure window.")
def r_period_end(df, ctx):
    ed = _entry_dates(df).dropna()
    days_in_month = ed.dt.daysinmonth
    flagged = ed[ed.dt.day > (days_in_month - ctx.period_end_days)]
    return {je: f"Posted {d:%Y-%m-%d} (last {ctx.period_end_days} days of month)"
            for je, d in flagged.items()}


@rule("OFFHOURS_POSTING", "Posted outside business hours", "entry", 1, ["posted_at"],
      "Entry timestamp is late night/early morning — unusual posting time.")
def r_offhours(df, ctx):
    ts = df.groupby("je_id")["posted_at"].first().dropna()
    hours = ts.dt.hour
    flagged = ts[(hours >= ctx.offhours_start) | (hours < ctx.offhours_end)]
    return {je: f"Posted at {t:%H:%M}" for je, t in flagged.items()}


# ---------------------------------------------------------------------------
# AMOUNT rules (line-level, rolled up to the entry)
# ---------------------------------------------------------------------------

@rule("ROUND_AMOUNT", "Round-dollar amount", "line", 1, ["amount"],
      "A line amount is an exact multiple of 1,000 — often estimates or plugs.")
def r_round(df, ctx):
    amt = df["amount"].abs()
    mask = (amt >= ctx.round_amount_min) & (amt % 1000 == 0)
    out = {}
    for je, sub in df[mask].groupby("je_id"):
        v = sub["amount"].abs().max()
        out[je] = f"Round amount ${v:,.0f}"
    return out


@rule("BELOW_THRESHOLD", "Just below an approval threshold", "line", 2, ["amount"],
      "A line amount sits just under a known approval limit — possible threshold gaming.")
def r_below_threshold(df, ctx):
    lo = ctx.approval_threshold * (1 - ctx.below_threshold_band)
    hi = ctx.approval_threshold
    amt = df["amount"].abs()
    mask = (amt >= lo) & (amt < hi)
    out = {}
    for je, sub in df[mask].groupby("je_id"):
        v = sub["amount"].abs().max()
        out[je] = f"${v:,.0f} is within {ctx.below_threshold_band:.0%} under the ${hi:,.0f} approval limit"
    return out


@rule("LARGE_OUTLIER", "Unusually large amount", "line", 1, ["amount"],
      "A line amount is in the top percentile of the population.")
def r_outlier(df, ctx):
    amt = df["amount"].abs()
    pool = amt[amt >= ctx.materiality_floor]
    if pool.empty:
        return {}
    cutoff = pool.quantile(ctx.large_outlier_pct)
    mask = amt >= cutoff
    out = {}
    for je, sub in df[mask].groupby("je_id"):
        v = sub["amount"].abs().max()
        out[je] = f"Large amount ${v:,.0f} (top {1-ctx.large_outlier_pct:.0%})"
    return out


# ---------------------------------------------------------------------------
# ACCOUNT rules
# ---------------------------------------------------------------------------

@rule("SELDOM_ACCOUNT", "Seldom-used account", "line", 2, ["account"],
      "An account touched by only one entry in the period — rarely-used accounts carry higher risk.")
def r_seldom(df, ctx):
    counts = df.groupby("account")["je_id"].nunique()
    rare = set(counts[counts <= ctx.seldom_account_max_entries].index)
    out = {}
    for je, sub in df[df["account"].isin(rare)].groupby("je_id"):
        accts = ", ".join(sorted(set(sub["account"].dropna())))
        out[je] = f"Seldom-used account(s): {accts}"
    return out


@rule("SENSITIVE_ACCOUNT", "Sensitive account", "line", 1, ["account_name"],
      "A line hits a higher-risk account class (suspense, reserve, equity, revenue, IC, etc.).")
def r_sensitive(df, ctx):
    names = df["account_name"].fillna("").str.lower()
    kw = ctx.sensitive_account_keywords
    mask = names.apply(lambda s: any(k in s for k in kw))
    out = {}
    for je, sub in df[mask].groupby("je_id"):
        hit = sub.loc[mask.loc[sub.index], "account_name"].dropna().iloc[0]
        out[je] = f"Sensitive account: {hit}"
    return out


# ---------------------------------------------------------------------------
# USER / SOURCE rules
# ---------------------------------------------------------------------------

@rule("MANUAL_SOURCE", "Manual entry", "entry", 1, ["source"],
      "Entry was posted manually rather than system-generated.")
def r_manual(df, ctx):
    src = df.groupby("je_id")["source"].first().fillna("").str.lower()
    flagged = src[src.str.contains("manual")]
    return {je: "Manual entry" for je in flagged.index}


@rule("SOD_CONFLICT", "Preparer is also approver", "entry", 3,
      ["entered_by", "approved_by"],
      "Same user prepared and approved the entry — segregation-of-duties conflict.")
def r_sod(df, ctx):
    g = df.groupby("je_id").agg(p=("entered_by", "first"), a=("approved_by", "first"))
    out = {}
    for je, row in g.iterrows():
        p, a = row["p"], row["a"]
        if pd.notna(p) and pd.notna(a) and str(p).strip() and str(p).strip() == str(a).strip():
            out[je] = f"Preparer = approver ({p})"
    return out


# ---------------------------------------------------------------------------
# TEXT rules
# ---------------------------------------------------------------------------

@rule("BLANK_DESCRIPTION", "Missing description", "entry", 2, ["description"],
      "Entry has no narrative — undocumented entries are harder to substantiate.")
def r_blank(df, ctx):
    desc = df.groupby("je_id")["description"].apply(
        lambda s: "".join([str(x) for x in s.dropna()]).strip())
    flagged = desc[desc.str.len() == 0]
    return {je: "No description provided" for je in flagged.index}


@rule("SUSPICIOUS_KEYWORD", "Suspicious description keyword", "line", 2, ["description"],
      "Description contains a flag word (plug, to balance, reclass, misc, correction, etc.).")
def r_keyword(df, ctx):
    import re
    desc = df["description"].fillna("").str.lower()
    pattern = re.compile(r"\b(" + "|".join(re.escape(k) for k in ctx.suspicious_keywords) + r")\b")
    def hit(s):
        m = pattern.search(s)
        return m.group(1) if m else None
    hits = desc.apply(hit)
    mask = hits.notna()
    out = {}
    for je, sub in df[mask].groupby("je_id"):
        k = hits.loc[sub.index].dropna().iloc[0]
        out[je] = f"Description contains '{k.strip()}'"
    return out


# ---------------------------------------------------------------------------
# STRUCTURAL rules
# ---------------------------------------------------------------------------

@rule("UNBALANCED_ENTRY", "Entry does not balance", "entry", 3, ["amount"],
      "Signed line amounts for the entry do not net to zero — debits ≠ credits.")
def r_unbalanced(df, ctx):
    net = df.groupby("je_id")["amount"].sum().round(2)
    flagged = net[net.abs() > 0.005]
    return {je: f"Out of balance by ${v:,.2f}" for je, v in flagged.items()}


# ---------------------------------------------------------------------------
# CROSS-ENTRY / POPULATION rules
# (these compare entries against each other, not just within one entry)
# ---------------------------------------------------------------------------

def _entry_signature(sub: pd.DataFrame) -> tuple:
    """A normalized (account, signed-amount) fingerprint for an entry's lines."""
    return tuple(sorted(
        (str(a), round(float(v), 2))
        for a, v in zip(sub["account"].fillna(""), sub["amount"].fillna(0.0))
    ))


@rule("DUPLICATE_ENTRY", "Possible duplicate entry", "entry", 3, ["account", "amount"],
      "Another entry posts the identical set of account/amount lines — possible duplicate "
      "posting or double-paid invoice.")
def r_duplicate(df, ctx):
    from collections import defaultdict
    groups = defaultdict(list)
    for je, sub in df.groupby("je_id"):
        sig = _entry_signature(sub)
        if sig and any(v != 0 for _, v in sig):
            groups[sig].append(je)
    out = {}
    for jes in groups.values():
        if len(jes) > 1:
            for je in jes:
                others = [x for x in jes if x != je]
                shown = ", ".join(sorted(others)[:3]) + ("…" if len(others) > 3 else "")
                n = len(others)
                out[je] = f"Identical account/amount lines to {n} other entr{'y' if n == 1 else 'ies'}: {shown}"
    return out


@rule("QUICK_REVERSAL", "Reversed shortly after posting", "entry", 2,
      ["entry_date", "account", "amount"],
      "An equal-and-opposite entry posts within a few days — possible window-dressing or a "
      "temporary booking that was backed out.")
def r_quick_reversal(df, ctx):
    from collections import defaultdict
    per = {}
    for je, sub in df.groupby("je_id"):
        sig = _entry_signature(sub)
        d = sub["entry_date"].dropna()
        per[je] = (sig, d.iloc[0] if len(d) else pd.NaT)
    by_sig = defaultdict(list)
    for je, (sig, d) in per.items():
        if sig:
            by_sig[sig].append((je, d))
    out = {}
    for je, (sig, d) in per.items():
        if not sig or pd.isna(d) or not any(v != 0 for _, v in sig):
            continue
        neg = tuple(sorted((a, round(-v, 2)) for a, v in sig))
        if neg == sig:
            continue
        for je2, d2 in by_sig.get(neg, []):
            if je2 == je or pd.isna(d2):
                continue
            delta = abs((d2 - d).days)
            if delta <= ctx.reversal_window_days:
                out[je] = f"Equal-and-opposite to {je2} ({delta} day(s) apart)"
                break
    return out


@rule("NUMBERING_GAP", "Gap in entry numbering", "entry", 1, ["je_id"],
      "A break in the journal-entry number sequence precedes this entry — possible deleted, "
      "voided, or unrecorded entries.")
def r_numbering_gap(df, ctx):
    import re
    from collections import defaultdict
    pat = re.compile(r"^(.*?)(\d+)\s*$")
    byprefix = defaultdict(set)
    for x in df["je_id"].dropna().astype(str).unique():
        m = pat.match(x.strip())
        if m:
            byprefix[m.group(1)].add((int(m.group(2)), x.strip()))
    out = {}
    for items in byprefix.values():
        items = sorted(items)
        if len(items) < 5:                       # need a real sequence to judge
            continue
        nums = [n for n, _ in items]
        span = nums[-1] - nums[0] + 1
        if span <= 0 or len(nums) / span < 0.7:  # only when mostly contiguous
            continue
        for i in range(1, len(items)):
            prev, cur = items[i - 1][0], items[i][0]
            if cur - prev > 1:
                missing = cur - prev - 1
                out[items[i][1]] = (f"{missing} entry number(s) missing before this entry "
                                    f"(…{prev} → …{cur})")
    return out


@rule("RARE_USER", "Posted by an infrequent user", "entry", 2, ["entered_by"],
      "Entry was posted by a user who rarely posts journal entries in this population — "
      "unfamiliar preparers warrant a closer look.")
def r_rare_user(df, ctx):
    by = df.groupby("je_id")["entered_by"].first().dropna()
    by = by[by.astype(str).str.strip() != ""]
    if by.empty:
        return {}
    counts = by.value_counts()
    rare_users = set(counts[counts <= ctx.rare_user_max_entries].index)
    rare_entries = by[by.isin(rare_users)]
    # guard: if "rare" users are actually a large share, the signal is meaningless
    if len(rare_entries) > 0.20 * len(by):
        return {}
    out = {}
    for je, u in rare_entries.items():
        c = int(counts[u])
        out[je] = f"Posted by infrequent user '{u}' ({c} entr{'y' if c == 1 else 'ies'} in file)"
    return out
