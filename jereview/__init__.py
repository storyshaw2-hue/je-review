"""jereview — local, privacy-first journal-entry review & support validation."""

from .schema import ColumnMapping, normalize, NormalizationError
from .rules import RULES, RuleContext
from .engine import run, RunResult
from .review import (
    ReviewRecord, SupportItem, ReviewMemory, new_record, entry_signature,
    build_scorecard, DISPOSITIONS, SUPPORT_STATUSES, ASSERTIONS, ASSERTION_OUTCOMES,
    SUPPORT_TYPES,
)

__all__ = [
    "ColumnMapping", "normalize", "NormalizationError",
    "RULES", "RuleContext", "run", "RunResult",
    "ReviewRecord", "SupportItem", "ReviewMemory", "new_record", "entry_signature",
    "build_scorecard", "DISPOSITIONS", "SUPPORT_STATUSES", "ASSERTIONS",
    "ASSERTION_OUTCOMES", "SUPPORT_TYPES",
]
__version__ = "0.2.0"
