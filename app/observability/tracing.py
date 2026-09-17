"""MLflow tracing for the query path, off unless it is asked for.

A trace is worth having because metrics say a query was slow or ungrounded and a trace says
which chunks came back and what the model was sent. That is also why it is off by default: a
span records the question, the retrieved text and the answer, so switching it on moves
restricted content into the tracking server, where it is no longer behind the group filter
that made the retrieval legal. Enabling it is a decision about the tracking server's access,
and belongs to whoever deploys, not to a default.

When it is on, spans are masked with the same detector that masks answers. Masking is not a
substitute for access control — it removes emails, phone numbers and card numbers, not the
paragraph from the restricted HR policy.

Tracing never fails a request: the tracking server is not a dependency of answering.
"""

import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.observability.logging import get_logger
from app.settings import Settings
from ingestion.pii import mask_pii

if TYPE_CHECKING:
    from mlflow.entities import LiveSpan

log = get_logger(__name__)

EXPERIMENT = "lakehouse-rag-serving"


def traced(span_type: str) -> Callable[[Any], Any]:
    """Marks a method as a span. A no-op at runtime while tracing is disabled.

    This is the only place the serving code imports MLflow, so the dependency stays in one
    module and the call sites read as an annotation rather than as instrumentation.
    """
    import mlflow

    return mlflow.trace(span_type=span_type)


def _mask(value: Any) -> Any:
    if isinstance(value, str):
        return mask_pii(value)
    if isinstance(value, dict):
        return {k: _mask(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_mask(v) for v in value]
    return value


def mask_span(span: "LiveSpan") -> None:
    """Masks PII in a span's inputs and outputs before it is exported."""
    if span.inputs:
        span.set_inputs(_mask(span.inputs))
    if span.outputs:
        span.set_outputs(_mask(span.outputs))


def configure_tracing(settings: Settings) -> bool:
    """Turns MLflow tracing on when configured. Returns whether it is active."""
    import mlflow

    if not settings.tracing_enabled:
        mlflow.tracing.disable()
        return False
    try:
        mlflow.set_tracking_uri(
            settings.mlflow_tracking_uri
            or os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
        )
        mlflow.set_experiment(EXPERIMENT)
        mlflow.tracing.configure(span_processors=[mask_span])
        mlflow.tracing.enable()
    except Exception as exc:
        log.warning("tracing.disabled", error=str(exc))
        mlflow.tracing.disable()
        return False
    log.info("tracing.enabled", experiment=EXPERIMENT)
    return True
