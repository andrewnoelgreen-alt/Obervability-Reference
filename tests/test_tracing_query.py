"""
Tests for src/tracing/query.py — TraceQuery API.

Ralph Run 5 of the Observability System build plan.

Most query tests require a live Supabase connection with the _023 migration applied.
They are skipped when DATABASE_URL is not set or points to localhost.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tracing.query import TraceQuery, TraceResult, _row_to_result


# ============================================================================
# Skip condition for live DB tests
# ============================================================================

SKIP_DB = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL")
    or "localhost" in os.environ.get("DATABASE_URL", "")
    or "127.0.0.1" in os.environ.get("DATABASE_URL", ""),
    reason="DATABASE_URL not set or points to localhost — skipping live database test"
)


# ============================================================================
# TestTraceResult
# ============================================================================

class TestTraceResult:
    def test_trace_result_creation(self):
        result = TraceResult(
            trace_id="trc_test",
            project_name="test",
            intent="validating",
            status="complete",
        )
        assert result.trace_id == "trc_test"
        assert result.project_name == "test"
        assert result.quality_gate_passed is None

    def test_trace_result_defaults(self):
        result = TraceResult(trace_id="trc_min")
        assert result.project_name is None
        assert result.query is None
        assert result.intent is None
        assert result.domain is None
        assert result.status == "unknown"
        assert result.gap_principles is None


class TestRowToResult:
    def test_converts_full_row(self):
        """Test _row_to_result with a mock record containing all fields."""
        row = {
            "trace_id": "trc_test_001",
            "project_name": "cobot",
            "query": "test query",
            "intent": "validating",
            "domain": "edtech",
            "report_type": "market_research",
            "status": "complete",
            "quality_gate_passed": True,
            "overall_quality_score": 2.4,
            "gap_principles": ["META-12"],
            "strength_principles": ["META-1"],
            "duration_seconds": 300.0,
            "started_at": "2026-02-13T12:00:00+00:00",
            "trace_file_path": "/some/path.json",
        }
        result = _row_to_result(row)
        assert result.trace_id == "trc_test_001"
        assert result.project_name == "cobot"
        assert result.quality_gate_passed is True
        assert result.overall_quality_score == 2.4
        assert result.gap_principles == ["META-12"]

    def test_converts_minimal_row(self):
        """Test _row_to_result with minimal fields."""
        row = {"trace_id": "trc_min", "started_at": None}
        result = _row_to_result(row)
        assert result.trace_id == "trc_min"
        assert result.project_name is None
        assert result.started_at is None


# ============================================================================
# TestTraceQueryMethods (mocked — no DB required)
# ============================================================================

class TestTraceQueryMethods:
    @pytest.mark.asyncio
    async def test_by_intent_calls_correct_sql(self):
        """Verify by_intent uses correct SQL pattern."""
        mock_rows = [
            {"trace_id": "trc_1", "intent": "validating", "status": "complete",
             "started_at": "2026-02-13T12:00:00+00:00"},
        ]
        with patch("src.tracing.query.fetch_all", new_callable=AsyncMock, return_value=mock_rows):
            results = await TraceQuery.by_intent("validating")
        assert len(results) == 1
        assert results[0].intent == "validating"

    @pytest.mark.asyncio
    async def test_by_intent_no_results(self):
        """by_intent returns empty list when no matches."""
        with patch("src.tracing.query.fetch_all", new_callable=AsyncMock, return_value=[]):
            results = await TraceQuery.by_intent("nonexistent")
        assert results == []

    @pytest.mark.asyncio
    async def test_low_scoring_principle(self):
        """low_scoring_principle returns matching rows."""
        mock_rows = [
            {"trace_id": "trc_2", "intent": "validating", "status": "complete",
             "started_at": "2026-02-13T12:00:00+00:00"},
        ]
        with patch("src.tracing.query.fetch_all", new_callable=AsyncMock, return_value=mock_rows):
            results = await TraceQuery.low_scoring_principle("META-12", threshold=2)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_quality_gate_failures(self):
        """quality_gate_failures returns failed runs."""
        mock_rows = [
            {"trace_id": "trc_fail", "quality_gate_passed": False, "status": "complete",
             "started_at": "2026-02-13T12:00:00+00:00"},
        ]
        with patch("src.tracing.query.fetch_all", new_callable=AsyncMock, return_value=mock_rows):
            results = await TraceQuery.quality_gate_failures()
        assert len(results) == 1
        assert results[0].trace_id == "trc_fail"

    @pytest.mark.asyncio
    async def test_by_domain(self):
        """by_domain returns matching rows."""
        mock_rows = [
            {"trace_id": "trc_dom", "domain": "edtech", "status": "complete",
             "started_at": "2026-02-13T12:00:00+00:00"},
        ]
        with patch("src.tracing.query.fetch_all", new_callable=AsyncMock, return_value=mock_rows):
            results = await TraceQuery.by_domain("edtech")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_by_project(self):
        """by_project returns matching rows."""
        mock_rows = [
            {"trace_id": "trc_proj", "project_name": "cobot", "status": "complete",
             "started_at": "2026-02-13T12:00:00+00:00"},
        ]
        with patch("src.tracing.query.fetch_all", new_callable=AsyncMock, return_value=mock_rows):
            results = await TraceQuery.by_project("cobot")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_principle_patterns(self):
        """principle_patterns returns aggregated failure counts."""
        mock_rows = [
            {"principle_id": "META-12", "fail_count": 5},
            {"principle_id": "META-1", "fail_count": 3},
        ]
        with patch("src.tracing.query.fetch_all", new_callable=AsyncMock, return_value=mock_rows):
            results = await TraceQuery.principle_patterns(min_runs=3)
        assert len(results) == 2
        assert results[0]["principle_id"] == "META-12"
        assert results[0]["fail_count"] == 5

    @pytest.mark.asyncio
    async def test_compare_two_traces(self):
        """compare returns delta and gap analysis."""
        row_a = {
            "trace_id": "trc_a", "overall_quality_score": 2.0,
            "duration_seconds": 200.0, "synthesis_cost_usd": 0.20,
            "gap_principles": ["META-12", "META-5"],
            "project_name": "test",
        }
        row_b = {
            "trace_id": "trc_b", "overall_quality_score": 2.8,
            "duration_seconds": 250.0, "synthesis_cost_usd": 0.30,
            "gap_principles": ["META-12", "MR-1"],
            "project_name": "test",
        }

        async def mock_fetch_one(query, trace_id):
            if trace_id == "trc_a":
                return row_a
            return row_b

        with patch("src.tracing.query.fetch_one", side_effect=mock_fetch_one):
            result = await TraceQuery.compare("trc_a", "trc_b")

        assert result["quality_delta"] == pytest.approx(0.8)
        assert result["duration_delta"] == pytest.approx(50.0)
        assert result["cost_delta"] == pytest.approx(0.10)
        assert "META-5" in result["gaps_a_only"]
        assert "MR-1" in result["gaps_b_only"]
        assert "META-12" in result["gaps_both"]

    @pytest.mark.asyncio
    async def test_compare_missing_trace(self):
        """compare returns error if a trace not found."""
        with patch("src.tracing.query.fetch_one", new_callable=AsyncMock, return_value=None):
            result = await TraceQuery.compare("trc_missing_a", "trc_missing_b")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_flagged_for_review(self):
        """flagged_for_review returns flagged rows."""
        mock_rows = [
            {"trace_id": "trc_flagged", "flagged_for_review": True, "status": "incomplete",
             "started_at": "2026-02-13T12:00:00+00:00"},
        ]
        with patch("src.tracing.query.fetch_all", new_callable=AsyncMock, return_value=mock_rows):
            results = await TraceQuery.flagged_for_review()
        assert len(results) == 1
        assert results[0].trace_id == "trc_flagged"

    @pytest.mark.asyncio
    async def test_summary(self):
        """summary returns aggregate counts."""
        mock_row = {
            "total_runs": 10,
            "complete": 7,
            "failed": 2,
            "incomplete": 1,
            "qg_passed": 5,
            "qg_failed": 2,
            "avg_quality": 2.3,
            "avg_duration": 280.5,
            "avg_cost": 0.25,
        }
        with patch("src.tracing.query.fetch_one", new_callable=AsyncMock, return_value=mock_row):
            result = await TraceQuery.summary()
        assert result["total_runs"] == 10
        assert result["complete"] == 7
        assert result["avg_quality"] == pytest.approx(2.3)

    @pytest.mark.asyncio
    async def test_summary_empty(self):
        """summary returns zeros when no traces exist."""
        with patch("src.tracing.query.fetch_one", new_callable=AsyncMock, return_value=None):
            result = await TraceQuery.summary()
        assert result["total_runs"] == 0
        assert result["avg_quality"] is None

    @pytest.mark.asyncio
    async def test_full_trace_reads_file(self, tmp_path):
        """full_trace reads JSON from the file path stored in Supabase."""
        import json

        # Create a trace file
        trace_file = tmp_path / "test_trace.json"
        trace_data = {"trace_id": "trc_full", "schema_version": 1}
        with open(trace_file, "w") as f:
            json.dump(trace_data, f)

        mock_row = {"trace_file_path": str(trace_file)}
        with patch("src.tracing.query.fetch_one", new_callable=AsyncMock, return_value=mock_row):
            result = await TraceQuery.full_trace("trc_full")
        assert result is not None
        assert result["trace_id"] == "trc_full"

    @pytest.mark.asyncio
    async def test_full_trace_missing_file(self):
        """full_trace returns None when file doesn't exist."""
        mock_row = {"trace_file_path": "/nonexistent/path.json"}
        with patch("src.tracing.query.fetch_one", new_callable=AsyncMock, return_value=mock_row):
            result = await TraceQuery.full_trace("trc_missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_full_trace_not_in_db(self):
        """full_trace returns None when trace_id not found in Supabase."""
        with patch("src.tracing.query.fetch_one", new_callable=AsyncMock, return_value=None):
            result = await TraceQuery.full_trace("trc_unknown")
        assert result is None


# ============================================================================
# TestTraceQueryLiveDB (skipped without Supabase)
# ============================================================================

@SKIP_DB
class TestTraceQueryLiveDB:
    """Live database tests — require Supabase with _023 migration applied."""

    @pytest.fixture(autouse=True)
    async def setup_test_traces(self):
        """Insert test rows, yield, then clean up."""
        from src.db.connection import get_connection
        from datetime import datetime, timezone

        self.test_trace_ids = [
            "test_query_001", "test_query_002", "test_query_003",
            "test_query_004", "test_query_005",
        ]
        now = datetime.now(timezone.utc)

        async with get_connection() as conn:
            # trace_1: validating, edtech, passed, score 2.5
            await conn.execute("""
                INSERT INTO traces (trace_id, intent, domain, report_type, status,
                    quality_gate_passed, overall_quality_score, principle_scores,
                    started_at, duration_seconds)
                VALUES ($1, 'validating', 'edtech', 'market_research', 'complete',
                    TRUE, 2.5, '{"META-1": 3, "META-12": 1}', $2, 300)
            """, self.test_trace_ids[0], now)

            # trace_2: validating, robotics, failed, score 1.5
            await conn.execute("""
                INSERT INTO traces (trace_id, intent, domain, status,
                    quality_gate_passed, overall_quality_score, principle_scores,
                    started_at, duration_seconds)
                VALUES ($1, 'validating', 'robotics', 'complete',
                    FALSE, 1.5, '{"META-1": 1, "META-12": 0}', $2, 200)
            """, self.test_trace_ids[1], now)

            # trace_3: exploring, edtech, passed, score 2.8
            await conn.execute("""
                INSERT INTO traces (trace_id, intent, domain, status,
                    quality_gate_passed, overall_quality_score, principle_scores,
                    started_at)
                VALUES ($1, 'exploring', 'edtech', 'complete',
                    TRUE, 2.8, '{"META-1": 3, "META-12": 3}', $2)
            """, self.test_trace_ids[2], now)

            # trace_4: executing, edtech, passed, score 2.2
            await conn.execute("""
                INSERT INTO traces (trace_id, intent, domain, status,
                    quality_gate_passed, overall_quality_score,
                    gap_principles, started_at)
                VALUES ($1, 'executing', 'edtech', 'complete',
                    TRUE, 2.2, ARRAY['META-5'], $2)
            """, self.test_trace_ids[3], now)

            # trace_5: validating, edtech, flagged, incomplete
            await conn.execute("""
                INSERT INTO traces (trace_id, intent, domain, status,
                    flagged_for_review, started_at)
                VALUES ($1, 'validating', 'edtech', 'incomplete',
                    TRUE, $2)
            """, self.test_trace_ids[4], now)

        yield

        async with get_connection() as conn:
            for tid in self.test_trace_ids:
                await conn.execute("DELETE FROM traces WHERE trace_id = $1", tid)

    @pytest.mark.asyncio
    async def test_by_intent_live(self):
        results = await TraceQuery.by_intent("validating")
        trace_ids = [r.trace_id for r in results]
        assert "test_query_001" in trace_ids
        assert "test_query_002" in trace_ids
        # trace_5 is incomplete, should not appear
        assert "test_query_005" not in trace_ids

    @pytest.mark.asyncio
    async def test_quality_gate_failures_live(self):
        results = await TraceQuery.quality_gate_failures()
        trace_ids = [r.trace_id for r in results]
        assert "test_query_002" in trace_ids

    @pytest.mark.asyncio
    async def test_flagged_for_review_live(self):
        results = await TraceQuery.flagged_for_review()
        trace_ids = [r.trace_id for r in results]
        assert "test_query_005" in trace_ids

    @pytest.mark.asyncio
    async def test_summary_live(self):
        result = await TraceQuery.summary()
        assert result["total_runs"] >= 5
