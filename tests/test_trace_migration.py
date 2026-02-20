"""
Tests for src/db/migrations/_023_trace_system.py — traces table migration.

Ralph Run 3 of the Observability System build plan.
"""

import os
import asyncio

import pytest

from src.db.migrations._023_trace_system import UP, DOWN


# All expected column names in the traces table
EXPECTED_COLUMNS = [
    "id", "trace_id", "project_id",
    "query", "intent", "domain", "report_type", "research_type",
    "status", "quality_gate_passed", "overall_quality_score",
    "started_at", "completed_at", "duration_seconds",
    "intake_duration", "rubric_duration", "collection_duration",
    "synthesis_duration", "quality_gate_duration",
    "evidence_collected", "evidence_passed", "evidence_filtered",
    "synthesis_model", "synthesis_input_tokens", "synthesis_output_tokens",
    "synthesis_cost_usd",
    "principle_scores", "gap_principles", "strength_principles",
    "iteration_count", "quality_gate_failures",
    "trace_file_path", "report_file_path", "output_file_paths",
    "flagged_for_review", "review_notes",
    "created_at", "updated_at",
]

EXPECTED_INDEXES = [
    "idx_traces_intent",
    "idx_traces_domain",
    "idx_traces_report_type",
    "idx_traces_status",
    "idx_traces_quality_gate",
    "idx_traces_project",
    "idx_traces_started",
    "idx_traces_overall_score",
    "idx_traces_flagged",
    "idx_traces_principle_scores",
]


class TestTraceMigration:
    def test_up_sql_is_valid(self):
        assert UP is not None
        assert isinstance(UP, str)
        assert len(UP) > 100

    def test_down_sql_is_valid(self):
        assert DOWN is not None
        assert isinstance(DOWN, str)
        assert len(DOWN) > 10

    def test_up_creates_table(self):
        assert "CREATE TABLE" in UP
        assert "traces" in UP

    def test_up_has_all_columns(self):
        for col in EXPECTED_COLUMNS:
            assert col in UP, f"Missing column: {col}"

    def test_up_has_all_indexes(self):
        for idx in EXPECTED_INDEXES:
            assert idx in UP, f"Missing index: {idx}"

    def test_up_has_gin_index(self):
        assert "USING GIN" in UP
        assert "principle_scores" in UP

    def test_up_has_trigger(self):
        assert "CREATE TRIGGER" in UP
        assert "update_traces_updated_at" in UP
        assert "update_updated_at_column" in UP

    def test_up_has_views(self):
        assert "trace_quality_gaps" in UP
        assert "trace_intent_summary" in UP
        assert "CREATE OR REPLACE VIEW trace_quality_gaps" in UP
        assert "CREATE OR REPLACE VIEW trace_intent_summary" in UP

    def test_down_drops_everything(self):
        assert "DROP VIEW IF EXISTS trace_intent_summary" in DOWN
        assert "DROP VIEW IF EXISTS trace_quality_gaps" in DOWN
        assert "DROP TRIGGER IF EXISTS update_traces_updated_at" in DOWN
        assert "DROP TABLE IF EXISTS traces" in DOWN

    @pytest.mark.skipif(
        not os.environ.get("DATABASE_URL")
        or "localhost" in os.environ.get("DATABASE_URL", "")
        or "127.0.0.1" in os.environ.get("DATABASE_URL", ""),
        reason="DATABASE_URL not set or points to localhost — skipping live database test"
    )
    @pytest.mark.asyncio
    async def test_migration_runs_on_database(self):
        """Actually run UP SQL against Supabase, verify table exists, then clean up."""
        from src.db.connection import get_connection

        try:
            # Run UP migration
            async with get_connection() as conn:
                await conn.execute(UP)

            # Verify table exists
            async with get_connection() as conn:
                row = await conn.fetchval("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_name = 'traces'
                    )
                """)
                assert row is True, "traces table should exist after UP migration"

            # Verify at least one index exists
            async with get_connection() as conn:
                count = await conn.fetchval("""
                    SELECT COUNT(*)
                    FROM pg_indexes
                    WHERE tablename = 'traces'
                    AND indexname LIKE 'idx_traces_%'
                """)
                assert count >= 10, f"Expected at least 10 indexes, found {count}"

        finally:
            # Always clean up — run DOWN migration
            async with get_connection() as conn:
                await conn.execute(DOWN)

            # Verify cleanup
            async with get_connection() as conn:
                row = await conn.fetchval("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_name = 'traces'
                    )
                """)
                assert row is False, "traces table should not exist after DOWN migration"
