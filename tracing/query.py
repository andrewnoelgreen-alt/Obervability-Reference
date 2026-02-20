"""
TraceQuery API — pre-built calibration queries for the observability system.

Provides all queries needed for calibration analysis without writing SQL.

Usage:
    from src.tracing import TraceQuery

    # Find all runs for a specific intent
    results = await TraceQuery.by_intent("validating")

    # Find runs where a specific principle scored low
    results = await TraceQuery.low_scoring_principle("META-12", threshold=2)

    # Get system-wide summary
    summary = await TraceQuery.summary()
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.db.connection import fetch_all, fetch_one
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TraceResult:
    """Lightweight result from a trace query."""
    trace_id: str
    project_name: Optional[str] = None
    query: Optional[str] = None
    intent: Optional[str] = None
    domain: Optional[str] = None
    report_type: Optional[str] = None
    status: str = "unknown"
    quality_gate_passed: Optional[bool] = None
    overall_quality_score: Optional[float] = None
    gap_principles: Optional[List[str]] = None
    strength_principles: Optional[List[str]] = None
    duration_seconds: Optional[float] = None
    started_at: Optional[str] = None
    trace_file_path: Optional[str] = None


def _row_to_result(row) -> TraceResult:
    """Convert an asyncpg Record to a TraceResult."""
    return TraceResult(
        trace_id=row["trace_id"],
        project_name=row.get("project_name"),
        query=row.get("query"),
        intent=row.get("intent"),
        domain=row.get("domain"),
        report_type=row.get("report_type"),
        status=row.get("status", "unknown"),
        quality_gate_passed=row.get("quality_gate_passed"),
        overall_quality_score=row.get("overall_quality_score"),
        gap_principles=row.get("gap_principles"),
        strength_principles=row.get("strength_principles"),
        duration_seconds=row.get("duration_seconds"),
        started_at=str(row["started_at"]) if row.get("started_at") else None,
        trace_file_path=row.get("trace_file_path"),
    )


class TraceQuery:
    """
    Pre-built trace queries for calibration analysis.

    All methods are static and async. No SQL knowledge required —
    just call the method that matches what you want to find.
    """

    @staticmethod
    async def by_intent(intent: str, limit: int = 50) -> List[TraceResult]:
        """Find all complete runs for a specific intent type."""
        rows = await fetch_all(
            """
            SELECT t.*, p.name as project_name
            FROM traces t
            LEFT JOIN projects p ON p.id = t.project_id
            WHERE t.intent = $1 AND t.status = 'complete'
            ORDER BY t.started_at DESC
            LIMIT $2
            """,
            intent, limit,
        )
        return [_row_to_result(r) for r in rows]

    @staticmethod
    async def low_scoring_principle(
        principle_id: str, threshold: int = 2, limit: int = 50
    ) -> List[TraceResult]:
        """Find runs where a specific principle scored below threshold."""
        rows = await fetch_all(
            """
            SELECT t.*, p.name as project_name
            FROM traces t
            LEFT JOIN projects p ON p.id = t.project_id
            WHERE (t.principle_scores->>$1)::int < $2
              AND t.status = 'complete'
            ORDER BY t.started_at DESC
            LIMIT $3
            """,
            principle_id, threshold, limit,
        )
        return [_row_to_result(r) for r in rows]

    @staticmethod
    async def quality_gate_failures(limit: int = 50) -> List[TraceResult]:
        """Find all complete runs that failed the quality gate."""
        rows = await fetch_all(
            """
            SELECT t.*, p.name as project_name
            FROM traces t
            LEFT JOIN projects p ON p.id = t.project_id
            WHERE t.quality_gate_passed = FALSE AND t.status = 'complete'
            ORDER BY t.started_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [_row_to_result(r) for r in rows]

    @staticmethod
    async def by_domain(domain: str, limit: int = 50) -> List[TraceResult]:
        """Find all complete runs for a specific domain."""
        rows = await fetch_all(
            """
            SELECT t.*, p.name as project_name
            FROM traces t
            LEFT JOIN projects p ON p.id = t.project_id
            WHERE t.domain = $1 AND t.status = 'complete'
            ORDER BY t.started_at DESC
            LIMIT $2
            """,
            domain, limit,
        )
        return [_row_to_result(r) for r in rows]

    @staticmethod
    async def by_project(project_name: str, limit: int = 50) -> List[TraceResult]:
        """Find all complete runs for a specific project."""
        rows = await fetch_all(
            """
            SELECT t.*, p.name as project_name
            FROM traces t
            JOIN projects p ON p.id = t.project_id
            WHERE p.name = $1 AND t.status = 'complete'
            ORDER BY t.started_at DESC
            LIMIT $2
            """,
            project_name, limit,
        )
        return [_row_to_result(r) for r in rows]

    @staticmethod
    async def principle_patterns(min_runs: int = 3) -> List[Dict[str, Any]]:
        """
        Find principles that frequently appear in gap_principles.

        Returns list of {"principle_id": str, "fail_count": int}
        sorted by fail_count descending.
        """
        rows = await fetch_all(
            """
            SELECT
                unnest(gap_principles) as principle_id,
                COUNT(*) as fail_count
            FROM traces
            WHERE status = 'complete' AND gap_principles IS NOT NULL
            GROUP BY principle_id
            HAVING COUNT(*) >= $1
            ORDER BY fail_count DESC
            """,
            min_runs,
        )
        return [
            {"principle_id": r["principle_id"], "fail_count": r["fail_count"]}
            for r in rows
        ]

    @staticmethod
    async def compare(trace_id_a: str, trace_id_b: str) -> Dict[str, Any]:
        """
        Compare two traces side-by-side.

        Returns dict with quality_delta, duration_delta, cost_delta,
        gaps_a_only, gaps_b_only, gaps_both.
        """
        row_a = await fetch_one(
            """
            SELECT t.*, p.name as project_name
            FROM traces t
            LEFT JOIN projects p ON p.id = t.project_id
            WHERE t.trace_id = $1
            """,
            trace_id_a,
        )
        row_b = await fetch_one(
            """
            SELECT t.*, p.name as project_name
            FROM traces t
            LEFT JOIN projects p ON p.id = t.project_id
            WHERE t.trace_id = $1
            """,
            trace_id_b,
        )

        if not row_a or not row_b:
            return {"error": "One or both traces not found"}

        score_a = row_a.get("overall_quality_score")
        score_b = row_b.get("overall_quality_score")
        dur_a = row_a.get("duration_seconds")
        dur_b = row_b.get("duration_seconds")
        cost_a = row_a.get("synthesis_cost_usd")
        cost_b = row_b.get("synthesis_cost_usd")
        gaps_a = set(row_a.get("gap_principles") or [])
        gaps_b = set(row_b.get("gap_principles") or [])

        return {
            "trace_a": trace_id_a,
            "trace_b": trace_id_b,
            "quality_delta": (score_b - score_a) if score_a is not None and score_b is not None else None,
            "duration_delta": (dur_b - dur_a) if dur_a is not None and dur_b is not None else None,
            "cost_delta": (cost_b - cost_a) if cost_a is not None and cost_b is not None else None,
            "gaps_a_only": sorted(gaps_a - gaps_b),
            "gaps_b_only": sorted(gaps_b - gaps_a),
            "gaps_both": sorted(gaps_a & gaps_b),
        }

    @staticmethod
    async def full_trace(trace_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the full trace JSON for a given trace_id.

        Looks up trace_file_path in Supabase, reads the JSON file.
        Returns None if trace not found or file missing.
        """
        row = await fetch_one(
            "SELECT trace_file_path FROM traces WHERE trace_id = $1",
            trace_id,
        )

        if not row or not row.get("trace_file_path"):
            return None

        file_path = Path(row["trace_file_path"])
        if not file_path.exists():
            logger.warning(f"Trace file not found: {file_path}")
            return None

        try:
            with open(file_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to read trace file {file_path}: {e}")
            return None

    @staticmethod
    async def flagged_for_review(limit: int = 20) -> List[TraceResult]:
        """Find all traces flagged for calibration review."""
        rows = await fetch_all(
            """
            SELECT t.*, p.name as project_name
            FROM traces t
            LEFT JOIN projects p ON p.id = t.project_id
            WHERE t.flagged_for_review = TRUE
            ORDER BY t.started_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [_row_to_result(r) for r in rows]

    @staticmethod
    async def summary() -> Dict[str, Any]:
        """
        Get aggregate summary across all traces.

        Returns: total_runs, complete, failed, incomplete,
        qg_passed, qg_failed, avg_quality, avg_duration, avg_cost.
        """
        row = await fetch_one(
            """
            SELECT
                COUNT(*) as total_runs,
                COUNT(*) FILTER (WHERE status = 'complete') as complete,
                COUNT(*) FILTER (WHERE status = 'failed') as failed,
                COUNT(*) FILTER (WHERE status = 'incomplete') as incomplete,
                COUNT(*) FILTER (WHERE quality_gate_passed = TRUE) as qg_passed,
                COUNT(*) FILTER (WHERE quality_gate_passed = FALSE) as qg_failed,
                AVG(overall_quality_score) FILTER (WHERE status = 'complete') as avg_quality,
                AVG(duration_seconds) FILTER (WHERE status = 'complete') as avg_duration,
                AVG(synthesis_cost_usd) FILTER (WHERE status = 'complete') as avg_cost
            FROM traces
            """
        )

        if not row:
            return {
                "total_runs": 0, "complete": 0, "failed": 0, "incomplete": 0,
                "qg_passed": 0, "qg_failed": 0,
                "avg_quality": None, "avg_duration": None, "avg_cost": None,
            }

        return {
            "total_runs": row["total_runs"],
            "complete": row["complete"],
            "failed": row["failed"],
            "incomplete": row["incomplete"],
            "qg_passed": row["qg_passed"],
            "qg_failed": row["qg_failed"],
            "avg_quality": float(row["avg_quality"]) if row["avg_quality"] is not None else None,
            "avg_duration": float(row["avg_duration"]) if row["avg_duration"] is not None else None,
            "avg_cost": float(row["avg_cost"]) if row["avg_cost"] is not None else None,
        }
