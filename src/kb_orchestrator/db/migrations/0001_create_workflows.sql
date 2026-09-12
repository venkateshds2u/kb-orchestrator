CREATE TABLE workflows (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Step ids are scoped to their workflow, not globally unique -- the
-- composite primary key is what lets "research"/"draft"/"approve" be
-- reused as step ids across many different workflow instances, matching
-- how real workflow tools (Airflow's task_id, for one) scope a step's
-- identity to one graph, not the whole system.
CREATE TABLE steps (
    workflow_id TEXT NOT NULL REFERENCES workflows(id),
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    message TEXT NOT NULL,
    -- SQLite has no native array type -- this column holds a JSON-encoded
    -- list of sibling step ids, encoded/decoded only in repository.py (the
    -- one place that knows this is a storage detail, not a domain one).
    depends_on TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    error TEXT,
    -- Preserves the original step order from workflow definition when
    -- reconstructing a Workflow -- a plain SELECT without ORDER BY gives
    -- no ordering guarantee, and this is cheaper and more explicit than
    -- relying on SQLite's rowid insertion-order behavior.
    ordinal INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workflow_id, id)
);
