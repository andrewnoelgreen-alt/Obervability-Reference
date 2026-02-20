"""
Tests for src/tracing/context.py — core trace data models and context manager.

Ralph Run 1 of the Observability System build plan.
"""

import asyncio
import re
import time

import pytest

from src.tracing.context import (
    Decision,
    StageTrace,
    Trace,
    TraceContext,
    _NoOpTrace,
    SCHEMA_VERSION,
    _current_trace,
)


# ============================================================================
# TestDecision
# ============================================================================

class TestDecision:
    def test_decision_creation(self):
        d = Decision(
            decision="classified_intent",
            what="validating",
            why="User has hypothesis",
            confidence=0.85,
            alternatives_considered=["exploring (0.10)"],
            inputs={"query_text": "test query"},
        )
        assert d.decision == "classified_intent"
        assert d.what == "validating"
        assert d.why == "User has hypothesis"
        assert d.confidence == 0.85
        assert d.alternatives_considered == ["exploring (0.10)"]
        assert d.inputs == {"query_text": "test query"}

    def test_decision_auto_timestamp(self):
        d = Decision(decision="test", what="x", why="y", confidence=1.0)
        assert d.timestamp is not None
        assert len(d.timestamp) > 0
        # Should be ISO format with timezone
        assert "T" in d.timestamp


# ============================================================================
# TestStageTrace
# ============================================================================

class TestStageTrace:
    def test_stage_creation(self):
        s = StageTrace(name="intake")
        assert s.name == "intake"
        assert s.started_at is None
        assert s.completed_at is None
        assert s.duration_seconds is None
        assert s.decisions == []
        assert s.outputs == {}
        assert s.evidence == {}
        assert s.prompts == {}
        assert s.error is None

    def test_stage_with_decisions(self):
        s = StageTrace(name="rubric")
        d1 = Decision(decision="rubric_loaded", what="21 principles", why="test", confidence=1.0)
        d2 = Decision(decision="intent_activation", what="validating", why="test", confidence=1.0)
        s.decisions.append(d1)
        s.decisions.append(d2)
        assert len(s.decisions) == 2
        assert s.decisions[0].decision == "rubric_loaded"
        assert s.decisions[1].decision == "intent_activation"


# ============================================================================
# TestTrace
# ============================================================================

class TestTrace:
    def test_trace_creation(self):
        t = Trace(trace_id="trc_test_001")
        assert t.trace_id == "trc_test_001"
        assert t.project_id is None
        assert t.project_name is None
        assert t.query is None
        assert t.intent is None
        assert t.domain is None
        assert t.report_type is None
        assert t.research_type is None
        assert t.started_at is None
        assert t.completed_at is None
        assert t.duration_seconds is None
        assert t.status == "in_progress"
        assert t.stages == {}
        assert t.iterations == []
        assert t.iteration_count == 1
        assert t.quality_gate_failures == 0
        assert t.outputs == {}
        assert t.child_traces == []

    def test_start_stage(self):
        t = Trace(trace_id="trc_test")
        t.start_stage("rubric")
        assert "rubric" in t.stages
        assert t.stages["rubric"].name == "rubric"
        assert t.stages["rubric"].started_at is not None
        assert "T" in t.stages["rubric"].started_at  # ISO format

    def test_end_stage(self):
        t = Trace(trace_id="trc_test")
        t.start_stage("rubric")
        time.sleep(0.01)  # Ensure measurable duration
        t.end_stage("rubric", outputs={"total_principles": 21})
        stage = t.stages["rubric"]
        assert stage.completed_at is not None
        assert stage.duration_seconds is not None
        assert stage.duration_seconds > 0
        assert stage.outputs == {"total_principles": 21}

    def test_end_stage_unstarted(self):
        """end_stage on unstarted stage should warn, not crash."""
        t = Trace(trace_id="trc_test")
        # Should not raise
        t.end_stage("nonexistent")
        assert "nonexistent" not in t.stages

    def test_end_stage_with_error(self):
        t = Trace(trace_id="trc_test")
        t.start_stage("collection")
        t.end_stage("collection", error="Timeout on exa API")
        assert t.stages["collection"].error == "Timeout on exa API"

    def test_record_decision(self):
        t = Trace(trace_id="trc_test")
        t.start_stage("intake")
        t.record("intake", "classified_intent", {
            "what": "validating",
            "why": "hypothesis detected",
            "confidence": 0.85,
            "alternatives_considered": ["exploring"],
            "inputs": {"query": "test"},
        })
        assert len(t.stages["intake"].decisions) == 1
        d = t.stages["intake"].decisions[0]
        assert d.decision == "classified_intent"
        assert d.what == "validating"
        assert d.confidence == 0.85

    def test_record_auto_creates_stage(self):
        t = Trace(trace_id="trc_test")
        # No start_stage call — record should auto-create
        t.record("new_stage", "some_decision", {"what": "test", "why": "auto"})
        assert "new_stage" in t.stages
        assert len(t.stages["new_stage"].decisions) == 1

    def test_record_evidence(self):
        t = Trace(trace_id="trc_test")
        t.start_stage("collection")
        evidence = {"collected_count": 28, "by_source": {"exa": 22, "reddit": 6}}
        t.record_evidence("collection", evidence)
        assert t.stages["collection"].evidence == evidence

    def test_record_evidence_auto_creates_stage(self):
        t = Trace(trace_id="trc_test")
        t.record_evidence("collection", {"collected_count": 10})
        assert "collection" in t.stages

    def test_record_prompts(self):
        t = Trace(trace_id="trc_test")
        t.start_stage("synthesis")
        prompts = {"system_prompt": "You are a research assistant", "user_message": "Analyze this"}
        t.record_prompts("synthesis", prompts)
        assert t.stages["synthesis"].prompts == prompts

    def test_record_prompts_auto_creates_stage(self):
        t = Trace(trace_id="trc_test")
        t.record_prompts("synthesis", {"system_prompt": "test"})
        assert "synthesis" in t.stages

    def test_record_iteration(self):
        t = Trace(trace_id="trc_test")
        t.record_iteration({"iteration": 1, "passed": True, "score": 2.4})
        assert len(t.iterations) == 1
        assert t.iteration_count == 1
        assert t.quality_gate_failures == 0

    def test_record_iteration_failure(self):
        t = Trace(trace_id="trc_test")
        t.record_iteration({"iteration": 1, "passed": False, "score": 1.5})
        assert t.iteration_count == 1
        assert t.quality_gate_failures == 1
        t.record_iteration({"iteration": 2, "passed": True, "score": 2.4})
        assert t.iteration_count == 2
        assert t.quality_gate_failures == 1  # Only 1 failure

    def test_set_outputs(self):
        t = Trace(trace_id="trc_test")
        outputs = {"report_path": "brain/projects/test/report.md"}
        t.set_outputs(outputs)
        assert t.outputs == outputs

    def test_mark_complete(self):
        t = Trace(trace_id="trc_test", _start_time=time.monotonic())
        time.sleep(0.01)
        t.mark_complete()
        assert t.status == "complete"
        assert t.completed_at is not None
        assert t.duration_seconds is not None
        assert t.duration_seconds > 0

    def test_mark_failed(self):
        t = Trace(trace_id="trc_test", _start_time=time.monotonic())
        t.mark_failed("API timeout")
        assert t.status == "failed"
        assert t.completed_at is not None
        assert t.outputs["error"] == "API timeout"
        assert t.duration_seconds is not None

    def test_mark_incomplete(self):
        t = Trace(trace_id="trc_test", _start_time=time.monotonic())
        t.mark_incomplete()
        assert t.status == "incomplete"
        assert t.completed_at is not None
        assert t.duration_seconds is not None

    def test_to_dict(self):
        """Full serialization test with multiple stages and decisions."""
        t = Trace(
            trace_id="trc_test_full",
            project_id="uuid-123",
            project_name="test-project",
            query="test query",
            intent="validating",
            domain="edtech",
            report_type="market_research",
            research_type="competitive",
            started_at="2026-02-13T10:00:00+00:00",
            status="complete",
        )

        # Add rubric stage with decisions
        t.start_stage("rubric")
        t.record("rubric", "rubric_loaded", {
            "what": "21 principles",
            "why": "market_research + validating",
            "confidence": 1.0,
        })
        t.end_stage("rubric", outputs={"total_principles": 21})

        # Add collection stage with evidence
        t.start_stage("collection")
        t.record_evidence("collection", {"collected_count": 28})
        t.record_prompts("collection", {"query_prompt": "search for..."})
        t.end_stage("collection", outputs={"evidence_passed": 18})

        d = t.to_dict()

        assert d["schema_version"] == SCHEMA_VERSION
        assert d["trace_id"] == "trc_test_full"
        assert d["project_id"] == "uuid-123"
        assert d["project_name"] == "test-project"
        assert d["run"]["query"] == "test query"
        assert d["run"]["intent"] == "validating"
        assert d["run"]["status"] == "complete"
        assert "rubric" in d["stages"]
        assert "collection" in d["stages"]
        assert len(d["stages"]["rubric"]["decisions"]) == 1
        assert d["stages"]["rubric"]["decisions"][0]["decision"] == "rubric_loaded"
        assert d["stages"]["rubric"]["outputs"]["total_principles"] == 21
        assert d["stages"]["collection"]["evidence"]["collected_count"] == 28
        assert d["stages"]["collection"]["prompts"]["query_prompt"] == "search for..."

    def test_to_dict_schema_version(self):
        t = Trace(trace_id="trc_test")
        d = t.to_dict()
        assert d["schema_version"] == 1

    def test_to_dict_metadata(self):
        t = Trace(trace_id="trc_test")
        d = t.to_dict()
        assert "metadata" in d
        assert d["metadata"]["generator"] == "ire-observability-v1"
        assert d["metadata"]["trace_version"] == SCHEMA_VERSION


# ============================================================================
# TestTraceContext
# ============================================================================

class TestTraceContext:
    def setup_method(self):
        """Reset context before each test."""
        _current_trace.set(None)

    def test_start_creates_trace(self):
        trace = TraceContext.start(project_name="test")
        assert trace is not None
        assert trace.trace_id.startswith("trc_")
        assert trace.project_name == "test"

    def test_start_sets_context(self):
        trace = TraceContext.start(project_name="test")
        assert TraceContext.current() is trace

    def test_current_none_without_start(self):
        assert TraceContext.current() is None

    @pytest.mark.asyncio
    async def test_finish_clears_context(self):
        trace = TraceContext.start(project_name="test")
        assert TraceContext.current() is not None
        result = await TraceContext.finish(trace)
        assert TraceContext.current() is None
        assert "trace_id" in result

    def test_start_disabled_returns_noop(self):
        trace = TraceContext.start(enabled=False)
        assert isinstance(trace, _NoOpTrace)
        assert trace.trace_id == "noop"
        assert trace.status == "disabled"

    def test_trace_id_format(self):
        trace = TraceContext.start(project_name="test")
        # Format: trc_YYYYMMDD_HHMMSS_hexhexhx
        pattern = r"^trc_\d{8}_\d{6}_[0-9a-f]{8}$"
        assert re.match(pattern, trace.trace_id), f"trace_id '{trace.trace_id}' doesn't match expected format"

    def test_start_with_all_params(self):
        trace = TraceContext.start(
            project_id="uuid-123",
            project_name="cobot",
            query="test query",
            intent="validating",
            domain="robotics",
            report_type="market_research",
            research_type="competitive",
        )
        assert trace.project_id == "uuid-123"
        assert trace.project_name == "cobot"
        assert trace.query == "test query"
        assert trace.intent == "validating"
        assert trace.domain == "robotics"
        assert trace.report_type == "market_research"
        assert trace.research_type == "competitive"
        assert trace.started_at is not None
        assert trace.status == "in_progress"

    @pytest.mark.asyncio
    async def test_finish_marks_incomplete_if_in_progress(self):
        trace = TraceContext.start(project_name="test")
        assert trace.status == "in_progress"
        result = await TraceContext.finish(trace)
        assert trace.status == "incomplete"
        assert result["status"] == "incomplete"

    @pytest.mark.asyncio
    async def test_finish_preserves_complete_status(self):
        trace = TraceContext.start(project_name="test")
        trace.mark_complete()
        result = await TraceContext.finish(trace)
        assert trace.status == "complete"
        assert result["status"] == "complete"

    @pytest.mark.asyncio
    async def test_finish_noop_returns_not_saved(self):
        trace = TraceContext.start(enabled=False)
        result = await TraceContext.finish(trace)
        assert result["saved"] is False
        assert result["reason"] == "tracing_disabled"


# ============================================================================
# TestNoOpTrace
# ============================================================================

class TestNoOpTrace:
    def test_noop_all_methods_silent(self):
        """Call every recording method on _NoOpTrace — none should raise."""
        t = _NoOpTrace()
        t.start_stage("test")
        t.end_stage("test", outputs={"x": 1}, error="err")
        t.record("test", "decision", {"what": "x", "why": "y"})
        t.record_evidence("test", {"count": 5})
        t.record_prompts("test", {"prompt": "hello"})
        t.record_iteration({"iteration": 1, "passed": True})
        t.set_outputs({"path": "test"})
        t.mark_complete()
        t.mark_failed("error")
        t.mark_incomplete()

    def test_noop_record_no_data(self):
        """NoOp record should not modify stages."""
        t = _NoOpTrace()
        t.record("stage", "decision", {"what": "test"})
        assert t.stages == {}

    def test_noop_mark_complete_silent(self):
        """mark_complete on noop should not change status."""
        t = _NoOpTrace()
        t.mark_complete()
        assert t.status == "disabled"

    def test_noop_status_disabled(self):
        t = _NoOpTrace()
        assert t.status == "disabled"

    def test_noop_set_outputs_no_change(self):
        t = _NoOpTrace()
        t.set_outputs({"report": "test.md"})
        assert t.outputs == {}


# ============================================================================
# TestAsyncContextIsolation
# ============================================================================

class TestAsyncContextIsolation:
    @pytest.mark.asyncio
    async def test_concurrent_traces(self):
        """
        Run 3 concurrent tasks, each starting its own trace.
        Verify each task sees its own trace via TraceContext.current().
        """
        results = {}

        async def task(name: str):
            trace = TraceContext.start(project_name=name)
            # Small delay to interleave tasks
            await asyncio.sleep(0.01)
            current = TraceContext.current()
            results[name] = current.project_name if current else None
            await TraceContext.finish(trace)

        await asyncio.gather(
            task("project_a"),
            task("project_b"),
            task("project_c"),
        )

        assert results["project_a"] == "project_a"
        assert results["project_b"] == "project_b"
        assert results["project_c"] == "project_c"
