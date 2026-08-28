"""ForgeCode coding-agent framework."""

__version__ = "0.0.31"

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
from .embed import invoke as invoke_embedded, session_result as session_result_embedded, session_wait as session_wait_embedded, session_list as session_list_embedded, stream as stream_embedded

__all__ = [
    "__version__", "DiffHunk", "ReviewArtifactError", "ReviewBuilder", "invoke_embedded", "session_result_embedded", "session_wait_embedded", "session_list_embedded", "stream_embedded",
    "ReviewError", "ReviewFinding", "ReviewReport", "SecurityCheckResult",
    "export_review", "import_review", "run_security_checks", "TrajectoryScore", "evaluate_events", "evaluate_session",
]
