"""ForgeCode coding-agent framework."""

__version__ = "0.8.70"

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
from .embed import config_profiles as config_profiles_embedded, provider_list as provider_list_embedded, provider_health as provider_health_embedded, config_policy as config_policy_embedded, invoke as invoke_embedded, rpc_describe as rpc_describe_embedded, login as login_embedded, session_open as session_open_embedded, session_run as session_run_embedded, session_inspect as session_inspect_embedded, session_status as session_status_embedded, session_events as session_events_embedded, session_result as session_result_embedded, session_wait as session_wait_embedded, session_list as session_list_embedded, session_tree as session_tree_embedded, session_cancel as session_cancel_embedded, session_pause as session_pause_embedded, session_resume as session_resume_embedded, session_approval as session_approval_embedded, stream as stream_embedded

__all__ = [
    "__version__", "DiffHunk", "ReviewArtifactError", "ReviewBuilder", "config_profiles_embedded", "provider_list_embedded", "provider_health_embedded", "config_policy_embedded", "invoke_embedded", "rpc_describe_embedded", "login_embedded", "session_open_embedded", "session_run_embedded", "session_inspect_embedded", "session_status_embedded", "session_events_embedded", "session_result_embedded", "session_wait_embedded", "session_list_embedded", "session_tree_embedded", "session_cancel_embedded", "session_pause_embedded", "session_resume_embedded", "session_approval_embedded", "stream_embedded",
    "ReviewError", "ReviewFinding", "ReviewReport", "SecurityCheckResult",
    "export_review", "import_review", "run_security_checks", "TrajectoryScore", "evaluate_events", "evaluate_session",
]
