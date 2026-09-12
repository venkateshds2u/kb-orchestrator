"""Domain models and business logic: Workflow/Step, the structural
invariants that make an illegal graph unrepresentable, ID/timestamp
assignment, and error semantics. No SQL and no execution-engine logic
(Step 6) live here -- `service.py` depends on `WorkflowRepository`
through its constructor, never on SQL directly."""
