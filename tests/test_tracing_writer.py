"""
Tests for src/tracing/writer.py — trace file + Supabase persistence.

Ralph Run 4 of the Observability System build plan.
"""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tracing.context import Trace, TraceContext, _NoOpTrace


# ============================================================================
# Helpers
# ============================================================================

def _make_trace(
    project_name="test-project",
    project_id=None,
    intent="validating",
    domain="edtech",
    report_type="market_research",
    status="complete",
    with_quality_gate=True,
    with_synthesis=True,
    with_collection=True,
) -> Trace:
    """Create a populated trace for testing."""
    trace = Trace(
        trace_id="trc_20260213_120000_abcd1234",
        project_id=project_id,
        project_name=project_name,
        query="test research query",
        intent=intent,
        domain=domain,
        report_type=report_type,
        research_type="deep",
        started_at="2026-02-13T12:00:00+00:00",
        completed_at="2026-02-13T12:05:00+00:00",
        duration_seconds=300.0,
        status=status,
        iteration_count=1,
        quality_gate_failures=0,
    )

    # Add rubric stage
    trace.start_stage("rubric")
    trace.record("rubric", "rubric_loaded", {
        "what": "21 principles loaded",
        "why": "market_research + edtech",
        "confidence": 1.0,
    })
    trace.end_stage("rubric", outputs={"total_principles": 21})

    if with_collection:
        trace.start_stage("collection")
        trace.record_evidence("collection", {
            "collected_count": 28,
            "by_source": {"exa": 22, "reddit": 6},
        })
        trace.end_stage("collection", outputs={
            "evidence_passed": 18,
            "evidence_filtered": 10,
        })

    if with_synthesis:
        trace.start_stage("synthesis")
        trace.record("synthesis", "prompt_construction", {
            "what": "Built synthesis prompt",
            "why": "Combining evidence",
            "confidence": 0.9,
        })
        trace.record_prompts("synthesis", {
            "system_prompt": "You are a research analyst...",
            "user_message": "Synthesize the following evidence...",
        })
        trace.end_stage("synthesis", outputs={
            "model": "claude-opus-4-6",
            "token_usage": {"input_tokens": 45000, "output_tokens": 3800},
            "cost_usd": 0.32,
        })

    if with_quality_gate:
        trace.start_stage("quality_gate")
        trace.record("quality_gate", "quality_assessment", {
            "what": "PASS (2.4/3.0)",
            "why": "Meets threshold",
            "confidence": 0.95,
        })
        trace.end_stage("quality_gate", outputs={
            "passed": True,
            "overall_score": 2.4,
            "principle_scores": [
                {"id": "META-1", "score": 3},
                {"id": "META-12", "score": 1},
            ],
        })

    trace.set_outputs({"report_path": "brain/projects/test/report.md"})
    return trace


# ============================================================================
# TestWriteTraceFile
# ============================================================================

class TestWriteTraceFile:
    def test_writes_json_file(self, tmp_path):
        """Write trace, verify file exists and is valid JSON."""
        trace = _make_trace()
        with patch("src.tracing.writer.BRAIN_DIR", tmp_path):
            from src.tracing.writer import write_trace_file
            file_path = write_trace_file(trace)
        assert file_path.exists()
        with open(file_path) as f:
            data = json.load(f)
        assert data["trace_id"] == trace.trace_id

    def test_file_path_structure(self, tmp_path):
        """Verify file is at brain/projects/{name}/_traces/{trace_id}.json."""
        trace = _make_trace(project_name="my-project")
        with patch("src.tracing.writer.BRAIN_DIR", tmp_path):
            from src.tracing.writer import write_trace_file
            file_path = write_trace_file(trace)
        expected = tmp_path / "projects" / "my-project" / "_traces" / f"{trace.trace_id}.json"
        assert file_path == expected

    def test_creates_traces_directory(self, tmp_path):
        """Write to project with no _traces/ dir, verify dir created."""
        trace = _make_trace(project_name="brand-new-project")
        traces_dir = tmp_path / "projects" / "brand-new-project" / "_traces"
        assert not traces_dir.exists()
        with patch("src.tracing.writer.BRAIN_DIR", tmp_path):
            from src.tracing.writer import write_trace_file
            write_trace_file(trace)
        assert traces_dir.exists()

    def test_unknown_project_name(self, tmp_path):
        """Trace with project_name=None writes to brain/projects/unknown/_traces/."""
        trace = _make_trace(project_name=None)
        with patch("src.tracing.writer.BRAIN_DIR", tmp_path):
            from src.tracing.writer import write_trace_file
            file_path = write_trace_file(trace)
        assert "unknown" in str(file_path)
        assert file_path.exists()

    def test_file_content_matches_trace(self, tmp_path):
        """Write and re-read, verify all fields match trace.to_dict()."""
        trace = _make_trace()
        with patch("src.tracing.writer.BRAIN_DIR", tmp_path):
            from src.tracing.writer import write_trace_file
            file_path = write_trace_file(trace)
        with open(file_path) as f:
            data = json.load(f)
        trace_dict = trace.to_dict()
        assert data["schema_version"] == trace_dict["schema_version"]
        assert data["trace_id"] == trace_dict["trace_id"]
        assert data["run"]["status"] == trace_dict["run"]["status"]
        assert "rubric" in data["stages"]
        assert "quality_gate" in data["stages"]

    def test_serializes_datetime(self, tmp_path):
        """Trace with datetime values serializes without error (default=str)."""
        from datetime import datetime, timezone
        trace = _make_trace()
        # Add a raw datetime to outputs to test default=str
        trace.outputs["raw_datetime"] = datetime.now(timezone.utc)
        with patch("src.tracing.writer.BRAIN_DIR", tmp_path):
            from src.tracing.writer import write_trace_file
            file_path = write_trace_file(trace)
        # Should not raise — default=str handles datetime
        with open(file_path) as f:
            data = json.load(f)
        assert "raw_datetime" in data["outputs"]

    def test_large_trace(self, tmp_path):
        """Create trace with 5 stages, 10 decisions each, verify writes successfully."""
        trace = _make_trace()
        for i in range(5):
            stage_name = f"extra_stage_{i}"
            trace.start_stage(stage_name)
            for j in range(10):
                trace.record(stage_name, f"decision_{j}", {
                    "what": f"Decision {j} in stage {i}",
                    "why": "Testing large trace",
                    "confidence": 0.8,
                })
            trace.end_stage(stage_name, outputs={"count": 10})
        with patch("src.tracing.writer.BRAIN_DIR", tmp_path):
            from src.tracing.writer import write_trace_file
            file_path = write_trace_file(trace)
        assert file_path.exists()
        with open(file_path) as f:
            data = json.load(f)
        # Original stages + 5 extra = at least 9 stages
        assert len(data["stages"]) >= 9


# ============================================================================
# TestWriteTraceMetadata
# ============================================================================

class TestWriteTraceMetadata:
    @pytest.mark.skipif(
        not os.environ.get("DATABASE_URL")
        or "localhost" in os.environ.get("DATABASE_URL", "")
        or "127.0.0.1" in os.environ.get("DATABASE_URL", ""),
        reason="DATABASE_URL not set or points to localhost — skipping live database test"
    )
    @pytest.mark.asyncio
    async def test_writes_to_supabase(self):
        """Create trace with quality_gate data, write metadata, verify row exists."""
        from src.tracing.writer import write_trace_metadata
        from src.db.connection import get_connection

        trace = _make_trace()
        trace.trace_id = "test_writer_supabase_001"

        try:
            await write_trace_metadata(trace)

            async with get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM traces WHERE trace_id = $1",
                    trace.trace_id,
                )
            assert row is not None
            assert row["intent"] == "validating"
            assert row["status"] == "complete"
        finally:
            async with get_connection() as conn:
                await conn.execute(
                    "DELETE FROM traces WHERE trace_id = $1",
                    trace.trace_id,
                )

    def test_handles_missing_quality_gate(self):
        """Trace without quality_gate stage extracts None for all QG fields."""
        from src.tracing.writer import _extract_quality_gate_data
        trace = _make_trace(with_quality_gate=False)
        data = _extract_quality_gate_data(trace)
        assert data["quality_gate_passed"] is None
        assert data["overall_quality_score"] is None
        assert data["principle_scores"] is None

    def test_handles_missing_synthesis(self):
        """Trace without synthesis stage extracts None for all synthesis fields."""
        from src.tracing.writer import _extract_synthesis_data
        trace = _make_trace(with_synthesis=False)
        data = _extract_synthesis_data(trace)
        assert data["synthesis_model"] is None
        assert data["synthesis_input_tokens"] is None
        assert data["synthesis_cost_usd"] is None


# ============================================================================
# TestTraceContextFinish
# ============================================================================

class TestTraceContextFinish:
    @pytest.mark.asyncio
    async def test_finish_writes_file(self, tmp_path):
        """Start trace, finish, verify file was written."""
        with patch("src.tracing.writer.BRAIN_DIR", tmp_path):
            trace = TraceContext.start(
                project_name="finish-test",
                query="test",
                intent="validating",
            )
            trace.mark_complete()
            result = await TraceContext.finish(trace)
        assert "file_path" in result
        assert Path(result["file_path"]).exists()

    @pytest.mark.asyncio
    async def test_finish_clears_context(self, tmp_path):
        """Start trace, finish, verify current() is None."""
        with patch("src.tracing.writer.BRAIN_DIR", tmp_path):
            trace = TraceContext.start(project_name="ctx-test")
            await TraceContext.finish(trace)
        assert TraceContext.current() is None

    @pytest.mark.asyncio
    async def test_finish_incomplete_trace(self, tmp_path):
        """Start trace (don't mark_complete), finish — verify status='incomplete'."""
        with patch("src.tracing.writer.BRAIN_DIR", tmp_path):
            trace = TraceContext.start(project_name="incomplete-test")
            result = await TraceContext.finish(trace)
        assert result["status"] == "incomplete"

    @pytest.mark.asyncio
    async def test_finish_noop_trace(self):
        """Start disabled trace, finish — returns {'saved': False}."""
        trace = TraceContext.start(enabled=False)
        result = await TraceContext.finish(trace)
        assert result["saved"] is False

    @pytest.mark.asyncio
    async def test_finish_survives_file_error(self):
        """Mock write_trace_file to raise, verify finish() still returns (no crash)."""
        trace = TraceContext.start(project_name="error-test")
        trace.mark_complete()
        with patch("src.tracing.writer.write_trace_file", side_effect=OSError("disk full")):
            result = await TraceContext.finish(trace)
        assert "file_error" in result
        assert "trace_id" in result

    @pytest.mark.asyncio
    async def test_finish_survives_supabase_error(self, tmp_path):
        """Mock write_trace_metadata to raise, verify finish() still returns."""
        with patch("src.tracing.writer.BRAIN_DIR", tmp_path):
            trace = TraceContext.start(project_name="sb-error-test")
            trace.mark_complete()
            with patch(
                "src.tracing.writer.write_trace_metadata",
                new_callable=AsyncMock,
                side_effect=ConnectionError("Supabase unreachable"),
            ):
                result = await TraceContext.finish(trace)
        assert "supabase_error" in result
        # File should still be written
        assert "file_path" in result
