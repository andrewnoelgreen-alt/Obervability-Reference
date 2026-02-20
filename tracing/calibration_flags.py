"""
Calibration flags — auto-pattern detection for trace quality.

Analyzes completed traces and returns human-readable flag messages
when patterns suggest calibration attention is needed.

Flags are informational only — they don't block or modify research runs.

Usage:
    from src.tracing.calibration_flags import check_calibration_flags

    flags = await check_calibration_flags(trace)
    if flags:
        for msg in flags:
            print(f"[FLAG] {msg}")
"""

from typing import Any, Dict, List, Optional

from src.tracing.context import Trace
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def _recent_failure_count(principle_id: str, days: int = 7) -> int:
    """Count how many times a principle appeared in gap_principles in the last N days."""
    from src.db.connection import fetch_one

    row = await fetch_one(
        """
        SELECT COUNT(*) as cnt
        FROM traces
        WHERE $1 = ANY(gap_principles)
          AND started_at > NOW() - make_interval(days => $2)
          AND status = 'complete'
        """,
        principle_id, days,
    )
    return row["cnt"] if row else 0


async def _intent_avg_score(intent: str) -> Optional[float]:
    """Get average quality score for a specific intent."""
    from src.db.connection import fetch_one

    row = await fetch_one(
        """
        SELECT AVG(overall_quality_score) as avg_score
        FROM traces
        WHERE intent = $1 AND status = 'complete' AND overall_quality_score IS NOT NULL
        """,
        intent,
    )
    if row and row["avg_score"] is not None:
        return float(row["avg_score"])
    return None


async def _domain_avg_score(domain: str) -> Optional[float]:
    """Get average quality score for a specific domain."""
    from src.db.connection import fetch_one

    row = await fetch_one(
        """
        SELECT AVG(overall_quality_score) as avg_score
        FROM traces
        WHERE domain = $1 AND status = 'complete' AND overall_quality_score IS NOT NULL
        """,
        domain,
    )
    if row and row["avg_score"] is not None:
        return float(row["avg_score"])
    return None


async def _overall_avg_score() -> Optional[float]:
    """Get overall average quality score across all complete traces."""
    from src.db.connection import fetch_one

    row = await fetch_one(
        """
        SELECT AVG(overall_quality_score) as avg_score
        FROM traces
        WHERE status = 'complete' AND overall_quality_score IS NOT NULL
        """
    )
    if row and row["avg_score"] is not None:
        return float(row["avg_score"])
    return None


async def _previous_trace_for_project(project_id: str, before: str) -> Optional[Dict[str, Any]]:
    """Get the most recent complete trace for a project before a given timestamp."""
    from src.db.connection import fetch_one
    from datetime import datetime

    try:
        before_dt = datetime.fromisoformat(before)
    except (ValueError, TypeError):
        return None

    row = await fetch_one(
        """
        SELECT trace_id, quality_gate_passed, overall_quality_score
        FROM traces
        WHERE project_id = $1::uuid AND status = 'complete' AND started_at < $2
        ORDER BY started_at DESC
        LIMIT 1
        """,
        project_id, before_dt,
    )
    if row:
        return dict(row)
    return None


async def check_calibration_flags(trace: Trace) -> List[str]:
    """
    Analyze a completed trace for calibration-worthy patterns.

    Returns list of human-readable flag messages. Empty list means no flags.
    Never raises — all DB errors are caught and logged.

    Args:
        trace: A completed Trace object

    Returns:
        List of flag message strings
    """
    flags: List[str] = []

    # Skip if no quality gate stage
    qg_stage = trace.stages.get("quality_gate")
    if not qg_stage:
        return flags

    qg_outputs = qg_stage.outputs or {}

    # --- Check 1: Repeated principle failures ---
    gap_principles = qg_outputs.get("gap_principles") or []
    # Also check if principle_scores has low scores
    principle_scores = qg_outputs.get("principle_scores")
    if isinstance(principle_scores, list):
        for item in principle_scores:
            if isinstance(item, dict) and item.get("score", 3) < 2:
                pid = item.get("id")
                if pid and pid not in gap_principles:
                    gap_principles.append(pid)
    elif isinstance(principle_scores, dict):
        for pid, score in principle_scores.items():
            if score < 2 and pid not in gap_principles:
                gap_principles.append(pid)

    for principle_id in gap_principles:
        try:
            count = await _recent_failure_count(principle_id)
            if count >= 3:
                flags.append(
                    f"Principle {principle_id} has scored below threshold "
                    f"{count} times in the last 7 days. Consider reviewing calibration."
                )
        except Exception as e:
            logger.warning(f"Failed to check failure count for {principle_id}: {e}")

    # --- Check 2: Intent quality disparity ---
    if trace.intent:
        try:
            intent_avg = await _intent_avg_score(trace.intent)
            overall_avg = await _overall_avg_score()
            if intent_avg is not None and overall_avg is not None:
                if overall_avg - intent_avg > 0.5:
                    flags.append(
                        f"{trace.intent} intent runs average {intent_avg:.1f} quality "
                        f"vs {overall_avg:.1f} overall. May need intent-specific tuning."
                    )
        except Exception as e:
            logger.warning(f"Failed to check intent disparity: {e}")

    # --- Check 3: Domain quality disparity ---
    if trace.domain:
        try:
            domain_avg = await _domain_avg_score(trace.domain)
            overall_avg = await _overall_avg_score()
            if domain_avg is not None and overall_avg is not None:
                if overall_avg - domain_avg > 0.5:
                    flags.append(
                        f"{trace.domain} domain runs average {domain_avg:.1f} quality "
                        f"vs {overall_avg:.1f} overall."
                    )
        except Exception as e:
            logger.warning(f"Failed to check domain disparity: {e}")

    # --- Check 4: Quality gate regression ---
    qg_passed = qg_outputs.get("passed")
    if qg_passed is False and trace.project_id and trace.started_at:
        try:
            prev = await _previous_trace_for_project(trace.project_id, trace.started_at)
            if prev and prev.get("quality_gate_passed") is True:
                flags.append(
                    f"Quality regression detected for project "
                    f"{trace.project_name or trace.project_id}: "
                    f"this run failed quality gate after previous run passed."
                )
        except Exception as e:
            logger.warning(f"Failed to check quality regression: {e}")

    return flags
