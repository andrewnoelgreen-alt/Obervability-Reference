"""
Trace System Schema - Observability layer for research pipeline.

This enables:
1. Queryable trace metadata for calibration analysis
2. Per-principle quality scores across runs
3. Intent/domain/report_type filtering
4. Stage-level timing and cost tracking
5. Quality gate pass/fail history
6. Calibration review flagging

Tables:
- traces: Main trace metadata table

Views:
- trace_quality_gaps: Runs with low-scoring principles
- trace_intent_summary: Intent distribution with quality/duration/cost averages
"""

UP = """
-- Traces table: Queryable metadata for research pipeline observability
CREATE TABLE IF NOT EXISTS traces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id VARCHAR(100) UNIQUE NOT NULL,
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,

    -- Run identifiers
    query TEXT,
    intent VARCHAR(50),
    domain VARCHAR(100),
    report_type VARCHAR(100),
    research_type VARCHAR(100),

    -- Status
    status VARCHAR(20) NOT NULL DEFAULT 'in_progress',
    quality_gate_passed BOOLEAN,
    overall_quality_score FLOAT,

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
    gap_principles TEXT[],
    strength_principles TEXT[],

    -- Iteration tracking
    iteration_count INT DEFAULT 1,
    quality_gate_failures INT DEFAULT 0,

    -- File references
    trace_file_path VARCHAR(500),
    report_file_path VARCHAR(500),
    output_file_paths TEXT[],

    -- Calibration support
    flagged_for_review BOOLEAN DEFAULT FALSE,
    review_notes TEXT,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for the 6 calibration query types
CREATE INDEX IF NOT EXISTS idx_traces_intent ON traces(intent);
CREATE INDEX IF NOT EXISTS idx_traces_domain ON traces(domain);
CREATE INDEX IF NOT EXISTS idx_traces_report_type ON traces(report_type);
CREATE INDEX IF NOT EXISTS idx_traces_status ON traces(status);
CREATE INDEX IF NOT EXISTS idx_traces_quality_gate ON traces(quality_gate_passed);
CREATE INDEX IF NOT EXISTS idx_traces_project ON traces(project_id);
CREATE INDEX IF NOT EXISTS idx_traces_started ON traces(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_overall_score ON traces(overall_quality_score);
CREATE INDEX IF NOT EXISTS idx_traces_flagged ON traces(flagged_for_review) WHERE flagged_for_review = TRUE;

-- GIN index for principle_scores JSONB queries
CREATE INDEX IF NOT EXISTS idx_traces_principle_scores ON traces USING GIN (principle_scores);

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
"""

DOWN = """
DROP VIEW IF EXISTS trace_intent_summary;
DROP VIEW IF EXISTS trace_quality_gaps;
DROP TRIGGER IF EXISTS update_traces_updated_at ON traces;
DROP TABLE IF EXISTS traces;
"""
