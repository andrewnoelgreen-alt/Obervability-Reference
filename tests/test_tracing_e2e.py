"""
End-to-end integration test for the tracing system.

Ralph Run 8 of the Observability System build plan.

Tests the complete flow:
1. Start trace via TraceContext
2. Call real components (rubric loader) that emit trace data
3. Record evidence, prompts, and decisions across stages
4. Finish trace (writes file + attempts Supabase)
5. Verify trace file exists and contains all expected data
6. Verify TraceQuery, calibration flags work on the trace
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.tracing.context import TraceContext, Trace, _current_trace
from src.tracing.writer import write_trace_file
from src.tracing.calibration_flags import check_calibration_flags


class TestE2ETraceLifecycle:
    """Full lifecycle: start → record → finish → read back."""

    @pytest.mark.asyncio
    async def test_full_trace_lifecycle(self, tmp_path):
        """
        Complete trace flow using real rubric loader + simulated stages.

        1. TraceContext.start()
        2. rubric_loader.load() emits to trace
        3. Simulate collection, synthesis, quality_gate stages
        4. TraceContext.finish() writes file
        5. Read back file and verify all data
        """
        from src.agents.rubric_loader import load_rubric

        with patch("src.tracing.writer.BRAIN_DIR", tmp_path):
            # Step 1: Start trace
            trace = TraceContext.start(
                project_name="e2e-test-project",
                project_id="00000000-0000-0000-0000-e2e000000001",
                query="Analyze the assessment market for Group Assessment AI",
                intent="validating",
                domain="edtech",
                report_type="market_research",
                research_type="competitive",
            )

            assert TraceContext.current() is trace
            assert trace.trace_id.startswith("trc_")

            # Step 2: Real rubric load (emits to trace)
            rubric = load_rubric(
                report_type="market_research",
                domain="edtech",
                intent="validating",
            )
            assert rubric.total_count > 0
            assert "rubric" in trace.stages

            # Step 3: Simulate collection stage
            trace.start_stage("collection")
            trace.record("collection", "source_selection", {
                "what": "Collected from 2 sources",
                "why": "Sources: exa, reddit",
                "confidence": 1.0,
                "inputs": {"sources": ["exa", "reddit"]},
            })
            trace.record_evidence("collection", {
                "collected_count": 28,
                "by_source": {"exa": 22, "reddit": 6},
            })
            trace.end_stage("collection", outputs={
                "evidence_passed": 18,
                "evidence_filtered": 10,
            })

            # Step 4: Simulate synthesis stage
            trace.start_stage("synthesis")
            trace.record("synthesis", "prompt_construction", {
                "what": "Built synthesis prompt with 18 sources",
                "why": "Synthesizing competitive analysis",
                "confidence": 1.0,
                "inputs": {"evidence_count": 18, "model": "claude-opus-4-6"},
            })
            trace.record_prompts("synthesis", {
                "system_prompt": "You are a research analyst...",
                "user_message": "Please synthesize...",
            })
            trace.end_stage("synthesis", outputs={
                "model": "claude-opus-4-6",
                "token_usage": {"input_tokens": 45000, "output_tokens": 3800},
                "cost_usd": 0.32,
            })

            # Step 5: Simulate quality gate stage
            trace.start_stage("quality_gate")
            trace.record("quality_gate", "quality_assessment", {
                "what": "PASS (2.4/2.0)",
                "why": "Meets threshold",
                "confidence": 0.95,
            })
            trace.end_stage("quality_gate", outputs={
                "passed": True,
                "overall_score": 2.4,
                "principle_scores": [
                    {"id": "META-1", "score": 3},
                    {"id": "META-2", "score": 2},
                    {"id": "META-12", "score": 1},
                ],
                "gap_principles": ["META-12"],
                "strength_principles": ["META-1"],
            })

            # Step 6: Set outputs and mark complete
            trace.set_outputs({
                "report_path": "brain/projects/e2e-test-project/report.md",
            })
            trace.mark_complete()

            # Step 7: Finish (writes file, attempts Supabase)
            result = await TraceContext.finish(trace)

        # ========================================================================
        # VERIFICATION
        # ========================================================================

        # Verify context cleared
        assert TraceContext.current() is None

        # Verify file written
        assert "file_path" in result
        file_path = Path(result["file_path"])
        assert file_path.exists()

        # Read back and verify structure
        with open(file_path) as f:
            data = json.load(f)

        # Top-level fields
        assert data["schema_version"] == 1
        assert data["trace_id"] == trace.trace_id
        assert data["run"]["status"] == "complete"
        assert data["run"]["query"] == "Analyze the assessment market for Group Assessment AI"
        assert data["run"]["intent"] == "validating"
        assert data["run"]["domain"] == "edtech"

        # Stages
        assert "rubric" in data["stages"]
        assert "collection" in data["stages"]
        assert "synthesis" in data["stages"]
        assert "quality_gate" in data["stages"]

        # Rubric stage has decisions from real rubric_loader
        rubric_stage = data["stages"]["rubric"]
        assert len(rubric_stage["decisions"]) >= 1
        assert any(d["decision"] == "rubric_loaded" for d in rubric_stage["decisions"])

        # Collection stage
        coll_stage = data["stages"]["collection"]
        assert coll_stage["evidence"]["collected_count"] == 28
        assert coll_stage["outputs"]["evidence_passed"] == 18

        # Synthesis stage
        synth_stage = data["stages"]["synthesis"]
        assert synth_stage["outputs"]["model"] == "claude-opus-4-6"
        assert synth_stage["outputs"]["cost_usd"] == 0.32
        assert synth_stage["prompts"]["system_prompt"] == "You are a research analyst..."

        # Quality gate stage
        qg_stage = data["stages"]["quality_gate"]
        assert qg_stage["outputs"]["passed"] is True
        assert qg_stage["outputs"]["overall_score"] == 2.4
        assert "META-12" in qg_stage["outputs"]["gap_principles"]

        # Outputs
        assert "report_path" in data["outputs"]

    @pytest.mark.asyncio
    async def test_noop_trace_produces_no_file(self):
        """Disabled tracing produces no file and no crash."""
        trace = TraceContext.start(enabled=False)
        result = await TraceContext.finish(trace)
        assert result["saved"] is False
        assert "file_path" not in result

    @pytest.mark.asyncio
    async def test_failed_trace_writes_incomplete(self, tmp_path):
        """Trace that isn't marked complete writes as 'incomplete'."""
        with patch("src.tracing.writer.BRAIN_DIR", tmp_path):
            trace = TraceContext.start(project_name="fail-test")
            trace.start_stage("collection")
            # Don't end stage, don't mark complete
            result = await TraceContext.finish(trace)

        assert "file_path" in result
        with open(result["file_path"]) as f:
            data = json.load(f)
        assert data["run"]["status"] == "incomplete"


class TestE2ECalibrationFlags:
    """Verify calibration flags work on a realistic trace."""

    @pytest.mark.asyncio
    async def test_calibration_flags_on_clean_trace(self):
        """Clean trace with all principles passing generates no flags."""
        trace = Trace(trace_id="trc_clean", status="complete", intent="validating")
        trace.start_stage("quality_gate")
        trace.end_stage("quality_gate", outputs={
            "passed": True,
            "overall_score": 2.8,
            "principle_scores": {"META-1": 3, "META-2": 3},
        })

        with patch(
            "src.tracing.calibration_flags._recent_failure_count",
            new_callable=AsyncMock, return_value=0,
        ), patch(
            "src.tracing.calibration_flags._intent_avg_score",
            new_callable=AsyncMock, return_value=2.5,
        ), patch(
            "src.tracing.calibration_flags._overall_avg_score",
            new_callable=AsyncMock, return_value=2.5,
        ), patch(
            "src.tracing.calibration_flags._domain_avg_score",
            new_callable=AsyncMock, return_value=2.5,
        ):
            flags = await check_calibration_flags(trace)
        assert flags == []

    @pytest.mark.asyncio
    async def test_calibration_flags_on_failing_trace(self):
        """Trace with low-scoring principles generates flags."""
        trace = Trace(
            trace_id="trc_failing",
            status="complete",
            intent="validating",
            domain="edtech",
            project_id="00000000-0000-0000-0000-000000000001",
            project_name="test",
            started_at="2026-02-13T12:00:00+00:00",
        )
        trace.start_stage("quality_gate")
        trace.end_stage("quality_gate", outputs={
            "passed": False,
            "overall_score": 1.5,
            "gap_principles": ["META-12"],
        })

        with patch(
            "src.tracing.calibration_flags._recent_failure_count",
            new_callable=AsyncMock, return_value=5,
        ), patch(
            "src.tracing.calibration_flags._intent_avg_score",
            new_callable=AsyncMock, return_value=1.5,
        ), patch(
            "src.tracing.calibration_flags._overall_avg_score",
            new_callable=AsyncMock, return_value=2.5,
        ), patch(
            "src.tracing.calibration_flags._domain_avg_score",
            new_callable=AsyncMock, return_value=2.3,
        ), patch(
            "src.tracing.calibration_flags._previous_trace_for_project",
            new_callable=AsyncMock,
            return_value={"trace_id": "trc_prev", "quality_gate_passed": True},
        ):
            flags = await check_calibration_flags(trace)
        # Should have at least 2 flags: repeated failure + intent disparity
        assert len(flags) >= 2


class TestE2ETraceQuery:
    """Verify TraceQuery module is importable and structurally correct."""

    def test_trace_query_importable(self):
        from src.tracing.query import TraceQuery
        assert hasattr(TraceQuery, 'by_intent')
        assert hasattr(TraceQuery, 'low_scoring_principle')
        assert hasattr(TraceQuery, 'quality_gate_failures')
        assert hasattr(TraceQuery, 'by_domain')
        assert hasattr(TraceQuery, 'by_project')
        assert hasattr(TraceQuery, 'compare')
        assert hasattr(TraceQuery, 'full_trace')
        assert hasattr(TraceQuery, 'summary')
        assert hasattr(TraceQuery, 'flagged_for_review')
        assert hasattr(TraceQuery, 'principle_patterns')

    def test_trace_result_importable(self):
        from src.tracing.query import TraceResult
        result = TraceResult(trace_id="trc_test")
        assert result.trace_id == "trc_test"


class TestE2EModuleExports:
    """Verify all tracing modules export correctly from __init__."""

    def test_all_exports_accessible(self):
        from src.tracing import (
            TraceContext,
            Trace,
            Decision,
            StageTrace,
            TracingComponent,
            validate_trace_schema,
            register_component,
            get_registered_components,
            TraceQuery,
            TraceResult,
            check_calibration_flags,
        )
        # All should be importable without error
        assert TraceContext is not None
        assert Trace is not None
        assert TraceQuery is not None
        assert check_calibration_flags is not None
