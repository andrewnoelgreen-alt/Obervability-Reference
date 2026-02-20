# Observability System — Technical Specification

> Synthesized from user design questionnaire answers + codebase architecture audit.
> This is the build blueprint. All decisions are final unless noted.

---

## 1. Purpose

**Primary goal:** Know what logic isn't adding up to good outcomes within the IRE system that is creating research, and other extracted, synthesized and outdated data 

**Core use case:** Calibration support — trace every decision in the research pipeline so you can isolate which component, principle, or data input is degrading report quality. Without this, tuning the rubric, intent activation, or evidence scoring is guesswork.

**Priority ranking of use cases:**
1. Calibration support ("show me all VALIDATING intent runs")
2. Debugging failed reports ("why did this specific report suck?")
3. Pattern analysis across reports ("which principle or piece of the logic chain fails most often?")
4. Audit trail ("prove the report was generated properly or why it wasn't")
5. Performance monitoring ("how long does each stage take?")
6. Cost tracking ("how many LLM calls per research run?")

---

## 2. Architecture Overview

```
Research Run Start
       │
       ▼
┌─────────────────────────────────────┐
│  TraceContext.start(project_id, ...) │  ← Created once per research run
│  Sets context-local trace object     │
└──────────────┬──────────────────────┘
               │
    ┌──────────┼──────────┬──────────────┬──────────────┐
    ▼          ▼          ▼              ▼              ▼
 Intake    Rubric    Collection    Synthesis    QualityGate
    │          │          │              │              │
    │  trace.record()  at each decision point          │
    │          │          │              │              │
    └──────────┴──────────┴──────────────┴──────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  TraceContext.finish()               │
│  1. Write full trace → file          │
│  2. Write metadata → Supabase        │
│  3. Link trace ↔ report (both dirs)  │
└─────────────────────────────────────┘
```

### Dual Storage (Option 4)

| Layer | What | Where | Purpose |
|-------|------|-------|---------|
| **Metadata** | IDs, scores, intent, domain, status, timing, principle scores | Supabase `traces` table | Fast queries for calibration |
| **Full trace** | Complete decision log, prompts, evidence, reasoning | `brain/projects/{project}/_traces/{trace_id}.json` | Deep debugging, full audit |

### Trace Lifecycle

1. **Start** — `TraceContext.start()` creates trace with unique ID, attaches to async context
2. **Record** — Components call `trace.record(event, data)` at decision points
3. **Finish** — `TraceContext.finish()` writes file + Supabase row
4. **Failure** — If research run crashes, partial trace saved with `status: "incomplete"`
5. **Toggle** — Tracing can be disabled per-project via config flag

---

## 3. Data Models

### 3.1 Trace (Full — File Storage)

Format: **Structured JSON with markdown in reasoning fields.**

```json
{
  "schema_version": 1,
  "trace_id": "trc_20260213_abc123def",
  "project_id": "uuid-here",
  "project_name": "cobot",

  "run": {
    "query": "What is the competitive landscape for collaborative robots?",
    "intent": "validating",
    "domain": "robotics",
    "report_type": "market_research",
    "research_type": "competitive",
    "started_at": "2026-02-13T10:30:00Z",
    "completed_at": "2026-02-13T10:34:22Z",
    "duration_seconds": 262.0,
    "status": "complete",
    "tracing_enabled": true
  },

  "stages": {
    "intake": {
      "started_at": "...",
      "duration_seconds": 5.2,
      "decisions": [
        {
          "decision": "classified_intent",
          "what": "validating",
          "why": "User has existing hypothesis about market position, wants confirmation or challenge",
          "confidence": 0.85,
          "alternatives_considered": ["exploring (0.10)", "executing (0.05)"],
          "inputs": {"query_text": "...", "user_context": "..."}
        },
        {
          "decision": "bias_classification",
          "what": "confirmation_bias",
          "why": "Query assumes collaborative robots are winning — needs testing",
          "confidence": 0.72,
          "alternatives_considered": ["no_bias (0.28)"],
          "inputs": {"query_text": "..."}
        }
      ],
      "outputs": {
        "research_brief": "...",
        "intent": "validating",
        "bias_flags": ["confirmation_bias"]
      }
    },

    "rubric": {
      "started_at": "...",
      "duration_seconds": 0.3,
      "decisions": [
        {
          "decision": "rubric_loaded",
          "what": "21 principles (13 META + 8 MR)",
          "why": "report_type=market_research, domain=None, intent=validating",
          "confidence": 1.0,
          "alternatives_considered": [],
          "inputs": {"report_type": "market_research", "domain": null, "intent": "validating"}
        },
        {
          "decision": "intent_activation",
          "what": "activated: [challenge, skepticism, distinguish_obvious, inline_source, source_attribution, real_voices, domain_authorities]; deactivated: [quick_start, personalize]",
          "why": "Validating intent emphasizes critical evaluation and source rigor",
          "confidence": 1.0,
          "alternatives_considered": [],
          "inputs": {"intent": "validating"}
        }
      ],
      "outputs": {
        "total_principles": 21,
        "activated_count": 19,
        "deactivated_count": 2,
        "critical_principles": ["META-1", "META-3", "META-5"],
        "principle_list": ["..."]
      }
    },

    "collection": {
      "started_at": "...",
      "duration_seconds": 45.8,
      "decisions": [
        {
          "decision": "source_selection",
          "what": "exa (primary), reddit (supplemental)",
          "why": "competitive research benefits from industry reports + community sentiment",
          "confidence": 0.90,
          "alternatives_considered": ["add linkedin (0.60)", "add twitter (0.40)"],
          "inputs": {"research_type": "competitive", "available_sources": ["exa", "reddit"]}
        },
        {
          "decision": "query_formulation",
          "what": ["collaborative robot market share 2025 2026", "cobot competition universal robots fanuc"],
          "why": "Two-pronged: market data + competitive players",
          "confidence": 0.80,
          "alternatives_considered": ["single broad query"],
          "inputs": {"research_brief": "..."}
        }
      ],
      "evidence": {
        "collected_count": 28,
        "by_source": {"exa": 22, "reddit": 6},
        "query_terms_used": ["collaborative robot market share...", "cobot competition..."],
        "filtered_out": [
          {"url": "https://example.com/old-article", "reason": "published 2019, below recency threshold", "scores": {"relevance": 0.3, "recency": 0.1}},
          {"url": "https://spam-site.com/robots", "reason": "low credibility score", "scores": {"credibility": 0.2}}
        ],
        "kept": [
          {"url": "https://ifr.org/report-2025", "title": "IFR World Robotics 2025", "scores": {"relevance": 0.95, "specificity": 0.88, "credibility": 0.97, "recency": 0.90, "composite": 0.93}}
        ]
      },
      "outputs": {
        "evidence_passed": 18,
        "evidence_filtered": 10,
        "quality_threshold_used": 0.5
      }
    },

    "synthesis": {
      "started_at": "...",
      "duration_seconds": 180.5,
      "decisions": [
        {
          "decision": "prompt_construction",
          "what": "module synthesis prompt + rubric injection (19 active principles)",
          "why": "Standard flow: module prompt provides structure, rubric provides quality guardrails",
          "confidence": 1.0,
          "alternatives_considered": [],
          "inputs": {"module": "market_research", "rubric_injected": true, "active_principles": 19}
        }
      ],
      "prompts": {
        "system_prompt": "<<full system prompt text including rubric>>",
        "user_message": "<<full user message with evidence>>"
      },
      "model": "claude-opus-4-6",
      "token_usage": {
        "input_tokens": 45000,
        "output_tokens": 3800,
        "total_tokens": 48800
      },
      "cost_usd": 0.32,
      "outputs": {
        "report_length_chars": 15200,
        "sources_cited": 12,
        "sections_generated": 7
      }
    },

    "quality_gate": {
      "started_at": "...",
      "duration_seconds": 30.2,
      "decisions": [
        {
          "decision": "quality_assessment",
          "what": "PASS (overall: 2.4/3.0, threshold: 2.0)",
          "why": "19/21 principles scored >= 2, 2 gaps identified",
          "confidence": 0.88,
          "alternatives_considered": ["FAIL + retry if overall < 2.0"],
          "inputs": {"report_text": "<<hash or length>>", "rubric": "21 principles"}
        }
      ],
      "principle_scores": [
        {"id": "META-1", "name": "No Generics", "score": 3, "feedback": "Report uses specific market data throughout"},
        {"id": "META-12", "name": "Source Attribution", "score": 1, "feedback": "Several claims lack inline citations"}
      ],
      "overall_score": 2.4,
      "passing": true,
      "threshold": 2.0,
      "gaps": [
        {"id": "META-12", "score": 1, "feedback": "..."}
      ],
      "strengths": [
        {"id": "META-1", "score": 3, "feedback": "..."}
      ],
      "outputs": {
        "passed": true,
        "iteration": 1
      }
    }
  },

  "iterations": [],

  "outputs": {
    "report_path": "brain/projects/cobot/artifacts/competitive-landscape-2026-02-13.md",
    "evidence_paths": ["brain/projects/cobot/evidence/competitive/..."],
    "knowledge_paths": []
  },

  "child_traces": [],

  "metadata": {
    "trace_version": 1,
    "generator": "ire-observability-v1",
    "tracing_overhead_ms": 45
  }
}
```

### 3.2 Trace Metadata (Supabase — Queryable)

```sql
-- Migration: _023_trace_system.py

CREATE TABLE IF NOT EXISTS traces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id VARCHAR(100) UNIQUE NOT NULL,      -- "trc_20260213_abc123def"
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,

    -- Run identifiers
    query TEXT,
    intent VARCHAR(50),                          -- exploring|validating|executing|learning
    domain VARCHAR(100),
    report_type VARCHAR(100),                    -- market_research, roadmap, etc.
    research_type VARCHAR(100),                  -- competitive, market, general, etc.

    -- Status
    status VARCHAR(20) NOT NULL DEFAULT 'in_progress',  -- in_progress|complete|incomplete|failed
    quality_gate_passed BOOLEAN,
    overall_quality_score FLOAT,                 -- 0.0-3.0

    -- Timing
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    duration_seconds FLOAT,

    -- Per-stage timing (seconds)
    intake_duration FLOAT,
    rubric_duration FLOAT,
    collection_duration FLOAT,
    synthesis_duration FLOAT,
    quality_gate_duration FLOAT,

    -- Evidence stats
    evidence_collected INT,
    evidence_passed INT,
    evidence_filtered INT,

    -- Synthesis stats
    synthesis_model VARCHAR(100),
    synthesis_input_tokens INT,
    synthesis_output_tokens INT,
    synthesis_cost_usd FLOAT,

    -- Quality scores (denormalized for fast queries)
    -- Store as JSONB: {"META-1": 3, "META-2": 2, "MR-1": 3, ...}
    principle_scores JSONB DEFAULT '{}',
    gap_principles TEXT[],                       -- ["META-12", "META-5"]
    strength_principles TEXT[],                  -- ["META-1", "META-3"]

    -- Iteration tracking
    iteration_count INT DEFAULT 1,
    quality_gate_failures INT DEFAULT 0,

    -- File references
    trace_file_path VARCHAR(500),                -- brain/projects/{project}/_traces/{id}.json
    report_file_path VARCHAR(500),               -- brain/projects/{project}/artifacts/report.md
    output_file_paths TEXT[],                    -- All output files

    -- Calibration support
    flagged_for_review BOOLEAN DEFAULT FALSE,
    review_notes TEXT,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for the 6 calibration query types
CREATE INDEX idx_traces_intent ON traces(intent);
CREATE INDEX idx_traces_domain ON traces(domain);
CREATE INDEX idx_traces_report_type ON traces(report_type);
CREATE INDEX idx_traces_status ON traces(status);
CREATE INDEX idx_traces_quality_gate ON traces(quality_gate_passed);
CREATE INDEX idx_traces_project ON traces(project_id);
CREATE INDEX idx_traces_started ON traces(started_at DESC);
CREATE INDEX idx_traces_overall_score ON traces(overall_quality_score);
CREATE INDEX idx_traces_flagged ON traces(flagged_for_review) WHERE flagged_for_review = TRUE;

-- GIN index for principle_scores JSONB queries
CREATE INDEX idx_traces_principle_scores ON traces USING GIN (principle_scores);

-- Trigger for updated_at
DROP TRIGGER IF EXISTS update_traces_updated_at ON traces;
CREATE TRIGGER update_traces_updated_at
    BEFORE UPDATE ON traces
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Calibration views

-- Runs with low-scoring principles
CREATE OR REPLACE VIEW trace_quality_gaps AS
SELECT
    t.trace_id,
    t.project_id,
    t.intent,
    t.domain,
    t.report_type,
    t.overall_quality_score,
    t.quality_gate_passed,
    t.gap_principles,
    t.principle_scores,
    t.started_at,
    p.name as project_name
FROM traces t
LEFT JOIN projects p ON p.id = t.project_id
WHERE t.status = 'complete'
  AND array_length(t.gap_principles, 1) > 0
ORDER BY t.started_at DESC;

-- Intent distribution
CREATE OR REPLACE VIEW trace_intent_summary AS
SELECT
    intent,
    COUNT(*) as total_runs,
    COUNT(*) FILTER (WHERE quality_gate_passed = TRUE) as passed,
    COUNT(*) FILTER (WHERE quality_gate_passed = FALSE) as failed,
    AVG(overall_quality_score) as avg_quality,
    AVG(duration_seconds) as avg_duration,
    AVG(synthesis_cost_usd) as avg_cost
FROM traces
WHERE status = 'complete'
GROUP BY intent
ORDER BY total_runs DESC;
```

### 3.3 Trace-to-Output Linking (Both Directions)

**Report → Trace** (YAML frontmatter in report markdown):
```yaml
---
trace_id: trc_20260213_abc123def
generated_at: 2026-02-13T10:34:22Z
quality_score: 2.4
quality_gate: passed
---
```

**Trace → Report** (in trace file + Supabase):
- `outputs.report_path` in JSON trace file
- `report_file_path` column in Supabase `traces` table

### 3.4 Parent-Child Trace Structure

For multi-output research runs:
```json
{
  "trace_id": "trc_20260213_parent",
  "child_traces": [
    {"trace_id": "trc_20260213_child_report", "output_type": "report", "path": "..."},
    {"trace_id": "trc_20260213_child_evidence", "output_type": "evidence_pack", "path": "..."}
  ]
}
```

Parent trace contains the full decision log. Child traces reference back to parent via `parent_trace_id`.

### 3.5 Retry/Loop Correlation

Single trace with iterations array:
```json
{
  "iterations": [
    {
      "iteration": 1,
      "quality_gate_result": "FAIL",
      "overall_score": 1.6,
      "gaps": ["META-1", "META-12"],
      "synthesis_tokens": 48000,
      "synthesis_cost": 0.32
    },
    {
      "iteration": 2,
      "quality_gate_result": "PASS",
      "overall_score": 2.4,
      "gaps": ["META-12"],
      "synthesis_tokens": 52000,
      "synthesis_cost": 0.35,
      "changes_from_previous": "Added specific market data for META-1, improved citations"
    }
  ],
  "iteration_count": 2,
  "quality_gate_failures": 1
}
```

---

## 4. TracingComponent Interface

All components that emit traces must implement this interface.

```python
# src/tracing/interface.py

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class TracingComponent(ABC):
    """
    Interface for components that emit trace data.

    Every component in the research pipeline that makes decisions
    must implement this to participate in the tracing system.
    """

    @property
    @abstractmethod
    def component_name(self) -> str:
        """Unique name for this component in traces (e.g., 'rubric_loader')."""
        ...

    @abstractmethod
    def get_trace_schema(self) -> Dict[str, Any]:
        """
        Return the schema of decisions this component can emit.

        Used for documentation and validation. Example:
        {
            "decisions": ["rubric_loaded", "intent_activation"],
            "outputs": ["total_principles", "activated_count"]
        }
        """
        ...
```

Components don't need to inherit from this ABC directly — they just need to call `trace.record()` at decision points. The interface exists to document the contract and validate during testing.

---

## 5. TraceContext — The Runtime Engine

```python
# src/tracing/context.py

"""
Trace context manager for research pipeline observability.

Usage:
    async with TraceContext.start(project_id="...", query="...") as trace:
        # Inside any component:
        trace = TraceContext.current()
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

    # On exit: writes file + Supabase metadata automatically
"""

import asyncio
import json
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import BRAIN_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Context variable — async-safe, one per task
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

    # Internal timing
    _start_time: float = field(default=0.0, repr=False)
    _stage_start_times: Dict[str, float] = field(default_factory=dict, repr=False)

    # --- Recording Methods ---

    def start_stage(self, stage_name: str) -> None:
        """Mark the start of a pipeline stage."""
        now = datetime.now(timezone.utc).isoformat()
        self.stages[stage_name] = StageTrace(name=stage_name, started_at=now)
        self._stage_start_times[stage_name] = time.monotonic()

    def end_stage(self, stage_name: str, outputs: Optional[Dict] = None,
                  error: Optional[str] = None) -> None:
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
            inputs=data.get("inputs", {})
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

        If enabled=False, returns a no-op trace that silently
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
    async def finish(trace: 'Trace') -> Dict[str, Any]:
        """
        Finish a trace: write file + Supabase metadata.

        Returns dict with file_path and supabase status.
        """
        if isinstance(trace, _NoOpTrace):
            return {"saved": False, "reason": "tracing_disabled"}

        if trace.status == "in_progress":
            trace.mark_incomplete()

        result = {"trace_id": trace.trace_id}

        # 1. Write trace file
        try:
            file_path = _write_trace_file(trace)
            result["file_path"] = str(file_path)
            trace.outputs["trace_file_path"] = str(file_path)
        except Exception as e:
            logger.error(f"Failed to write trace file: {e}")
            result["file_error"] = str(e)

        # 2. Write Supabase metadata
        try:
            await _write_trace_metadata(trace)
            result["supabase"] = "saved"
        except Exception as e:
            logger.error(f"Failed to write trace metadata to Supabase: {e}")
            result["supabase_error"] = str(e)

        # 3. Clear context
        _current_trace.set(None)
        logger.info(f"Trace finished: {trace.trace_id} (status={trace.status})")

        return result


class _NoOpTrace(Trace):
    """Trace that does nothing — used when tracing is disabled."""

    def __init__(self):
        super().__init__(trace_id="noop", status="disabled")

    def start_stage(self, stage_name): pass
    def end_stage(self, stage_name, **kwargs): pass
    def record(self, stage_name, decision_type, data): pass
    def record_evidence(self, stage_name, data): pass
    def record_prompts(self, stage_name, prompts): pass
    def record_iteration(self, data): pass
    def set_outputs(self, outputs): pass
    def mark_complete(self): pass
    def mark_failed(self, error): pass
    def mark_incomplete(self): pass


def _write_trace_file(trace: Trace) -> Path:
    """Write full trace JSON to brain/projects/{project}/_traces/."""
    project_name = trace.project_name or "unknown"
    traces_dir = Path(BRAIN_DIR) / "projects" / project_name / "_traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    file_path = traces_dir / f"{trace.trace_id}.json"
    with open(file_path, "w") as f:
        json.dump(trace.to_dict(), f, indent=2, default=str)

    return file_path


async def _write_trace_metadata(trace: Trace) -> None:
    """Write trace metadata to Supabase traces table."""
    from src.db.connection import get_connection

    # Build principle_scores JSONB from quality_gate stage
    principle_scores = {}
    gap_principles = []
    strength_principles = []
    overall_score = None
    quality_gate_passed = None

    qg = trace.stages.get("quality_gate")
    if qg and qg.outputs:
        quality_gate_passed = qg.outputs.get("passed")
        # Extract from decisions
        for d in qg.decisions:
            if d.decision == "quality_assessment":
                pass  # scores come from stage data
        # If principle_scores recorded in evidence or outputs
        if "principle_scores" in qg.outputs:
            for ps in qg.outputs["principle_scores"]:
                principle_scores[ps["id"]] = ps["score"]
                if ps["score"] < 2:
                    gap_principles.append(ps["id"])
                elif ps["score"] == 3:
                    strength_principles.append(ps["id"])
            overall_score = qg.outputs.get("overall_score")

    # Build stage durations
    stage_durations = {}
    for name, stage in trace.stages.items():
        if stage.duration_seconds is not None:
            stage_durations[name] = stage.duration_seconds

    # Synthesis stats
    synth = trace.stages.get("synthesis")
    synth_model = None
    synth_input = None
    synth_output = None
    synth_cost = None
    if synth:
        synth_model = synth.outputs.get("model") or (
            synth.prompts.get("model") if synth.prompts else None
        )
        if "token_usage" in synth.outputs:
            synth_input = synth.outputs["token_usage"].get("input_tokens")
            synth_output = synth.outputs["token_usage"].get("output_tokens")
        synth_cost = synth.outputs.get("cost_usd")

    # Evidence stats
    coll = trace.stages.get("collection")
    ev_collected = None
    ev_passed = None
    ev_filtered = None
    if coll and coll.outputs:
        ev_collected = coll.evidence.get("collected_count")
        ev_passed = coll.outputs.get("evidence_passed")
        ev_filtered = coll.outputs.get("evidence_filtered")

    async with get_connection() as conn:
        await conn.execute("""
            INSERT INTO traces (
                trace_id, project_id, query, intent, domain, report_type,
                research_type, status, quality_gate_passed, overall_quality_score,
                started_at, completed_at, duration_seconds,
                intake_duration, rubric_duration, collection_duration,
                synthesis_duration, quality_gate_duration,
                evidence_collected, evidence_passed, evidence_filtered,
                synthesis_model, synthesis_input_tokens, synthesis_output_tokens,
                synthesis_cost_usd, principle_scores, gap_principles,
                strength_principles, iteration_count, quality_gate_failures,
                trace_file_path, report_file_path, output_file_paths
            ) VALUES (
                $1, $2::uuid, $3, $4, $5, $6,
                $7, $8, $9, $10,
                $11, $12, $13,
                $14, $15, $16,
                $17, $18,
                $19, $20, $21,
                $22, $23, $24,
                $25, $26::jsonb, $27,
                $28, $29, $30,
                $31, $32, $33
            )
        """,
            trace.trace_id,
            trace.project_id,
            trace.query,
            trace.intent,
            trace.domain,
            trace.report_type,
            trace.research_type,
            trace.status,
            quality_gate_passed,
            overall_score,
            trace.started_at,
            trace.completed_at,
            trace.duration_seconds,
            stage_durations.get("intake"),
            stage_durations.get("rubric"),
            stage_durations.get("collection"),
            stage_durations.get("synthesis"),
            stage_durations.get("quality_gate"),
            ev_collected,
            ev_passed,
            ev_filtered,
            synth_model,
            synth_input,
            synth_output,
            synth_cost,
            json.dumps(principle_scores),
            gap_principles or None,
            strength_principles or None,
            trace.iteration_count,
            trace.quality_gate_failures,
            trace.outputs.get("trace_file_path"),
            trace.outputs.get("report_path"),
            list(trace.outputs.get("evidence_paths", [])) or None,
        )
```

---

## 6. Query API — No SQL Required

```python
# src/tracing/query.py

"""
Pre-built trace queries for calibration and debugging.

Usage:
    from src.tracing.query import TraceQuery

    # All validating intent runs
    traces = await TraceQuery.by_intent("validating")

    # Runs where a specific principle scored low
    traces = await TraceQuery.low_scoring_principle("META-12", threshold=2)

    # Quality gate failures
    traces = await TraceQuery.quality_gate_failures()

    # Runs for a specific domain
    traces = await TraceQuery.by_domain("edtech")

    # Compare two traces side by side
    comparison = await TraceQuery.compare("trc_abc", "trc_def")

    # Principle patterns across all runs
    patterns = await TraceQuery.principle_patterns()
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from pathlib import Path

from src.db.connection import get_connection, fetch_all, fetch_one
from src.config import BRAIN_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TraceResult:
    """Lightweight trace result from a query."""
    trace_id: str
    project_name: Optional[str]
    query: Optional[str]
    intent: Optional[str]
    domain: Optional[str]
    report_type: Optional[str]
    status: str
    quality_gate_passed: Optional[bool]
    overall_quality_score: Optional[float]
    gap_principles: Optional[List[str]]
    strength_principles: Optional[List[str]]
    duration_seconds: Optional[float]
    started_at: str
    trace_file_path: Optional[str]


class TraceQuery:
    """Pre-built queries for trace data. No SQL knowledge required."""

    @staticmethod
    async def by_intent(intent: str, limit: int = 50) -> List[TraceResult]:
        """Show all runs with a specific intent (e.g., 'validating')."""
        rows = await fetch_all("""
            SELECT t.trace_id, p.name as project_name, t.query, t.intent,
                   t.domain, t.report_type, t.status, t.quality_gate_passed,
                   t.overall_quality_score, t.gap_principles, t.strength_principles,
                   t.duration_seconds, t.started_at, t.trace_file_path
            FROM traces t
            LEFT JOIN projects p ON p.id = t.project_id
            WHERE t.intent = $1 AND t.status = 'complete'
            ORDER BY t.started_at DESC
            LIMIT $2
        """, intent, limit)
        return [_row_to_result(r) for r in rows]

    @staticmethod
    async def low_scoring_principle(
        principle_id: str, threshold: int = 2, limit: int = 50
    ) -> List[TraceResult]:
        """Show all runs where a specific principle scored below threshold."""
        rows = await fetch_all("""
            SELECT t.trace_id, p.name as project_name, t.query, t.intent,
                   t.domain, t.report_type, t.status, t.quality_gate_passed,
                   t.overall_quality_score, t.gap_principles, t.strength_principles,
                   t.duration_seconds, t.started_at, t.trace_file_path
            FROM traces t
            LEFT JOIN projects p ON p.id = t.project_id
            WHERE t.status = 'complete'
              AND (t.principle_scores->>$1)::int < $2
            ORDER BY t.started_at DESC
            LIMIT $3
        """, principle_id, threshold, limit)
        return [_row_to_result(r) for r in rows]

    @staticmethod
    async def quality_gate_failures(limit: int = 50) -> List[TraceResult]:
        """Show all runs where quality gate failed."""
        rows = await fetch_all("""
            SELECT t.trace_id, p.name as project_name, t.query, t.intent,
                   t.domain, t.report_type, t.status, t.quality_gate_passed,
                   t.overall_quality_score, t.gap_principles, t.strength_principles,
                   t.duration_seconds, t.started_at, t.trace_file_path
            FROM traces t
            LEFT JOIN projects p ON p.id = t.project_id
            WHERE t.quality_gate_passed = FALSE AND t.status = 'complete'
            ORDER BY t.started_at DESC
            LIMIT $1
        """, limit)
        return [_row_to_result(r) for r in rows]

    @staticmethod
    async def by_domain(domain: str, limit: int = 50) -> List[TraceResult]:
        """Show all runs for a specific domain (e.g., 'edtech')."""
        rows = await fetch_all("""
            SELECT t.trace_id, p.name as project_name, t.query, t.intent,
                   t.domain, t.report_type, t.status, t.quality_gate_passed,
                   t.overall_quality_score, t.gap_principles, t.strength_principles,
                   t.duration_seconds, t.started_at, t.trace_file_path
            FROM traces t
            LEFT JOIN projects p ON p.id = t.project_id
            WHERE t.domain = $1 AND t.status = 'complete'
            ORDER BY t.started_at DESC
            LIMIT $2
        """, domain, limit)
        return [_row_to_result(r) for r in rows]

    @staticmethod
    async def by_project(project_name: str, limit: int = 50) -> List[TraceResult]:
        """Show all runs for a specific project."""
        rows = await fetch_all("""
            SELECT t.trace_id, p.name as project_name, t.query, t.intent,
                   t.domain, t.report_type, t.status, t.quality_gate_passed,
                   t.overall_quality_score, t.gap_principles, t.strength_principles,
                   t.duration_seconds, t.started_at, t.trace_file_path
            FROM traces t
            JOIN projects p ON p.id = t.project_id
            WHERE p.name = $1
            ORDER BY t.started_at DESC
            LIMIT $2
        """, project_name, limit)
        return [_row_to_result(r) for r in rows]

    @staticmethod
    async def principle_patterns(min_runs: int = 3) -> List[Dict[str, Any]]:
        """
        Show which principles fail most often across all runs.

        Returns list of {"principle_id", "fail_count", "avg_score", "total_runs"}
        sorted by fail_count descending.
        """
        rows = await fetch_all("""
            SELECT
                unnest(gap_principles) as principle_id,
                COUNT(*) as fail_count
            FROM traces
            WHERE status = 'complete' AND gap_principles IS NOT NULL
            GROUP BY principle_id
            HAVING COUNT(*) >= $1
            ORDER BY fail_count DESC
        """, min_runs)
        return [{"principle_id": r["principle_id"], "fail_count": r["fail_count"]} for r in rows]

    @staticmethod
    async def compare(trace_id_a: str, trace_id_b: str) -> Dict[str, Any]:
        """
        Compare two traces side by side.

        Returns both trace summaries plus a diff of key metrics.
        """
        row_a = await fetch_one("""
            SELECT t.*, p.name as project_name
            FROM traces t LEFT JOIN projects p ON p.id = t.project_id
            WHERE t.trace_id = $1
        """, trace_id_a)
        row_b = await fetch_one("""
            SELECT t.*, p.name as project_name
            FROM traces t LEFT JOIN projects p ON p.id = t.project_id
            WHERE t.trace_id = $1
        """, trace_id_b)

        if not row_a or not row_b:
            return {"error": "One or both trace IDs not found"}

        return {
            "trace_a": _row_to_summary(row_a),
            "trace_b": _row_to_summary(row_b),
            "diff": {
                "quality_delta": (row_b["overall_quality_score"] or 0) - (row_a["overall_quality_score"] or 0),
                "duration_delta": (row_b["duration_seconds"] or 0) - (row_a["duration_seconds"] or 0),
                "cost_delta": (row_b["synthesis_cost_usd"] or 0) - (row_a["synthesis_cost_usd"] or 0),
                "gaps_a_only": list(set(row_a["gap_principles"] or []) - set(row_b["gap_principles"] or [])),
                "gaps_b_only": list(set(row_b["gap_principles"] or []) - set(row_a["gap_principles"] or [])),
                "gaps_both": list(set(row_a["gap_principles"] or []) & set(row_b["gap_principles"] or [])),
            }
        }

    @staticmethod
    async def full_trace(trace_id: str) -> Optional[Dict[str, Any]]:
        """Load the full trace file (all decisions, prompts, evidence)."""
        row = await fetch_one(
            "SELECT trace_file_path FROM traces WHERE trace_id = $1", trace_id
        )
        if not row or not row["trace_file_path"]:
            return None

        file_path = Path(row["trace_file_path"])
        if not file_path.exists():
            return None

        with open(file_path) as f:
            return json.load(f)

    @staticmethod
    async def flagged_for_review(limit: int = 20) -> List[TraceResult]:
        """Show traces flagged for calibration review."""
        rows = await fetch_all("""
            SELECT t.trace_id, p.name as project_name, t.query, t.intent,
                   t.domain, t.report_type, t.status, t.quality_gate_passed,
                   t.overall_quality_score, t.gap_principles, t.strength_principles,
                   t.duration_seconds, t.started_at, t.trace_file_path
            FROM traces t
            LEFT JOIN projects p ON p.id = t.project_id
            WHERE t.flagged_for_review = TRUE
            ORDER BY t.started_at DESC
            LIMIT $1
        """, limit)
        return [_row_to_result(r) for r in rows]

    @staticmethod
    async def summary() -> Dict[str, Any]:
        """High-level summary of all traces."""
        row = await fetch_one("""
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
        """)
        return dict(row) if row else {}


def _row_to_result(row) -> TraceResult:
    """Convert a database row to TraceResult."""
    return TraceResult(
        trace_id=row["trace_id"],
        project_name=row.get("project_name"),
        query=row.get("query"),
        intent=row.get("intent"),
        domain=row.get("domain"),
        report_type=row.get("report_type"),
        status=row["status"],
        quality_gate_passed=row.get("quality_gate_passed"),
        overall_quality_score=row.get("overall_quality_score"),
        gap_principles=row.get("gap_principles"),
        strength_principles=row.get("strength_principles"),
        duration_seconds=row.get("duration_seconds"),
        started_at=str(row["started_at"]),
        trace_file_path=row.get("trace_file_path"),
    )


def _row_to_summary(row) -> Dict[str, Any]:
    """Convert a database row to a summary dict for comparison."""
    return {
        "trace_id": row["trace_id"],
        "project_name": row.get("project_name"),
        "intent": row.get("intent"),
        "domain": row.get("domain"),
        "quality_score": row.get("overall_quality_score"),
        "quality_gate_passed": row.get("quality_gate_passed"),
        "gap_principles": row.get("gap_principles"),
        "strength_principles": row.get("strength_principles"),
        "duration_seconds": row.get("duration_seconds"),
        "cost_usd": row.get("synthesis_cost_usd"),
    }
```

---

## 7. Calibration Integration

### 7.1 Auto-Flag Patterns

After each trace is saved, check for emerging patterns:

```python
# src/tracing/calibration_flags.py

async def check_calibration_flags(trace: Trace) -> List[str]:
    """
    Check if this trace reveals calibration opportunities.
    Returns list of human-readable flag messages.

    Policy: flag patterns but don't be pushy.
    """
    flags = []

    # Check if a principle has failed 3+ times recently
    if trace.stages.get("quality_gate"):
        for gap_id in (trace.stages["quality_gate"].outputs.get("gap_principles") or []):
            count = await _recent_failure_count(gap_id, days=7)
            if count >= 3:
                flags.append(
                    f"Principle {gap_id} has scored below threshold "
                    f"{count} times in the last 7 days. "
                    f"Consider reviewing calibration."
                )

    return flags
```

### 7.2 Calibration Data Pull

When running `/capture-calibration`, the system will:
1. Pull the trace for the current research run
2. Auto-populate: intent, principle scores, gaps, evidence quality stats
3. Present to user with pre-filled fields
4. User confirms/edits/adds context before saving

---

## 8. Retrofit Plan

### Approach: Context-Based Trace + Explicit Records (Hybrid A+B)

Components grab the trace from async context — no signature changes needed. They add explicit `trace.record()` calls at decision points.

### Retrofit Priority (per user's ranking)

| Priority | Component | Key Decision Points to Instrument |
|----------|-----------|----------------------------------|
| 1 | EvidenceQualityScorer | score calculations, tier assignment, filter decisions |
| 2 | CollectorCoordinator | source selection, query formulation, per-source results |
| 3 | RubricLoader | principles loaded, intent activation, deactivation reasoning |
| 4 | synthesize_report() | prompt construction, rubric injection, model selection |
| 5 | ResearchOrchestrator | routing decisions, retry logic, stage orchestration |

### Retrofit Pattern

```python
# Example: retrofitting rubric_loader.py

async def load_rubric(report_type, domain, intent):
    trace = TraceContext.current()  # Grab from context — no param needed

    # ... existing logic ...

    if trace:
        trace.record("rubric", "rubric_loaded", {
            "what": f"{total} principles ({meta_count} META + {type_count} {report_type})",
            "why": f"report_type={report_type}, domain={domain}, intent={intent}",
            "confidence": 1.0,
            "inputs": {"report_type": report_type, "domain": domain, "intent": intent}
        })

    # ... existing logic continues ...
```

The `if trace:` guard means existing code works identically when tracing is off. Zero behavior change.

---

## 9. File Layout

```
src/tracing/
├── __init__.py          # Exports: TraceContext, Trace, TraceQuery
├── context.py           # TraceContext, Trace, Decision, StageTrace, _NoOpTrace
├── interface.py         # TracingComponent ABC
├── query.py             # TraceQuery pre-built queries
├── calibration_flags.py # Auto-flag calibration opportunities
└── writer.py            # File writer + Supabase metadata writer (if split out)

src/db/migrations/
└── _023_trace_system.py # Supabase table + indexes + views

brain/projects/{project}/
└── _traces/
    ├── trc_20260213_abc123.json
    └── trc_20260213_def456.json
```

---

## 10. Failure Modes

| Scenario | Behavior |
|----------|----------|
| Trace file write fails | Research run continues, logs error, Supabase row has `trace_file_path = NULL` |
| Supabase write fails | Research run continues, logs error, trace file still saved locally |
| Both writes fail | Research run continues, warning logged, trace data lost for this run |
| Research run crashes mid-way | Partial trace saved with `status: "incomplete"` (if possible) |
| Corrupted trace file | Supabase metadata still queryable, `full_trace()` returns None |
| Tracing disabled for project | `_NoOpTrace` used — zero overhead, all record() calls are no-ops |

---

## 11. Build Order

1. **`src/tracing/context.py`** — Trace, TraceContext, Decision, StageTrace, _NoOpTrace
2. **`src/tracing/interface.py`** — TracingComponent ABC
3. **`src/db/migrations/_023_trace_system.py`** — Supabase table
4. **`src/tracing/query.py`** — TraceQuery with all pre-built queries
5. **`src/tracing/calibration_flags.py`** — Pattern detection
6. **Retrofit components** — In priority order: QualityScorer → Coordinator → RubricLoader → Synthesis → Orchestrator
7. **Report frontmatter injection** — Add trace_id to report YAML
8. **Tests** — Unit tests for each module, integration test for full trace lifecycle

---

## 12. Open Design Notes

- **Schema versioning**: Every trace has `schema_version: 1`. When schema changes, bump version. Old traces remain readable — query layer handles version differences.
- **Trace retention**: Keep forever for now. Revisit when storage becomes a concern.
- **Sampling**: Toggle per-project via config. No automatic sampling — all runs traced when enabled.
- **Performance**: Tracing overhead target < 100ms per research run (metadata writes are tiny vs LLM calls that take seconds).
