"""
Enriched Traces Schema - Extends traces table with calibration and retrieval columns.

Adds 9 new columns to the existing traces table via ALTER TABLE:
- tier_config: Tier name used for the run (e.g., "THOROUGH")
- rubric_scores: JSONB per-principle scores from QG evaluation (same format as calibration_runs.score_by_principle)
- principle_breakdown: JSONB detailed per-principle analysis (reasoning, hints)
- qg_iteration_count: Number of quality gate improvement iterations (separate from existing iteration_count)
- retrieval_method: How evidence was retrieved (e.g., "parallel", "sequential")
- evidence_retrieved: Total evidence items retrieved before filtering
- evidence_used: Evidence items actually used in synthesis
- retrieval_tokens: Total tokens used for retrieval/extraction
- retrieval_cost_usd: Cost of retrieval phase in USD (NUMERIC for precision)

All columns use ADD COLUMN IF NOT EXISTS for idempotency.

IMPORTANT: The existing traces table already has iteration_count (generic pipeline iterations)
and principle_scores (observability telemetry). The new columns are:
- qg_iteration_count: specifically QG improvement loop iterations
- rubric_scores: specifically QG evaluation output (may differ from principle_scores)

Spec reference: Spec 08 (Memory & RAG) Section 2.2 — MEM-10
"""

UP = """
-- Extend traces table with calibration and retrieval columns
ALTER TABLE traces ADD COLUMN IF NOT EXISTS tier_config TEXT;
ALTER TABLE traces ADD COLUMN IF NOT EXISTS rubric_scores JSONB;
ALTER TABLE traces ADD COLUMN IF NOT EXISTS principle_breakdown JSONB;
ALTER TABLE traces ADD COLUMN IF NOT EXISTS qg_iteration_count INT;
ALTER TABLE traces ADD COLUMN IF NOT EXISTS retrieval_method TEXT;
ALTER TABLE traces ADD COLUMN IF NOT EXISTS evidence_retrieved INT;
ALTER TABLE traces ADD COLUMN IF NOT EXISTS evidence_used INT;
ALTER TABLE traces ADD COLUMN IF NOT EXISTS retrieval_tokens INT;
ALTER TABLE traces ADD COLUMN IF NOT EXISTS retrieval_cost_usd NUMERIC(10,6);

-- GIN indexes for JSONB query performance
CREATE INDEX IF NOT EXISTS idx_traces_rubric_scores
    ON traces USING GIN (rubric_scores);

CREATE INDEX IF NOT EXISTS idx_traces_principle_breakdown
    ON traces USING GIN (principle_breakdown);
"""

DOWN = """
-- Drop GIN indexes first
DROP INDEX IF EXISTS idx_traces_principle_breakdown;
DROP INDEX IF EXISTS idx_traces_rubric_scores;

-- Drop added columns
ALTER TABLE traces DROP COLUMN IF EXISTS retrieval_cost_usd;
ALTER TABLE traces DROP COLUMN IF EXISTS retrieval_tokens;
ALTER TABLE traces DROP COLUMN IF EXISTS evidence_used;
ALTER TABLE traces DROP COLUMN IF EXISTS evidence_retrieved;
ALTER TABLE traces DROP COLUMN IF EXISTS retrieval_method;
ALTER TABLE traces DROP COLUMN IF EXISTS qg_iteration_count;
ALTER TABLE traces DROP COLUMN IF EXISTS principle_breakdown;
ALTER TABLE traces DROP COLUMN IF EXISTS rubric_scores;
ALTER TABLE traces DROP COLUMN IF EXISTS tier_config;
"""
