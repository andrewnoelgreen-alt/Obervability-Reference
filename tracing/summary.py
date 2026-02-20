"""
Post-run trace summary — compact terminal scorecard + detailed markdown file.

Three outputs:
1. format_compact_summary(trace) → 5-6 line terminal scorecard (default)
2. format_verbose_summary(trace) → full stage-by-stage terminal output
3. write_summary_file(trace) → detailed markdown to _traces/{trace_id}_summary.md

All functions are safe — exceptions are caught and logged, never propagated.
"""

from pathlib import Path
from typing import Optional

from src.config import BRAIN_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _fmt_duration(seconds: Optional[float]) -> str:
    """Format seconds into human-readable duration."""
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m {secs:.0f}s"


def _fmt_cost(cost: Optional[float]) -> str:
    """Format cost as USD."""
    if cost is None:
        return "—"
    return f"${cost:.2f}"


def _fmt_score(score: Optional[float]) -> str:
    """Format quality score."""
    if score is None:
        return "—"
    return f"{score:.1f}"


def _get_quality_data(trace) -> dict:
    """Extract quality gate data from trace."""
    qg = trace.stages.get("quality_gate")
    if not qg:
        return {"passed": None, "score": None, "gaps": [], "strengths": []}
    outputs = qg.outputs or {}
    return {
        "passed": outputs.get("passed"),
        "score": outputs.get("overall_score"),
        "gaps": outputs.get("gap_principles") or [],
        "strengths": outputs.get("strength_principles") or [],
    }


def _get_evidence_data(trace) -> dict:
    """Extract evidence counts from trace."""
    coll = trace.stages.get("collection")
    if not coll:
        return {"collected": None, "passed": None, "filtered": None}
    evidence = coll.evidence or {}
    outputs = coll.outputs or {}
    return {
        "collected": evidence.get("collected_count"),
        "passed": outputs.get("evidence_passed"),
        "filtered": outputs.get("evidence_filtered"),
    }


def _get_cost_data(trace) -> Optional[float]:
    """Extract synthesis cost from trace."""
    synth = trace.stages.get("synthesis")
    if not synth:
        return None
    return (synth.outputs or {}).get("cost_usd")


def format_compact_summary(trace) -> str:
    """
    Format a 5-6 line compact scorecard for terminal output.

    Example:
        ── Trace Summary ──────────────────────────
        Quality: 2.4/3.0  PASS    Duration: 1m 23s
        Cost: $0.32                Evidence: 28→18
        Gaps: META-12
        Trace: trc_20260213_143022_a1b2c3d4
        ────────────────────────────────────────────
    """
    qg = _get_quality_data(trace)
    ev = _get_evidence_data(trace)
    cost = _get_cost_data(trace)

    # Line 1: header
    lines = ["── Trace Summary ──────────────────────────"]

    # Line 2: quality + duration
    score_str = _fmt_score(qg["score"])
    if qg["passed"] is True:
        status = "PASS"
    elif qg["passed"] is False:
        status = "FAIL"
    else:
        status = "N/A"
    duration_str = _fmt_duration(trace.duration_seconds)
    lines.append(f"Quality: {score_str}/3.0  {status:<8}Duration: {duration_str}")

    # Line 3: cost + evidence
    cost_str = _fmt_cost(cost)
    if ev["collected"] is not None and ev["passed"] is not None:
        ev_str = f"{ev['collected']}\u2192{ev['passed']}"
    elif ev["collected"] is not None:
        ev_str = str(ev["collected"])
    else:
        ev_str = "\u2014"
    lines.append(f"Cost: {cost_str:<20}Evidence: {ev_str}")

    # Line 4: gaps (if any)
    if qg["gaps"]:
        lines.append(f"Gaps: {', '.join(qg['gaps'])}")

    # Line 5: trace id
    lines.append(f"Trace: {trace.trace_id}")

    # Line 6: footer
    lines.append("────────────────────────────────────────────")

    return "\n".join(lines)


def format_verbose_summary(trace) -> str:
    """
    Format full stage-by-stage breakdown for terminal output.

    Includes per-stage timings, principle scores, gap analysis,
    and token breakdown.
    """
    qg = _get_quality_data(trace)
    ev = _get_evidence_data(trace)
    cost = _get_cost_data(trace)

    lines = ["══ Trace Detail ═══════════════════════════════"]
    lines.append(f"Trace ID:  {trace.trace_id}")
    lines.append(f"Project:   {trace.project_name or '—'}")
    lines.append(f"Query:     {(trace.query or '—')[:80]}")
    lines.append(f"Intent:    {trace.intent or '—'}    Domain: {trace.domain or '—'}")
    lines.append(f"Status:    {trace.status}    Duration: {_fmt_duration(trace.duration_seconds)}")
    lines.append("")

    # Quality summary
    lines.append("── Quality Gate ───────────────────────────────")
    score_str = _fmt_score(qg["score"])
    if qg["passed"] is True:
        status = "PASS"
    elif qg["passed"] is False:
        status = "FAIL"
    else:
        status = "N/A"
    lines.append(f"Score: {score_str}/3.0  {status}")

    # Principle scores
    qg_stage = trace.stages.get("quality_gate")
    if qg_stage:
        raw_scores = (qg_stage.outputs or {}).get("principle_scores")
        if isinstance(raw_scores, list):
            lines.append("Principle Scores:")
            for item in raw_scores:
                if isinstance(item, dict):
                    pid = item.get("id", "?")
                    score = item.get("score", "?")
                    marker = " <gap" if pid in qg["gaps"] else ""
                    lines.append(f"  {pid}: {score}{marker}")
        elif isinstance(raw_scores, dict):
            lines.append("Principle Scores:")
            for pid, score in raw_scores.items():
                marker = " <gap" if pid in qg["gaps"] else ""
                lines.append(f"  {pid}: {score}{marker}")

    if qg["gaps"]:
        lines.append(f"Gap Principles: {', '.join(qg['gaps'])}")
    if qg["strengths"]:
        lines.append(f"Strengths: {', '.join(qg['strengths'])}")
    lines.append("")

    # Stage timings
    lines.append("── Stages ─────────────────────────────────────")
    for stage_name, stage in trace.stages.items():
        dur = _fmt_duration(stage.duration_seconds)
        decision_count = len(stage.decisions)
        lines.append(f"  {stage_name:<16} {dur:>8}  ({decision_count} decisions)")

    lines.append("")

    # Evidence
    lines.append("── Evidence ───────────────────────────────────")
    lines.append(f"Collected: {ev['collected'] or '—'}  Passed: {ev['passed'] or '—'}  Filtered: {ev['filtered'] or '—'}")
    lines.append("")

    # Synthesis / tokens
    synth = trace.stages.get("synthesis")
    if synth:
        outputs = synth.outputs or {}
        lines.append("── Synthesis ──────────────────────────────────")
        lines.append(f"Model: {outputs.get('model', '—')}")
        tokens = outputs.get("token_usage", {})
        if isinstance(tokens, dict):
            inp = tokens.get("input_tokens", "—")
            out = tokens.get("output_tokens", "—")
            lines.append(f"Tokens: {inp} in / {out} out")
        lines.append(f"Cost: {_fmt_cost(outputs.get('cost_usd'))}")
        lines.append("")

    lines.append("═══════════════════════════════════════════════")
    return "\n".join(lines)


def write_summary_file(trace) -> Optional[Path]:
    """
    Write detailed markdown summary to brain/projects/{project}/_traces/{trace_id}_summary.md.

    Returns the file path, or None if writing fails.
    """
    try:
        project_name = trace.project_name or "unknown"
        traces_dir = BRAIN_DIR / "projects" / project_name / "_traces"
        traces_dir.mkdir(parents=True, exist_ok=True)

        file_path = traces_dir / f"{trace.trace_id}_summary.md"

        qg = _get_quality_data(trace)
        ev = _get_evidence_data(trace)
        cost = _get_cost_data(trace)

        # Build markdown
        md = []
        md.append(f"# Trace Summary: {trace.trace_id}")
        md.append("")
        md.append(f"**Project:** {trace.project_name or '—'}")
        md.append(f"**Query:** {trace.query or '—'}")
        md.append(f"**Intent:** {trace.intent or '—'} | **Domain:** {trace.domain or '—'}")
        md.append(f"**Report Type:** {trace.report_type or '—'} | **Research Type:** {trace.research_type or '—'}")
        md.append(f"**Status:** {trace.status}")
        md.append(f"**Started:** {trace.started_at or '—'}")
        md.append(f"**Completed:** {trace.completed_at or '—'}")
        md.append(f"**Duration:** {_fmt_duration(trace.duration_seconds)}")
        md.append("")

        # Quality Gate
        md.append("## Quality Gate")
        md.append("")
        score_str = _fmt_score(qg["score"])
        if qg["passed"] is True:
            md.append(f"**Result:** PASS ({score_str}/3.0)")
        elif qg["passed"] is False:
            md.append(f"**Result:** FAIL ({score_str}/3.0)")
        else:
            md.append("**Result:** Not evaluated")

        if qg["gaps"]:
            md.append(f"**Gap Principles:** {', '.join(qg['gaps'])}")
        if qg["strengths"]:
            md.append(f"**Strength Principles:** {', '.join(qg['strengths'])}")

        # Principle scores table
        qg_stage = trace.stages.get("quality_gate")
        if qg_stage:
            raw_scores = (qg_stage.outputs or {}).get("principle_scores")
            if raw_scores:
                md.append("")
                md.append("| Principle | Score | Status |")
                md.append("|-----------|-------|--------|")
                if isinstance(raw_scores, list):
                    for item in raw_scores:
                        if isinstance(item, dict):
                            pid = item.get("id", "?")
                            score = item.get("score", "?")
                            status = "Gap" if pid in qg["gaps"] else "OK"
                            md.append(f"| {pid} | {score} | {status} |")
                elif isinstance(raw_scores, dict):
                    for pid, score in raw_scores.items():
                        status = "Gap" if pid in qg["gaps"] else "OK"
                        md.append(f"| {pid} | {score} | {status} |")
        md.append("")

        # Evidence
        md.append("## Evidence")
        md.append("")
        md.append(f"- **Collected:** {ev['collected'] or '—'}")
        md.append(f"- **Passed filter:** {ev['passed'] or '—'}")
        md.append(f"- **Filtered out:** {ev['filtered'] or '—'}")

        # Source breakdown
        coll = trace.stages.get("collection")
        if coll and coll.evidence:
            by_source = coll.evidence.get("by_source")
            if by_source and isinstance(by_source, dict):
                md.append("")
                md.append("**By Source:**")
                for source, count in by_source.items():
                    md.append(f"- {source}: {count}")
        md.append("")

        # Stage Breakdown
        md.append("## Stage Breakdown")
        md.append("")
        md.append("| Stage | Duration | Decisions |")
        md.append("|-------|----------|-----------|")
        for stage_name, stage in trace.stages.items():
            dur = _fmt_duration(stage.duration_seconds)
            decisions = len(stage.decisions)
            md.append(f"| {stage_name} | {dur} | {decisions} |")
        md.append("")

        # Decision Log
        md.append("## Decision Log")
        md.append("")
        for stage_name, stage in trace.stages.items():
            if stage.decisions:
                md.append(f"### {stage_name}")
                md.append("")
                for d in stage.decisions:
                    md.append(f"- **{d.decision}**: {d.what}")
                    if d.why:
                        md.append(f"  - Why: {d.why}")
                    if d.confidence < 1.0:
                        md.append(f"  - Confidence: {d.confidence:.0%}")
                md.append("")

        # Synthesis Details
        synth = trace.stages.get("synthesis")
        if synth:
            outputs = synth.outputs or {}
            md.append("## Synthesis")
            md.append("")
            md.append(f"- **Model:** {outputs.get('model', '—')}")
            tokens = outputs.get("token_usage", {})
            if isinstance(tokens, dict):
                md.append(f"- **Input tokens:** {tokens.get('input_tokens', '—')}")
                md.append(f"- **Output tokens:** {tokens.get('output_tokens', '—')}")
            md.append(f"- **Cost:** {_fmt_cost(outputs.get('cost_usd'))}")
            md.append("")

        # Iterations
        if trace.iterations:
            md.append("## Iterations")
            md.append("")
            md.append(f"- **Total iterations:** {trace.iteration_count}")
            md.append(f"- **Quality gate failures:** {trace.quality_gate_failures}")
            for i, iteration in enumerate(trace.iterations, 1):
                passed = iteration.get("passed", "?")
                md.append(f"- Iteration {i}: {'PASS' if passed else 'FAIL'}")
            md.append("")

        # Outputs
        if trace.outputs:
            md.append("## Outputs")
            md.append("")
            for key, val in trace.outputs.items():
                md.append(f"- **{key}:** {val}")
            md.append("")

        md.append("---")
        md.append(f"*Generated from trace {trace.trace_id}*")

        file_path.write_text("\n".join(md))
        logger.info(f"Trace summary written: {file_path}")
        return file_path

    except Exception as e:
        logger.error(f"Failed to write trace summary file: {e}")
        return None
