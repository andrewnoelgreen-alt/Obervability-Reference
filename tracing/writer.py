"""
Trace persistence layer — writes traces to files and Supabase.

Two write paths:
1. write_trace_file: Full trace JSON to brain/projects/{project}/_traces/{trace_id}.json
2. write_trace_metadata: Metadata row to Supabase traces table (queryable)
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from src.config import BRAIN_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _extract_stage_duration(trace: 'Trace', stage_name: str) -> Optional[float]:
    """Extract duration_seconds from a stage, or None if stage doesn't exist."""
    stage = trace.stages.get(stage_name)
    if stage and stage.duration_seconds is not None:
        return stage.duration_seconds
    return None


def _extract_quality_gate_data(trace: 'Trace') -> Dict[str, Any]:
    """
    Extract quality gate metadata from trace for Supabase row.

    Returns dict with: principle_scores, gap_principles, strength_principles,
    overall_quality_score, quality_gate_passed. All default to None if missing.
    """
    result: Dict[str, Any] = {
        "principle_scores": None,
        "gap_principles": None,
        "strength_principles": None,
        "overall_quality_score": None,
        "quality_gate_passed": None,
    }

    qg_stage = trace.stages.get("quality_gate")
    if not qg_stage:
        return result

    outputs = qg_stage.outputs or {}

    # quality_gate_passed
    if "passed" in outputs:
        result["quality_gate_passed"] = bool(outputs["passed"])

    # overall_quality_score
    if "overall_score" in outputs:
        result["overall_quality_score"] = float(outputs["overall_score"])

    # principle_scores: may be list of dicts or already a dict
    raw_scores = outputs.get("principle_scores")
    if isinstance(raw_scores, list):
        # Convert [{id: "META-1", score: 3}, ...] to {"META-1": 3, ...}
        result["principle_scores"] = {
            item["id"]: item["score"]
            for item in raw_scores
            if isinstance(item, dict) and "id" in item and "score" in item
        }
    elif isinstance(raw_scores, dict):
        result["principle_scores"] = raw_scores

    # gap_principles and strength_principles from trace top-level or outputs
    if "gap_principles" in outputs:
        result["gap_principles"] = outputs["gap_principles"]
    if "strength_principles" in outputs:
        result["strength_principles"] = outputs["strength_principles"]

    return result


def _extract_synthesis_data(trace: 'Trace') -> Dict[str, Any]:
    """Extract synthesis metadata (model, tokens, cost) from trace."""
    result: Dict[str, Any] = {
        "synthesis_model": None,
        "synthesis_input_tokens": None,
        "synthesis_output_tokens": None,
        "synthesis_cost_usd": None,
    }

    synth_stage = trace.stages.get("synthesis")
    if not synth_stage:
        return result

    outputs = synth_stage.outputs or {}

    result["synthesis_model"] = outputs.get("model")

    token_usage = outputs.get("token_usage", {})
    if isinstance(token_usage, dict):
        result["synthesis_input_tokens"] = token_usage.get("input_tokens")
        result["synthesis_output_tokens"] = token_usage.get("output_tokens")

    if "cost_usd" in outputs:
        result["synthesis_cost_usd"] = outputs["cost_usd"]

    return result


def _extract_enriched_trace_data(trace: 'Trace') -> Dict[str, Any]:
    """Extract enriched calibration/retrieval columns from trace outputs (MEM-10)."""
    result: Dict[str, Any] = {
        "tier_config": None,
        "rubric_scores": None,
        "principle_breakdown": None,
        "qg_iteration_count": None,
        "retrieval_method": None,
        "evidence_retrieved": None,
        "evidence_used": None,
        "retrieval_tokens": None,
        "retrieval_cost_usd": None,
    }

    outputs = trace.outputs or {}

    result["tier_config"] = outputs.get("tier_config")
    result["qg_iteration_count"] = outputs.get("qg_iteration_count")
    result["retrieval_method"] = outputs.get("retrieval_method")
    result["evidence_retrieved"] = outputs.get("evidence_retrieved")
    result["evidence_used"] = outputs.get("evidence_used")
    result["retrieval_tokens"] = outputs.get("retrieval_tokens")
    result["retrieval_cost_usd"] = outputs.get("retrieval_cost_usd")

    # rubric_scores is JSONB — may be dict
    rubric_scores = outputs.get("rubric_scores")
    if isinstance(rubric_scores, dict):
        result["rubric_scores"] = rubric_scores
    # principle_breakdown is JSONB — may be dict or list
    principle_breakdown = outputs.get("principle_breakdown")
    if principle_breakdown is not None:
        result["principle_breakdown"] = principle_breakdown

    return result


def _extract_evidence_data(trace: 'Trace') -> Dict[str, Any]:
    """Extract evidence stats from the collection stage."""
    result: Dict[str, Any] = {
        "evidence_collected": None,
        "evidence_passed": None,
        "evidence_filtered": None,
    }

    coll_stage = trace.stages.get("collection")
    if not coll_stage:
        return result

    # Evidence data stored in stage.evidence
    evidence = coll_stage.evidence or {}
    if "collected_count" in evidence:
        result["evidence_collected"] = evidence["collected_count"]

    # Outputs may have passed/filtered
    outputs = coll_stage.outputs or {}
    if "evidence_passed" in outputs:
        result["evidence_passed"] = outputs["evidence_passed"]
    if "evidence_filtered" in outputs:
        result["evidence_filtered"] = outputs["evidence_filtered"]

    return result


def write_trace_file(trace: 'Trace') -> Path:
    """
    Write full trace JSON to brain/projects/{project_name}/_traces/{trace_id}.json.

    Args:
        trace: The completed Trace object

    Returns:
        Path to the written file
    """
    project_name = trace.project_name or "unknown"
    traces_dir = BRAIN_DIR / "projects" / project_name / "_traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    file_path = traces_dir / f"{trace.trace_id}.json"
    trace_dict = trace.to_dict()

    with open(file_path, "w") as f:
        json.dump(trace_dict, f, indent=2, default=str)

    logger.info(f"Trace file written: {file_path}")
    return file_path


async def write_trace_metadata(trace: 'Trace') -> None:
    """
    Write trace metadata row to Supabase traces table.

    Extracts queryable fields from the trace and inserts a row.
    Uses parameterized queries — never string interpolation.

    Args:
        trace: The completed Trace object
    """
    from src.db.connection import get_connection

    # Extract structured data from stages
    qg_data = _extract_quality_gate_data(trace)
    synth_data = _extract_synthesis_data(trace)
    evidence_data = _extract_evidence_data(trace)
    enriched_data = _extract_enriched_trace_data(trace)

    # Prepare principle_scores as JSON string for JSONB column
    principle_scores_json = (
        json.dumps(qg_data["principle_scores"])
        if qg_data["principle_scores"] is not None
        else "{}"
    )

    # Build the output file paths array
    output_file_paths = None
    if trace.outputs:
        paths = []
        for key, val in trace.outputs.items():
            if isinstance(val, str) and ("/" in val or "\\" in val):
                paths.append(val)
        output_file_paths = paths if paths else None

    # Prepare enriched JSONB fields
    rubric_scores_json = (
        json.dumps(enriched_data["rubric_scores"])
        if enriched_data["rubric_scores"] is not None
        else None
    )
    principle_breakdown_json = (
        json.dumps(enriched_data["principle_breakdown"])
        if enriched_data["principle_breakdown"] is not None
        else None
    )

    query = """
        INSERT INTO traces (
            trace_id, project_id, query, intent, domain, report_type, research_type,
            status, quality_gate_passed, overall_quality_score,
            started_at, completed_at, duration_seconds,
            intake_duration, rubric_duration, collection_duration,
            synthesis_duration, quality_gate_duration,
            evidence_collected, evidence_passed, evidence_filtered,
            synthesis_model, synthesis_input_tokens, synthesis_output_tokens,
            synthesis_cost_usd,
            principle_scores, gap_principles, strength_principles,
            iteration_count, quality_gate_failures,
            trace_file_path, report_file_path, output_file_paths,
            flagged_for_review, review_notes,
            tier_config, rubric_scores, principle_breakdown,
            qg_iteration_count, retrieval_method,
            evidence_retrieved, evidence_used,
            retrieval_tokens, retrieval_cost_usd
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7,
            $8, $9, $10,
            $11, $12, $13,
            $14, $15, $16,
            $17, $18,
            $19, $20, $21,
            $22, $23, $24,
            $25,
            $26, $27, $28,
            $29, $30,
            $31, $32, $33,
            $34, $35,
            $36, $37, $38,
            $39, $40,
            $41, $42,
            $43, $44
        )
    """

    # Convert project_id to UUID if present
    project_id = None
    if trace.project_id:
        import uuid as _uuid
        try:
            project_id = _uuid.UUID(trace.project_id)
        except (ValueError, AttributeError):
            project_id = None

    # Parse started_at / completed_at as datetime
    from datetime import datetime, timezone

    started_at = None
    if trace.started_at:
        try:
            started_at = datetime.fromisoformat(trace.started_at)
        except (ValueError, TypeError):
            started_at = datetime.now(timezone.utc)

    completed_at = None
    if trace.completed_at:
        try:
            completed_at = datetime.fromisoformat(trace.completed_at)
        except (ValueError, TypeError):
            pass

    async with get_connection() as conn:
        await conn.execute(
            query,
            trace.trace_id,                                 # $1
            project_id,                                     # $2
            trace.query,                                    # $3
            trace.intent,                                   # $4
            trace.domain,                                   # $5
            trace.report_type,                              # $6
            trace.research_type,                            # $7
            trace.status,                                   # $8
            qg_data["quality_gate_passed"],                 # $9
            qg_data["overall_quality_score"],               # $10
            started_at,                                     # $11
            completed_at,                                   # $12
            trace.duration_seconds,                         # $13
            _extract_stage_duration(trace, "intake"),       # $14
            _extract_stage_duration(trace, "rubric"),       # $15
            _extract_stage_duration(trace, "collection"),   # $16
            _extract_stage_duration(trace, "synthesis"),    # $17
            _extract_stage_duration(trace, "quality_gate"), # $18
            evidence_data["evidence_collected"],            # $19
            evidence_data["evidence_passed"],               # $20
            evidence_data["evidence_filtered"],             # $21
            synth_data["synthesis_model"],                  # $22
            synth_data["synthesis_input_tokens"],           # $23
            synth_data["synthesis_output_tokens"],          # $24
            synth_data["synthesis_cost_usd"],               # $25
            principle_scores_json,                          # $26
            qg_data["gap_principles"],                      # $27
            qg_data["strength_principles"],                 # $28
            trace.iteration_count,                          # $29
            trace.quality_gate_failures,                    # $30
            trace.outputs.get("trace_file_path"),           # $31
            trace.outputs.get("report_file_path") or trace.outputs.get("report_path"),  # $32
            output_file_paths,                              # $33
            False,                                          # $34 flagged_for_review
            None,                                           # $35 review_notes
            enriched_data["tier_config"],                   # $36
            rubric_scores_json,                             # $37
            principle_breakdown_json,                       # $38
            enriched_data["qg_iteration_count"],            # $39
            enriched_data["retrieval_method"],              # $40
            enriched_data["evidence_retrieved"],            # $41
            enriched_data["evidence_used"],                 # $42
            enriched_data["retrieval_tokens"],              # $43
            enriched_data["retrieval_cost_usd"],            # $44
        )

    logger.info(f"Trace metadata written to Supabase: {trace.trace_id}")
