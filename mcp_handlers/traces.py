"""
Traces domain handler.

Tools for querying historical trace data from the observability system.

Tools:
- trace_summary: Aggregate stats across all traces
- trace_failures: Recent quality gate failures
- trace_compare: Compare two traces side-by-side
- traces_by_intent: Filter traces by intent type
- traces_by_domain: Filter traces by domain
- traces_flagged: Traces flagged for calibration review
"""

from typing import Optional

from pydantic import Field
from mcp.types import TextContent

from src.mcp.registry import tool, ToolInput
from src.mcp.response import ResponseBuilder, text_response
from src.tracing.query import TraceQuery, TraceResult
from src.utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# Input Models
# =============================================================================

class TraceSummaryInput(ToolInput):
    """Input for trace_summary tool."""
    pass


class TraceFailuresInput(ToolInput):
    """Input for trace_failures tool."""
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of failures to return"
    )


class TraceCompareInput(ToolInput):
    """Input for trace_compare tool."""
    trace_id_a: str = Field(description="First trace ID to compare")
    trace_id_b: str = Field(description="Second trace ID to compare")


class TracesByIntentInput(ToolInput):
    """Input for traces_by_intent tool."""
    intent: str = Field(description="Intent type: validating, exploring, comparing, monitoring")
    limit: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum number of traces to return"
    )


class TracesByDomainInput(ToolInput):
    """Input for traces_by_domain tool."""
    domain: str = Field(description="Domain name (e.g., edtech, robotics, fintech)")
    limit: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum number of traces to return"
    )


class TracesFlaggedInput(ToolInput):
    """Input for traces_flagged tool."""
    limit: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum number of flagged traces to return"
    )


# =============================================================================
# Helpers
# =============================================================================

def _format_trace_row(tr: TraceResult) -> str:
    """Format a single TraceResult as a compact text row."""
    score = f"{tr.overall_quality_score:.1f}" if tr.overall_quality_score is not None else "—"
    passed = "PASS" if tr.quality_gate_passed is True else "FAIL" if tr.quality_gate_passed is False else "—"
    duration = f"{tr.duration_seconds:.0f}s" if tr.duration_seconds is not None else "—"
    gaps = ", ".join(tr.gap_principles) if tr.gap_principles else "none"
    date_str = tr.started_at[:10] if tr.started_at else "—"
    return (
        f"  {tr.trace_id}  {date_str}  "
        f"Score: {score}  {passed}  "
        f"Duration: {duration}  "
        f"Gaps: {gaps}"
    )


# =============================================================================
# Handlers
# =============================================================================

@tool(
    name="trace_summary",
    description="Get aggregate stats across all trace runs: total runs, pass/fail counts, average quality, average cost.",
    domain="traces",
    input_model=TraceSummaryInput
)
async def trace_summary(args: TraceSummaryInput) -> list[TextContent]:
    """Get aggregate trace stats."""
    data = await TraceQuery.summary()

    builder = ResponseBuilder()
    builder.title("Trace Summary")
    builder.separator()
    builder.field("Total Runs", str(data["total_runs"]))
    builder.field("Complete", str(data["complete"]))
    builder.field("Failed", str(data["failed"]))
    builder.field("Incomplete", str(data["incomplete"]))
    builder.separator()
    builder.field("Quality Gate Passed", str(data["qg_passed"]))
    builder.field("Quality Gate Failed", str(data["qg_failed"]))
    builder.separator()

    avg_q = f"{data['avg_quality']:.2f}" if data["avg_quality"] is not None else "—"
    avg_d = f"{data['avg_duration']:.0f}s" if data["avg_duration"] is not None else "—"
    avg_c = f"${data['avg_cost']:.2f}" if data["avg_cost"] is not None else "—"

    builder.field("Avg Quality Score", avg_q)
    builder.field("Avg Duration", avg_d)
    builder.field("Avg Cost", avg_c)

    return builder.build()


@tool(
    name="trace_failures",
    description="Get recent quality gate failures with scores and gap principles. Sorted by most recent.",
    domain="traces",
    input_model=TraceFailuresInput
)
async def trace_failures(args: TraceFailuresInput) -> list[TextContent]:
    """Get recent quality gate failures."""
    results = await TraceQuery.quality_gate_failures(limit=args.limit)

    if not results:
        return text_response("No quality gate failures found.")

    builder = ResponseBuilder()
    builder.title(f"Quality Gate Failures ({len(results)} found)")
    builder.separator()

    for tr in results:
        score = f"{tr.overall_quality_score:.1f}" if tr.overall_quality_score is not None else "—"
        gaps = ", ".join(tr.gap_principles) if tr.gap_principles else "none"
        date_str = tr.started_at[:10] if tr.started_at else "—"
        project = tr.project_name or "—"

        builder.text(f"**{tr.trace_id}** ({date_str})")
        builder.bullet(f"Project: {project} | Score: {score}/3.0")
        builder.bullet(f"Intent: {tr.intent or '—'} | Domain: {tr.domain or '—'}")
        builder.bullet(f"Gap Principles: {gaps}")
        builder.text("")

    return builder.build()


@tool(
    name="trace_compare",
    description="Compare two traces side-by-side: quality delta, cost delta, duration delta, gap principle differences.",
    domain="traces",
    input_model=TraceCompareInput
)
async def trace_compare(args: TraceCompareInput) -> list[TextContent]:
    """Compare two traces."""
    data = await TraceQuery.compare(args.trace_id_a, args.trace_id_b)

    if "error" in data:
        return text_response(f"Error: {data['error']}")

    builder = ResponseBuilder()
    builder.title("Trace Comparison")
    builder.separator()
    builder.field("Trace A", data["trace_a"])
    builder.field("Trace B", data["trace_b"])
    builder.separator()

    # Quality delta
    if data["quality_delta"] is not None:
        direction = "+" if data["quality_delta"] > 0 else ""
        builder.field("Quality Delta (B-A)", f"{direction}{data['quality_delta']:.2f}")
    else:
        builder.field("Quality Delta", "—")

    # Duration delta
    if data["duration_delta"] is not None:
        direction = "+" if data["duration_delta"] > 0 else ""
        builder.field("Duration Delta (B-A)", f"{direction}{data['duration_delta']:.0f}s")
    else:
        builder.field("Duration Delta", "—")

    # Cost delta
    if data["cost_delta"] is not None:
        direction = "+" if data["cost_delta"] > 0 else ""
        builder.field("Cost Delta (B-A)", f"{direction}${data['cost_delta']:.2f}")
    else:
        builder.field("Cost Delta", "—")

    builder.separator()

    # Gap analysis
    if data["gaps_a_only"]:
        builder.field("Gaps only in A", ", ".join(data["gaps_a_only"]))
    if data["gaps_b_only"]:
        builder.field("Gaps only in B", ", ".join(data["gaps_b_only"]))
    if data["gaps_both"]:
        builder.field("Gaps in both", ", ".join(data["gaps_both"]))
    if not data["gaps_a_only"] and not data["gaps_b_only"] and not data["gaps_both"]:
        builder.text("No gap principle data available.")

    return builder.build()


@tool(
    name="traces_by_intent",
    description="Filter traces by intent type (validating, exploring, comparing, monitoring). Shows quality scores and pass/fail.",
    domain="traces",
    input_model=TracesByIntentInput
)
async def traces_by_intent(args: TracesByIntentInput) -> list[TextContent]:
    """Filter traces by intent."""
    results = await TraceQuery.by_intent(args.intent, limit=args.limit)

    if not results:
        return text_response(f"No traces found for intent '{args.intent}'.")

    builder = ResponseBuilder()
    builder.title(f"Traces: intent={args.intent} ({len(results)} found)")
    builder.separator()

    for tr in results:
        builder.text(_format_trace_row(tr))

    return builder.build()


@tool(
    name="traces_by_domain",
    description="Filter traces by domain (edtech, robotics, fintech, etc.). Shows quality scores and pass/fail.",
    domain="traces",
    input_model=TracesByDomainInput
)
async def traces_by_domain(args: TracesByDomainInput) -> list[TextContent]:
    """Filter traces by domain."""
    results = await TraceQuery.by_domain(args.domain, limit=args.limit)

    if not results:
        return text_response(f"No traces found for domain '{args.domain}'.")

    builder = ResponseBuilder()
    builder.title(f"Traces: domain={args.domain} ({len(results)} found)")
    builder.separator()

    for tr in results:
        builder.text(_format_trace_row(tr))

    return builder.build()


@tool(
    name="traces_flagged",
    description="Get traces flagged for calibration review. These are runs where calibration patterns were detected.",
    domain="traces",
    input_model=TracesFlaggedInput
)
async def traces_flagged(args: TracesFlaggedInput) -> list[TextContent]:
    """Get flagged traces."""
    results = await TraceQuery.flagged_for_review(limit=args.limit)

    if not results:
        return text_response("No traces flagged for review.")

    builder = ResponseBuilder()
    builder.title(f"Flagged Traces ({len(results)} found)")
    builder.separator()

    for tr in results:
        score = f"{tr.overall_quality_score:.1f}" if tr.overall_quality_score is not None else "—"
        passed = "PASS" if tr.quality_gate_passed is True else "FAIL" if tr.quality_gate_passed is False else "—"
        date_str = tr.started_at[:10] if tr.started_at else "—"
        project = tr.project_name or "—"
        gaps = ", ".join(tr.gap_principles) if tr.gap_principles else "none"

        builder.text(f"**{tr.trace_id}** ({date_str})")
        builder.bullet(f"Project: {project} | Score: {score}/3.0 | {passed}")
        builder.bullet(f"Intent: {tr.intent or '—'} | Domain: {tr.domain or '—'}")
        builder.bullet(f"Gaps: {gaps}")
        builder.text("")

    return builder.build()
