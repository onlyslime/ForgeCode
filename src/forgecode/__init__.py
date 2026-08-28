"""ForgeCode coding-agent framework."""

__version__ = "0.0.10"

# Public, provider-neutral review API.  Importing these types has no I/O and
# keeps CLI/application consumers independent from storage internals.
from .review import (
    DiffHunk,
    ReviewArtifactError,
    ReviewBuilder,
    ReviewError,
    ReviewFinding,
    ReviewReport,
    SecurityCheckResult,
    export_review,
    import_review,
    run_security_checks,
)
from .evaluation import TrajectoryScore, evaluate_events, evaluate_session

__all__ = [
    "__version__", "DiffHunk", "ReviewArtifactError", "ReviewBuilder",
    "ReviewError", "ReviewFinding", "ReviewReport", "SecurityCheckResult",
    "export_review", "import_review", "run_security_checks", "TrajectoryScore", "evaluate_events", "evaluate_session",
]
