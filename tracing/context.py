"""
Trace context manager for research pipeline observability.

Usage:
    # Start a trace at the beginning of a research run
    trace = TraceContext.start(project_id="uuid", project_name="cobot", query="...", intent="validating")

    # Inside any component, grab the current trace:
    trace = TraceContext.current()
    if trace:
        trace.record("intake", "classified_intent", {
            "what": "validating",
            "why": "User has existing hypothesis...",
            "confidence": 0.85,
            "alternatives_considered": ["exploring (0.10)"],
            "inputs": {"query_text": "..."}
        })

        # Record stage timing:
        trace.start_stage("collection")
        # ... do collection work ...
        trace.end_stage("collection", outputs={...})

    # Finish the trace (persists to file + Supabase in Run 4)
    result = await TraceContext.finish(trace)
"""

import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Context variable — async-safe, one per asyncio Task
_current_trace: ContextVar[Optional['Trace']] = ContextVar('_current_trace', default=None)

SCHEMA_VERSION = 1


@dataclass
class Decision:
    """A single decision recorded by a component."""
    decision: str           # What type of decision (e.g., "classified_intent")
    what: Any               # The decision made
    why: str                # Reasoning
    confidence: float       # 0.0-1.0
    alternatives_considered: List[str] = field(default_factory=list)
    inputs: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class StageTrace:
    """Trace data for a single pipeline stage."""
    name: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    decisions: List[Decision] = field(default_factory=list)
    outputs: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    prompts: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class Trace:
    """
    Full trace for a research run.

    Created by TraceContext.start(), populated during the run,
    persisted by TraceContext.finish().
    """
    trace_id: str
    project_id: Optional[str] = None
    project_name: Optional[str] = None

    # Run metadata
    query: Optional[str] = None
    intent: Optional[str] = None
    domain: Optional[str] = None
    report_type: Optional[str] = None
    research_type: Optional[str] = None

    # Timing
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None

    # Status
    status: str = "in_progress"  # in_progress|complete|incomplete|failed

    # Stages
    stages: Dict[str, StageTrace] = field(default_factory=dict)

    # Iterations (for retry loops)
    iterations: List[Dict[str, Any]] = field(default_factory=list)
    iteration_count: int = 1
    quality_gate_failures: int = 0

    # Outputs
    outputs: Dict[str, Any] = field(default_factory=dict)
    child_traces: List[Dict[str, Any]] = field(default_factory=list)

    # Internal timing (not serialized)
    _start_time: float = field(default=0.0, repr=False)
    _stage_start_times: Dict[str, float] = field(default_factory=dict, repr=False)

    # --- Recording Methods ---

    def start_stage(self, stage_name: str) -> None:
        """Mark the start of a pipeline stage."""
        now = datetime.now(timezone.utc).isoformat()
        self.stages[stage_name] = StageTrace(name=stage_name, started_at=now)
        self._stage_start_times[stage_name] = time.monotonic()

    def end_stage(
        self,
        stage_name: str,
        outputs: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Mark the end of a pipeline stage."""
        if stage_name not in self.stages:
            logger.warning(f"end_stage called for unstarted stage: {stage_name}")
            return
        stage = self.stages[stage_name]
        stage.completed_at = datetime.now(timezone.utc).isoformat()
        if stage_name in self._stage_start_times:
            stage.duration_seconds = time.monotonic() - self._stage_start_times[stage_name]
        if outputs:
            stage.outputs = outputs
        if error:
            stage.error = error

    def record(self, stage_name: str, decision_type: str, data: Dict[str, Any]) -> None:
        """
        Record a decision within a stage.

        Args:
            stage_name: Which pipeline stage (intake, rubric, collection, etc.)
            decision_type: What kind of decision (classified_intent, rubric_loaded, etc.)
            data: Decision data with keys: what, why, confidence, alternatives_considered, inputs
        """
        if stage_name not in self.stages:
            # Auto-create stage if not explicitly started
            self.stages[stage_name] = StageTrace(name=stage_name)

        decision = Decision(
            decision=decision_type,
            what=data.get("what"),
            why=data.get("why", ""),
            confidence=data.get("confidence", 1.0),
            alternatives_considered=data.get("alternatives_considered", []),
            inputs=data.get("inputs", {}),
        )
        self.stages[stage_name].decisions.append(decision)

    def record_evidence(self, stage_name: str, evidence_data: Dict[str, Any]) -> None:
        """Record evidence collection details (collected, filtered, kept)."""
        if stage_name not in self.stages:
            self.stages[stage_name] = StageTrace(name=stage_name)
        self.stages[stage_name].evidence = evidence_data

    def record_prompts(self, stage_name: str, prompts: Dict[str, str]) -> None:
        """Record full prompt text for a stage (system_prompt, user_message)."""
        if stage_name not in self.stages:
            self.stages[stage_name] = StageTrace(name=stage_name)
        self.stages[stage_name].prompts = prompts

    def record_iteration(self, iteration_data: Dict[str, Any]) -> None:
        """Record a quality gate iteration (for retry loops)."""
        self.iterations.append(iteration_data)
        self.iteration_count = len(self.iterations)
        if not iteration_data.get("passed", True):
            self.quality_gate_failures += 1

    def set_outputs(self, outputs: Dict[str, Any]) -> None:
        """Set the output file paths and references."""
        self.outputs = outputs

    def mark_complete(self) -> None:
        """Mark trace as successfully completed."""
        self.status = "complete"
        self.completed_at = datetime.now(timezone.utc).isoformat()
        if self._start_time:
            self.duration_seconds = time.monotonic() - self._start_time

    def mark_failed(self, error: str) -> None:
        """Mark trace as failed with error."""
        self.status = "failed"
        self.completed_at = datetime.now(timezone.utc).isoformat()
        if self._start_time:
            self.duration_seconds = time.monotonic() - self._start_time
        self.outputs["error"] = error

    def mark_incomplete(self) -> None:
        """Mark trace as incomplete (partial data saved)."""
        self.status = "incomplete"
        self.completed_at = datetime.now(timezone.utc).isoformat()
        if self._start_time:
            self.duration_seconds = time.monotonic() - self._start_time

    # --- Serialization ---

    def to_dict(self) -> Dict[str, Any]:
        """Serialize trace to dict for JSON output."""
        stages_dict = {}
        for name, stage in self.stages.items():
            stages_dict[name] = {
                "started_at": stage.started_at,
                "completed_at": stage.completed_at,
                "duration_seconds": stage.duration_seconds,
                "decisions": [
                    {
                        "decision": d.decision,
                        "what": d.what,
                        "why": d.why,
                        "confidence": d.confidence,
                        "alternatives_considered": d.alternatives_considered,
                        "inputs": d.inputs,
                        "timestamp": d.timestamp,
                    }
                    for d in stage.decisions
                ],
                "outputs": stage.outputs,
                "evidence": stage.evidence,
                "prompts": stage.prompts,
                "error": stage.error,
            }

        return {
            "schema_version": SCHEMA_VERSION,
            "trace_id": self.trace_id,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "run": {
                "query": self.query,
                "intent": self.intent,
                "domain": self.domain,
                "report_type": self.report_type,
                "research_type": self.research_type,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "duration_seconds": self.duration_seconds,
                "status": self.status,
            },
            "stages": stages_dict,
            "iterations": self.iterations,
            "iteration_count": self.iteration_count,
            "quality_gate_failures": self.quality_gate_failures,
            "outputs": self.outputs,
            "child_traces": self.child_traces,
            "metadata": {
                "trace_version": SCHEMA_VERSION,
                "generator": "ire-observability-v1",
            },
        }


def _write_calibration_alert_file(trace: 'Trace', flags: List[str]) -> None:
    """
    Append calibration alert entries to brain/projects/{project}/_calibration_alerts.md.

    Append-only — never overwrites existing content.
    Each entry includes timestamp, trace_id, and human-readable flag messages.
    """
    from src.config import BRAIN_DIR

    project_name = trace.project_name or "unknown"
    project_dir = BRAIN_DIR / "projects" / project_name
    project_dir.mkdir(parents=True, exist_ok=True)
    alert_file = project_dir / "_calibration_alerts.md"

    # Build the entry
    timestamp = trace.completed_at or datetime.now(timezone.utc).isoformat()
    lines = []
    lines.append(f"## {timestamp}")
    lines.append(f"**Trace:** `{trace.trace_id}`")
    lines.append("")
    for msg in flags:
        lines.append(f"- {msg}")
    lines.append("")
    lines.append("---")
    lines.append("")

    entry = "\n".join(lines)

    # Append (create with header if file doesn't exist)
    if not alert_file.exists():
        header = "# Calibration Alerts\n\nAuto-generated alerts when trace patterns suggest calibration attention.\n\n---\n\n"
        alert_file.write_text(header + entry)
    else:
        with open(alert_file, "a") as f:
            f.write(entry)

    logger.info(f"Calibration alert appended: {alert_file}")


class TraceContext:
    """
    Async-safe trace context manager.

    Manages the lifecycle of a trace through a research run.
    Uses Python's contextvars for async safety — each asyncio Task
    gets its own trace without thread-safety issues.
    """

    @staticmethod
    def start(
        project_id: Optional[str] = None,
        project_name: Optional[str] = None,
        query: Optional[str] = None,
        intent: Optional[str] = None,
        domain: Optional[str] = None,
        report_type: Optional[str] = None,
        research_type: Optional[str] = None,
        enabled: bool = True,
    ) -> 'Trace':
        """
        Start a new trace and set it as current context.

        If enabled=False, returns a _NoOpTrace that silently
        ignores all record() calls (zero overhead).
        """
        if not enabled:
            trace = _NoOpTrace()
            _current_trace.set(trace)
            return trace

        trace_id = f"trc_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        trace = Trace(
            trace_id=trace_id,
            project_id=project_id,
            project_name=project_name,
            query=query,
            intent=intent,
            domain=domain,
            report_type=report_type,
            research_type=research_type,
            started_at=datetime.now(timezone.utc).isoformat(),
            _start_time=time.monotonic(),
        )
        _current_trace.set(trace)
        logger.info(f"Trace started: {trace_id}")
        return trace

    @staticmethod
    def current() -> Optional['Trace']:
        """Get the current trace from async context. Returns None if no active trace."""
        return _current_trace.get()

    @staticmethod
    async def finish(trace: 'Trace', verbose: bool = False) -> Dict[str, Any]:
        """
        Finish a trace: mark status, write to file + Supabase, run summary + alerts, clear context.

        All post-persist steps (summary, alerts) are wrapped in try/except
        and never crash the research run.

        Args:
            trace: The Trace object to finish.
            verbose: If True, print full stage-by-stage breakdown instead of compact scorecard.

        Returns dict with trace_id, file_path (or file_error), supabase status,
        summary_file, and calibration_flags.
        """
        if isinstance(trace, _NoOpTrace):
            _current_trace.set(None)
            return {"saved": False, "reason": "tracing_disabled"}

        if trace.status == "in_progress":
            trace.mark_incomplete()

        result: Dict[str, Any] = {"trace_id": trace.trace_id, "status": trace.status}

        # Write trace file
        try:
            from src.tracing.writer import write_trace_file
            file_path = write_trace_file(trace)
            result["file_path"] = str(file_path)
            # Store file path in trace outputs for Supabase reference
            trace.outputs["trace_file_path"] = str(file_path)
        except Exception as e:
            logger.error(f"Failed to write trace file: {e}")
            result["file_error"] = str(e)

        # Write Supabase metadata
        try:
            from src.tracing.writer import write_trace_metadata
            await write_trace_metadata(trace)
            result["supabase"] = "saved"
        except Exception as e:
            logger.error(f"Failed to write trace metadata to Supabase: {e}")
            result["supabase_error"] = str(e)

        # --- Post-Run Summary (Phase 2) ---
        # Print terminal scorecard (compact or verbose)
        try:
            from src.tracing.summary import format_compact_summary, format_verbose_summary
            if verbose:
                print(format_verbose_summary(trace))
            else:
                print(format_compact_summary(trace))
        except Exception as e:
            logger.error(f"Failed to print trace summary: {e}")

        # Write detailed markdown summary file
        try:
            from src.tracing.summary import write_summary_file
            summary_path = write_summary_file(trace)
            if summary_path:
                result["summary_file"] = str(summary_path)
        except Exception as e:
            logger.error(f"Failed to write trace summary file: {e}")

        # --- Calibration Alerts (Phase 3) ---
        try:
            from src.tracing.calibration_flags import check_calibration_flags
            flags = await check_calibration_flags(trace)
            result["calibration_flags"] = flags

            if flags:
                # Print alerts to terminal
                for flag_msg in flags:
                    print(f"[CALIBRATION] {flag_msg}")

                # Set flagged_for_review in Supabase
                try:
                    from src.db.connection import get_connection
                    async with get_connection() as conn:
                        await conn.execute(
                            "UPDATE traces SET flagged_for_review = TRUE WHERE trace_id = $1",
                            trace.trace_id,
                        )
                    result["flagged_for_review"] = True
                except Exception as e:
                    logger.error(f"Failed to set flagged_for_review: {e}")

                # Append to project's _calibration_alerts.md
                try:
                    _write_calibration_alert_file(trace, flags)
                except Exception as e:
                    logger.error(f"Failed to write calibration alert file: {e}")
        except Exception as e:
            logger.error(f"Failed to run calibration flags: {e}")
            result["calibration_flags"] = []

        # Clear context
        _current_trace.set(None)
        logger.info(f"Trace finished: {trace.trace_id} (status={trace.status})")

        return result


class _NoOpTrace(Trace):
    """Trace that does nothing — used when tracing is disabled."""

    def __init__(self):
        super().__init__(trace_id="noop", status="disabled")

    def start_stage(self, stage_name: str) -> None:
        pass

    def end_stage(self, stage_name: str, outputs: Optional[Dict[str, Any]] = None,
                  error: Optional[str] = None) -> None:
        pass

    def record(self, stage_name: str, decision_type: str, data: Dict[str, Any]) -> None:
        pass

    def record_evidence(self, stage_name: str, evidence_data: Dict[str, Any]) -> None:
        pass

    def record_prompts(self, stage_name: str, prompts: Dict[str, str]) -> None:
        pass

    def record_iteration(self, iteration_data: Dict[str, Any]) -> None:
        pass

    def set_outputs(self, outputs: Dict[str, Any]) -> None:
        pass

    def mark_complete(self) -> None:
        pass

    def mark_failed(self, error: str) -> None:
        pass

    def mark_incomplete(self) -> None:
        pass


@asynccontextmanager
async def traced_research(
    query: str,
    project_id=None,
    project_name=None,
    intent=None,
    domain=None,
    report_type=None,
    research_type=None,
    verbose=False,
    enabled=True,
):
    """
    Async context manager for wrapping any research workflow with tracing.

    Usage:
        async with traced_research(query="...", project_name="edtech") as trace:
            result = await synthesize_report(...)  # auto-records to trace
            score = await score_report(...)        # auto-records to trace

    Handles:
    - Creating and setting the trace as current context
    - Marking complete on normal exit
    - Marking failed on exception (then re-raises)
    - Always calling finish() for persistence
    """
    trace = TraceContext.start(
        project_id=project_id,
        project_name=project_name,
        query=query,
        intent=intent,
        domain=domain,
        report_type=report_type,
        research_type=research_type,
        enabled=enabled,
    )
    try:
        yield trace
        if trace.status == "in_progress":
            trace.mark_complete()
    except Exception as e:
        try:
            trace.mark_failed(str(e))
        except Exception:
            pass
        raise
    finally:
        try:
            await TraceContext.finish(trace, verbose=verbose)
        except Exception:
            pass
