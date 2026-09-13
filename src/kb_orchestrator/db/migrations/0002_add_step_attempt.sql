-- Step 8: how many times a step has actually been attempted. Defaults to
-- 0 for existing rows (there are none in production yet, but the default
-- is still correct: a step that predates this column was never attempted
-- under a retry policy).
ALTER TABLE steps ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0;
