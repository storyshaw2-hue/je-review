"""jereview — local, privacy-preserving journal-entry risk testing for internal audit."""

from .schema import ColumnMapping, normalize, NormalizationError
from .rules import RULES, RuleContext
from .engine import run, RunResult

__all__ = [
    "ColumnMapping", "normalize", "NormalizationError",
    "RULES", "RuleContext", "run", "RunResult",
]
__version__ = "0.1.0"
