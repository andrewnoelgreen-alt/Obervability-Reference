# Observability & Trace System — Reference Implementation

This is a reference implementation of a **decision-tracing observability system** extracted from IRE (Intelligent Research Engine), an AI-powered research pipeline. The code is provided as-is for architectural reference — it has IRE-specific imports that won't run standalone, but the patterns, data models, and design decisions are the valuable part.

**Use this repo by feeding it to your LLM** alongside your own codebase and asking it to adapt the architecture to your system.

---

## Why This Exists (The Problem It Solves)

Most observability systems track **what happened** — timings, error rates, request counts. This system tracks **why things happened** — the decisions, reasoning, confidence levels, and alternatives considered at every step of a multi-stage pipeline.

This matters when your system is an AI pipeline (or any complex multi-step workflow) where:

- The output quality varies and you need to diagnose **which stage** degraded it
- Multiple components make autonomous decisions and you need to trace **which decision** led to a bad outcome
- You're iterating on quality and need to see **patterns across runs** (e.g., "principle X fails 80% of the time for domain Y")
- You need both **fast queryable metrics** (aggregate stats, filtering) and **deep audit trails** (full decision logs, prompts used)

Traditional logging gives you breadcrumbs. This gives you a complete decision tree for every run.

---

## Architecture Overview

### Dual Storage Model

The system writes every trace to two places simultaneously:

| Layer | Format | Location | Purpose |
|-------|--------|----------|---------|
| **Full Trace** | JSON file | `{project}/_traces/{trace_id}.json` | Complete audit trail — every decision, prompt, evidence record, timing |
| **Metadata** | Database row | PostgreSQL `traces` table | Fast queries — filter by intent, domain, score, date; aggregate stats |

The full JSON trace is the source of truth. The database row is a denormalized projection optimized for queries. If the DB write fails, the JSON file still saves. If the file write fails, the DB row still saves. The research run never fails because of tracing.

### Core Concepts

**Trace** — One complete run through your pipeline. Has a unique ID (`trc_20260213_143022_a1b2c3d4`), tracks overall status, timing, and outputs.

**Stage** — A named phase within a trace (e.g., `intake`, `collection`, `synthesis`, `quality_gate`). Each stage has its own start/end timing, decisions, evidence, and outputs.

**Decision** — A first-class object representing a choice your system made. Contains:
- `what` — The decision itself ("selected exa_search as primary source")
- `why` — The reasoning ("high-quality neural search results for competitive analysis")
- `confidence` — How sure the system was (0.0-1.0)
- `alternatives_considered` — What else was on the table ("google (0.60)", "reddit (0.40)")
- `inputs` — What data informed the decision

**Calibration Flags** — Automated pattern detection that fires after each trace completes. Detects things like "principle X has failed 3+ times in 7 days" or "domain Y runs score 0.5 below average." These are informational alerts, not blockers.

---

## File Map

```
ire-observability-reference/
|
|-- tracing/                    # Core system (~2,000 lines)
|   |-- __init__.py             # Public API exports
|   |-- context.py              # TraceContext, Trace, Decision, StageTrace, _NoOpTrace
|   |-- writer.py               # Dual persistence: JSON files + database metadata
|   |-- summary.py              # Terminal scorecards + markdown summary reports
|   |-- query.py                # Pre-built query API (no SQL knowledge needed)
|   |-- calibration_flags.py    # Auto-pattern detection for quality issues
|   |-- interface.py            # TracingComponent ABC + component registry
|
|-- migrations/                 # Database schema
|   |-- _023_trace_system.py    # Core traces table, indexes, views
|   |-- _034_enriched_traces.py # Extended columns for calibration/retrieval data
|
|-- mcp_handlers/               # API layer example
|   |-- traces.py               # MCP tool handlers that wrap the query API
|
|-- tests/                      # Test suite (~2,100 lines)
|   |-- test_tracing_context.py # Core data model + context manager tests
|   |-- test_tracing_interface.py # Component interface tests
|   |-- test_tracing_writer.py  # Persistence layer tests
|   |-- test_tracing_query.py   # Query API tests
|   |-- test_calibration_flags.py # Calibration flag detection tests
|   |-- test_trace_migration.py # Database schema validation tests
|   |-- test_tracing_e2e.py     # End-to-end integration tests
|
|-- spec/
|   |-- OBSERVABILITY_SYSTEM_SPEC.md  # Full technical design spec with examples
|
|-- README.md                   # This file
```

---

## How It Works — The Lifecycle

### 1. Start a Trace

```python
from tracing import TraceContext

trace = TraceContext.start(
    project_name="my-project",
    query="analyze competitor pricing strategies",
    intent="validating",        # what the user wants to do
    domain="saas",              # subject area
    report_type="market_research",
)
```

Uses Python's `contextvars.ContextVar` for async safety — each asyncio Task gets its own trace automatically.

### 2. Record Decisions Inside Components

Any component in the pipeline grabs the current trace from context (no parameter passing needed):

```python
trace = TraceContext.current()
if trace:
    trace.start_stage("collection")

    trace.record("collection", "source_selected", {
        "what": "exa_search",
        "why": "High-quality neural search for competitive analysis",
        "confidence": 0.9,
        "alternatives_considered": ["google (0.6)", "reddit (0.4)"],
        "inputs": {"query": "...", "research_type": "competitive"}
    })

    trace.record_evidence("collection", {
        "collected_count": 150,
        "by_source": {"exa": 100, "reddit": 50}
    })

    trace.end_stage("collection", outputs={"evidence_passed": 120})
```

The `if trace:` guard means code works identically when tracing is off. Zero behavior change.

### 3. Finish the Trace

```python
trace.mark_complete()
result = await TraceContext.finish(trace, verbose=False)
# result = {
#     "trace_id": "trc_20260213_143022_a1b2c3d4",
#     "file_path": "projects/my-project/_traces/trc_....json",
#     "supabase": "saved",
#     "summary_file": "projects/my-project/_traces/trc_..._summary.md",
#     "calibration_flags": ["Principle META-12 has scored below threshold 5 times..."],
# }
```

Finish does four things:
1. Writes full trace JSON to file
2. Writes metadata row to database
3. Prints a compact terminal scorecard (or verbose breakdown)
4. Runs calibration flag detection and alerts

### 4. Or Use the Context Manager

```python
from tracing import traced_research

async with traced_research(
    query="analyze competitor pricing",
    project_name="my-project",
    intent="validating",
) as trace:
    # Your pipeline code here
    # trace.record() calls happen inside your components
    # On normal exit: auto-marks complete
    # On exception: auto-marks failed, re-raises
    # Always calls finish() for persistence
    pass
```

### 5. Disable Tracing (Zero Overhead)

```python
trace = TraceContext.start(enabled=False)
# Returns a _NoOpTrace — all record() calls are silent no-ops
# No file writes, no DB writes, no overhead
```

---

## Querying Traces

The query API provides pre-built methods so you never write SQL:

```python
from tracing import TraceQuery

# Aggregate summary
summary = await TraceQuery.summary()
# {"total_runs": 142, "complete": 130, "failed": 8, "avg_quality": 2.3, ...}

# Filter by intent, domain, or project
traces = await TraceQuery.by_intent("validating", limit=20)
traces = await TraceQuery.by_domain("edtech", limit=20)
traces = await TraceQuery.by_project("cobot", limit=20)

# Find quality gate failures
failures = await TraceQuery.quality_gate_failures(limit=10)

# Find which principles fail most often
patterns = await TraceQuery.principle_patterns(min_runs=3)
# [{"principle_id": "META-12", "fail_count": 15}, ...]

# Compare two runs side-by-side
diff = await TraceQuery.compare("trc_abc", "trc_def")
# {"quality_delta": +0.4, "cost_delta": -$0.05, "gaps_a_only": ["META-12"], ...}

# Get full trace JSON for deep debugging
full = await TraceQuery.full_trace("trc_abc")

# Traces flagged for review by calibration system
flagged = await TraceQuery.flagged_for_review()
```

---

## Calibration Flags (Auto-Pattern Detection)

After each trace completes, the system checks for patterns that suggest something needs attention:

| Flag | Trigger | Example |
|------|---------|---------|
| **Repeated Principle Failure** | Same principle appears in `gap_principles` 3+ times in 7 days | "META-12 has scored below threshold 5 times in the last 7 days" |
| **Intent Quality Disparity** | An intent's average score is >0.5 below the overall average | "validating intent runs average 1.8 quality vs 2.3 overall" |
| **Domain Quality Disparity** | A domain's average score is >0.5 below the overall average | "edtech domain runs average 1.9 vs 2.3 overall" |
| **Quality Gate Regression** | Current run failed QG after previous run for same project passed | "Quality regression detected for project cobot" |

Flags trigger three actions:
1. Print to terminal: `[CALIBRATION] Principle META-12 has scored below threshold...`
2. Set `flagged_for_review = TRUE` in database
3. Append to `{project}/_calibration_alerts.md` with timestamp

Flags are informational — they never block or modify pipeline behavior.

---

## Database Schema

The `traces` table has ~44 columns covering:

- **Identity**: trace_id, project_id, query, intent, domain, report_type
- **Status**: status (in_progress/complete/incomplete/failed), quality_gate_passed
- **Timing**: overall duration + per-stage durations (intake, rubric, collection, synthesis, quality_gate)
- **Evidence**: collected/passed/filtered/retrieved/used counts
- **Synthesis**: model name, input/output tokens, cost in USD
- **Quality**: overall score, per-principle scores (JSONB), gap/strength principle arrays
- **Iterations**: count, quality gate failure count
- **Files**: trace file path, report file path, output file paths
- **Calibration**: flagged_for_review, review_notes

Key indexes: intent, domain, report_type, status, quality_gate_passed, project_id, started_at DESC, overall_quality_score, GIN on principle_scores JSONB.

Two views: `trace_quality_gaps` (runs with low-scoring principles) and `trace_intent_summary` (intent distribution with averages).

See `migrations/_023_trace_system.py` and `migrations/_034_enriched_traces.py` for the full schema.

---

## Terminal Output

### Compact Scorecard (default)

```
-- Trace Summary ------------------------------------------
Quality: 2.4/3.0  PASS    Duration: 1m 23s
Cost: $0.32                Evidence: 28->18
Gaps: META-12
Trace: trc_20260213_143022_a1b2c3d4
-----------------------------------------------------------
```

### Verbose Breakdown

```
== Trace Detail =============================================
Trace ID:  trc_20260213_143022_a1b2c3d4
Project:   cobot
Query:     What is the competitive landscape for collaborative robots?
Intent:    validating    Domain: robotics
Status:    complete    Duration: 4m 22s

-- Quality Gate -------------------------------------------
Score: 2.4/3.0  PASS
Principle Scores:
  META-1: 3
  META-12: 1 <gap
...

-- Stages -------------------------------------------------
  intake           5.2s  (2 decisions)
  rubric           0.3s  (2 decisions)
  collection      45.8s  (2 decisions)
  synthesis      180.5s  (1 decisions)
  quality_gate    30.2s  (1 decisions)
...
=============================================================
```

---

## Key Design Decisions

1. **Decisions are first-class objects** — Not just log lines. Each has what/why/confidence/alternatives. This is what makes diagnosis possible.

2. **Dual storage** — JSON files for completeness, database for queries. Either can fail without losing the other.

3. **Context-based trace propagation** — Uses `contextvars.ContextVar` so components grab the trace from async context. No parameter threading through every function signature.

4. **NoOp pattern for zero-overhead disable** — `_NoOpTrace` silently ignores all calls. No conditional logic scattered through components.

5. **Tracing never crashes the pipeline** — Every write operation is wrapped in try/except. The research run always completes regardless of tracing failures.

6. **Calibration flags are informational** — They alert, they don't block. The system trusts the human to decide when to act.

7. **Schema versioning** — Every trace carries a `schema_version` field for forward compatibility.

---

## Adapting This For Your System

To build your own version, your LLM should focus on:

1. **Define your stages** — What are the distinct phases of your pipeline? Each becomes a stage in the trace.

2. **Identify your decision points** — Where does your system choose between options? Those are your `trace.record()` calls.

3. **Choose your storage** — The dual-storage pattern (file + DB) works well, but adapt to your stack. SQLite instead of PostgreSQL, S3 instead of local files, etc.

4. **Define your quality metrics** — IRE uses principle-based scoring. Your system might use accuracy, latency, user satisfaction, or something else entirely. The trace schema should reflect what you actually measure.

5. **Build queries for your calibration loop** — What questions do you ask when something goes wrong? "Show me all runs where X failed" — those become your query API methods.

6. **Keep it non-blocking** — Tracing should never be in the critical path. Failures should be logged and swallowed, never propagated.

The `spec/OBSERVABILITY_SYSTEM_SPEC.md` file contains the full design rationale with example trace JSON, the complete database schema, the retrofit plan for adding tracing to existing components, and failure mode analysis.

---

## What's NOT Included

This reference package does not include:

- **The pipeline itself** — The research pipeline components that produce traces are IRE-specific
- **Database connection layer** — Uses `asyncpg` via a custom connection pool (`src/db/connection.py`)
- **Configuration** — Paths and settings from `src/config.py`
- **Logger** — Custom structured logger from `src/utils/logger.py`

These are standard infrastructure that your system will have its own versions of. The tracing system's internal imports (`from src.db.connection import get_connection`, `from src.config import BRAIN_DIR`, etc.) show you exactly what dependencies exist so you know what to wire up.
