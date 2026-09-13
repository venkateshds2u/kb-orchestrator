-- Step 9's Step.requires_approval was added to the domain model but never
-- persisted -- a real gap, only caught while building Step 11's HTTP API,
-- where a workflow is created in one request and re-fetched from the
-- database in a separate one, unlike Step 9's own tests which mostly
-- worked with in-memory Workflow objects that never made a real round
-- trip through storage. SQLite has no native boolean type; stored as
-- INTEGER 0/1, the standard SQLite convention.
ALTER TABLE steps ADD COLUMN requires_approval INTEGER NOT NULL DEFAULT 0;
