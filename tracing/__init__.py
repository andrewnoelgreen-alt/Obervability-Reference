"""
IRE Observability — Tracing system for research pipeline.

Provides trace context, data models, and query API for
capturing decisions, reasoning, and outcomes across all
pipeline components.

Usage:
    from src.tracing import TraceContext, Trace

    # Start a trace for a research run
    trace = TraceContext.start(project_name="cobot", intent="validating")

    # Record decisions in any component
    trace = TraceContext.current()
    if trace:
        trace.record("rubric", "rubric_loaded", {"what": "21 principles", ...})

    # Finish and persist
    result = await TraceContext.finish(trace)
"""

from src.tracing.context import (
    TraceContext,
    Trace,
    Decision,
    StageTrace,
    traced_research,
)
from src.tracing.interface import (
    TracingComponent,
    validate_trace_schema,
    register_component,
    get_registered_components,
)
from src.tracing.query import TraceQuery, TraceResult
from src.tracing.calibration_flags import check_calibration_flags

__all__ = [
    "TraceContext",
    "Trace",
    "Decision",
    "StageTrace",
    "TracingComponent",
    "validate_trace_schema",
    "register_component",
    "get_registered_components",
    "TraceQuery",
    "TraceResult",
    "check_calibration_flags",
    "traced_research",
]
