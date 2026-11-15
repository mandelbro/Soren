# /plan — Discovery to Work Items Pipeline

Transform a discovery document into reviewed, integration-aware task files in a
single pass. Runs four agents sequentially, each with full context from the
previous step:

1. **Norwood** — Validates the discovery document: checks for Integration
   Requirements section, inventories existing code, maps data flow, identifies
   reuse opportunities
2. **Task Breakdown** — Generates tasks with outcome-based acceptance criteria
   and section references that cover ALL integration points from the discovery
3. **Shelly** — Reviews tasks against the discovery for orphaned integrations,
   assumption drift, phase ordering coherence, and data flow completeness
4. **Zod** — Reviews task prompts for missing discovery section references,
   integration points with existing code, and acceptance criteria gaps

## Usage

```
/plan <discovery-doc-path>
```

Example:
```
/plan docs/discovery/autonomous-learning-system.md
```

## What this produces

- Reviewed task files in `tasks/<feature-name>/`
- A tasks-index.md with sprint planning recommendations
- Each task includes:
  - References to ALL relevant discovery sections (not just mechanism sections)
  - Outcome-based acceptance criteria (what a user would recognize, not just mechanism)
  - Integration checkpoints (what data flows in, what flows out)
  - Session awareness instructions pointing to integration-critical context

## Pre-flight checks

Before generating tasks, verify the discovery document has:

1. **Integration Requirements section** — If missing, prompt the user to add one
   (or generate a draft using the template below)
2. **Existing Components & Reuse Plan** — If missing, use Norwood to generate
3. **End-to-end smoke test scenario** — At least one concrete scenario that
   exercises the feature end-to-end

## Integration Requirements template (if missing)

```markdown
## Integration Requirements

### Data Flow
- What data does this feature need from existing components?
- What data does this feature produce that other components consume?

### Assumption Alignment
- What does the feature name imply about its behavior?
- What would a human assume this feature does if they only read the title?
- What integration points are so obvious a human wouldn't think to state them?

### End-to-End Smoke Test
- Describe one concrete scenario that exercises the feature end-to-end
- If the feature cannot support this scenario after implementation, what's missing?
```

## Review gates

### Shelly's review checks for:
- **Orphaned integrations**: discovery describes an integration no task references
- **Assumption drift**: task title uses a word the acceptance criteria don't verify
- **Phase ordering**: implementation order matches discovery's recommended delivery
- **Data flow breaks**: data enters a component but no task wires the output

### Zod's review checks for:
- **Missing section references**: task prompt references Section 10.6 but not 6.5
- **Integration with existing code**: task modifies a file but doesn't call existing
  methods the discovery says it should
- **Acceptance criteria sufficiency**: mechanism-only criteria flagged for tasks > 3pts

## Post-generation

After tasks are generated and reviewed:
1. Create a feature-context entry so the integration requirements are loaded
   into context on every prompt during implementation
2. Record the smoke test scenario for post-phase validation
3. Commit the task files before starting implementation
