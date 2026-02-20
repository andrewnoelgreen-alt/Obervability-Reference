"""
Tests for src/tracing/calibration_flags.py — calibration pattern detection.

Ralph Run 6 of the Observability System build plan.
Uses mocking for DB queries — no live Supabase required.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.tracing.context import Trace
from src.tracing.calibration_flags import check_calibration_flags


# ============================================================================
# Helpers
# ============================================================================

def _make_trace(
    intent="validating",
    domain="edtech",
    project_id="00000000-0000-0000-0000-000000000001",
    project_name="test-project",
    qg_passed=True,
    qg_score=2.4,
    gap_principles=None,
    principle_scores=None,
) -> Trace:
    """Create a trace with quality_gate stage for testing."""
    trace = Trace(
        trace_id="trc_test_flags",
        project_id=project_id,
        project_name=project_name,
        intent=intent,
        domain=domain,
        started_at="2026-02-13T12:00:00+00:00",
        status="complete",
    )
    trace.start_stage("quality_gate")
    outputs = {
        "passed": qg_passed,
        "overall_score": qg_score,
    }
    if gap_principles is not None:
        outputs["gap_principles"] = gap_principles
    if principle_scores is not None:
        outputs["principle_scores"] = principle_scores
    trace.end_stage("quality_gate", outputs=outputs)
    return trace


# ============================================================================
# TestCalibrationFlags
# ============================================================================

class TestCalibrationFlags:

    @pytest.mark.asyncio
    async def test_no_flags_for_clean_trace(self):
        """Trace with all principles passing and no issues -> empty list."""
        trace = _make_trace(qg_passed=True, qg_score=2.8)
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
    async def test_repeated_failure_flag(self):
        """Principle failing >= 3 times in 7 days triggers flag."""
        trace = _make_trace(gap_principles=["META-12"])
        with patch(
            "src.tracing.calibration_flags._recent_failure_count",
            new_callable=AsyncMock, return_value=4,
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
        assert len(flags) >= 1
        assert any("META-12" in f and "4 times" in f for f in flags)

    @pytest.mark.asyncio
    async def test_repeated_failure_below_threshold(self):
        """Principle failing only 2 times -> no flag."""
        trace = _make_trace(gap_principles=["META-12"])
        with patch(
            "src.tracing.calibration_flags._recent_failure_count",
            new_callable=AsyncMock, return_value=2,
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
        assert not any("META-12" in f and "threshold" in f for f in flags)

    @pytest.mark.asyncio
    async def test_intent_disparity_flag(self):
        """Intent avg significantly below overall avg triggers flag."""
        trace = _make_trace(intent="validating")
        with patch(
            "src.tracing.calibration_flags._recent_failure_count",
            new_callable=AsyncMock, return_value=0,
        ), patch(
            "src.tracing.calibration_flags._intent_avg_score",
            new_callable=AsyncMock, return_value=1.5,
        ), patch(
            "src.tracing.calibration_flags._overall_avg_score",
            new_callable=AsyncMock, return_value=2.3,
        ), patch(
            "src.tracing.calibration_flags._domain_avg_score",
            new_callable=AsyncMock, return_value=2.3,
        ):
            flags = await check_calibration_flags(trace)
        assert any("validating" in f and "intent" in f for f in flags)

    @pytest.mark.asyncio
    async def test_intent_disparity_within_tolerance(self):
        """Intent avg within 0.5 of overall -> no flag."""
        trace = _make_trace(intent="validating")
        with patch(
            "src.tracing.calibration_flags._recent_failure_count",
            new_callable=AsyncMock, return_value=0,
        ), patch(
            "src.tracing.calibration_flags._intent_avg_score",
            new_callable=AsyncMock, return_value=2.0,
        ), patch(
            "src.tracing.calibration_flags._overall_avg_score",
            new_callable=AsyncMock, return_value=2.3,
        ), patch(
            "src.tracing.calibration_flags._domain_avg_score",
            new_callable=AsyncMock, return_value=2.3,
        ):
            flags = await check_calibration_flags(trace)
        assert not any("intent" in f for f in flags)

    @pytest.mark.asyncio
    async def test_domain_disparity_flag(self):
        """Domain avg significantly below overall avg triggers flag."""
        trace = _make_trace(domain="robotics")
        with patch(
            "src.tracing.calibration_flags._recent_failure_count",
            new_callable=AsyncMock, return_value=0,
        ), patch(
            "src.tracing.calibration_flags._intent_avg_score",
            new_callable=AsyncMock, return_value=2.3,
        ), patch(
            "src.tracing.calibration_flags._overall_avg_score",
            new_callable=AsyncMock, return_value=2.5,
        ), patch(
            "src.tracing.calibration_flags._domain_avg_score",
            new_callable=AsyncMock, return_value=1.8,
        ):
            flags = await check_calibration_flags(trace)
        assert any("robotics" in f and "domain" in f for f in flags)

    @pytest.mark.asyncio
    async def test_quality_regression_flag(self):
        """Current run failed QG after previous run passed -> regression flag."""
        trace = _make_trace(qg_passed=False)
        with patch(
            "src.tracing.calibration_flags._recent_failure_count",
            new_callable=AsyncMock, return_value=0,
        ), patch(
            "src.tracing.calibration_flags._intent_avg_score",
            new_callable=AsyncMock, return_value=2.3,
        ), patch(
            "src.tracing.calibration_flags._overall_avg_score",
            new_callable=AsyncMock, return_value=2.3,
        ), patch(
            "src.tracing.calibration_flags._domain_avg_score",
            new_callable=AsyncMock, return_value=2.3,
        ), patch(
            "src.tracing.calibration_flags._previous_trace_for_project",
            new_callable=AsyncMock,
            return_value={"trace_id": "trc_prev", "quality_gate_passed": True},
        ):
            flags = await check_calibration_flags(trace)
        assert any("regression" in f.lower() for f in flags)

    @pytest.mark.asyncio
    async def test_no_regression_if_previous_also_failed(self):
        """Previous also failed -> no regression flag."""
        trace = _make_trace(qg_passed=False)
        with patch(
            "src.tracing.calibration_flags._recent_failure_count",
            new_callable=AsyncMock, return_value=0,
        ), patch(
            "src.tracing.calibration_flags._intent_avg_score",
            new_callable=AsyncMock, return_value=2.3,
        ), patch(
            "src.tracing.calibration_flags._overall_avg_score",
            new_callable=AsyncMock, return_value=2.3,
        ), patch(
            "src.tracing.calibration_flags._domain_avg_score",
            new_callable=AsyncMock, return_value=2.3,
        ), patch(
            "src.tracing.calibration_flags._previous_trace_for_project",
            new_callable=AsyncMock,
            return_value={"trace_id": "trc_prev", "quality_gate_passed": False},
        ):
            flags = await check_calibration_flags(trace)
        assert not any("regression" in f.lower() for f in flags)

    @pytest.mark.asyncio
    async def test_handles_no_quality_gate_stage(self):
        """Trace without quality_gate stage -> empty list."""
        trace = Trace(trace_id="trc_no_qg", status="complete")
        # No stages added at all
        flags = await check_calibration_flags(trace)
        assert flags == []

    @pytest.mark.asyncio
    async def test_handles_db_errors(self):
        """DB errors caught gracefully, returns empty list."""
        trace = _make_trace(gap_principles=["META-12"])
        with patch(
            "src.tracing.calibration_flags._recent_failure_count",
            new_callable=AsyncMock, side_effect=ConnectionError("DB down"),
        ), patch(
            "src.tracing.calibration_flags._intent_avg_score",
            new_callable=AsyncMock, side_effect=ConnectionError("DB down"),
        ), patch(
            "src.tracing.calibration_flags._overall_avg_score",
            new_callable=AsyncMock, side_effect=ConnectionError("DB down"),
        ), patch(
            "src.tracing.calibration_flags._domain_avg_score",
            new_callable=AsyncMock, side_effect=ConnectionError("DB down"),
        ):
            flags = await check_calibration_flags(trace)
        # Should not crash — returns whatever flags were collected before error
        assert isinstance(flags, list)

    @pytest.mark.asyncio
    async def test_multiple_flags(self):
        """Trace that triggers 3 different flags -> all 3 returned."""
        trace = _make_trace(
            qg_passed=False,
            gap_principles=["META-12"],
            intent="validating",
            domain="robotics",
        )
        with patch(
            "src.tracing.calibration_flags._recent_failure_count",
            new_callable=AsyncMock, return_value=5,
        ), patch(
            "src.tracing.calibration_flags._intent_avg_score",
            new_callable=AsyncMock, return_value=1.2,
        ), patch(
            "src.tracing.calibration_flags._overall_avg_score",
            new_callable=AsyncMock, return_value=2.5,
        ), patch(
            "src.tracing.calibration_flags._domain_avg_score",
            new_callable=AsyncMock, return_value=1.3,
        ), patch(
            "src.tracing.calibration_flags._previous_trace_for_project",
            new_callable=AsyncMock,
            return_value={"trace_id": "trc_prev", "quality_gate_passed": True},
        ):
            flags = await check_calibration_flags(trace)
        # Should have: repeated failure + intent disparity + domain disparity + regression
        assert len(flags) >= 3

    @pytest.mark.asyncio
    async def test_principle_scores_dict_triggers_gap_detection(self):
        """Principle scores as dict with low scores triggers gap principle detection."""
        trace = _make_trace(
            principle_scores={"META-1": 3, "META-12": 1, "MR-1": 0},
        )
        with patch(
            "src.tracing.calibration_flags._recent_failure_count",
            new_callable=AsyncMock, return_value=4,
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
        # META-12 and MR-1 both have scores < 2, should trigger repeated failure flags
        assert any("META-12" in f for f in flags)
        assert any("MR-1" in f for f in flags)
