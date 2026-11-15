#!/bin/bash
# scripts/claude-hooks/pre-compact-handover.sh
#
# PreCompact hook: generates a handover summary before context compaction.
# Captures active work state so post-compact Claude doesn't lose
# integration-critical context that the default compactor might summarize
# away.
#
# Fires on both manual (/compact) and auto compaction.
# Output goes to stdout and is injected as additional context.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# --- Active task detection ---
# Find which feature's tasks exist and what's in progress
active_feature=""
active_tasks=""
for idx in "$REPO_ROOT"/tasks/*/tasks-index.md; do
  [ -f "$idx" ] || continue
  dir=$(dirname "$idx")
  feature=$(basename "$dir")
  pending=$(grep -c 'Complete: \[ \]' "$dir"/tasks-*.md 2>/dev/null | awk -F: '{s+=$NF} END {print s+0}')
  complete=$(grep -c 'Complete: \[x\]' "$dir"/tasks-*.md 2>/dev/null | awk -F: '{s+=$NF} END {print s+0}')
  if [ "$pending" -gt 0 ]; then
    active_feature="$feature"
    active_tasks="$pending pending, $complete complete"
    break
  fi
done

# --- Discovery integration context ---
# If there's an active feature, extract its integration requirements
integration_context=""
if [ -n "$active_feature" ]; then
  discovery="$REPO_ROOT/docs/discovery/${active_feature}.md"
  if [ -f "$discovery" ]; then
    # Extract Integration Requirements section if it exists
    integration_context=$(sed -n '/^## Integration Requirements/,/^## /p' "$discovery" 2>/dev/null | head -40)
  fi
fi

# --- Git state ---
branch=$(cd "$REPO_ROOT" && git branch --show-current 2>/dev/null || echo "unknown")
uncommitted=$(cd "$REPO_ROOT" && git diff --stat HEAD 2>/dev/null | tail -1)
recent_commit=$(cd "$REPO_ROOT" && git log -1 --oneline 2>/dev/null || echo "none")

# --- Output handover summary ---
cat <<EOF
[PRE-COMPACT HANDOVER SUMMARY]
This context was captured before compaction to preserve active work state.

Git State:
  Branch: $branch
  Last commit: $recent_commit
  Uncommitted: ${uncommitted:-"clean"}
EOF

if [ -n "$active_feature" ]; then
  cat <<EOF

Active Feature: $active_feature
  Tasks: $active_tasks
  Discovery: docs/discovery/${active_feature}.md
EOF
fi

if [ -n "$integration_context" ]; then
  cat <<EOF

Integration Context (from discovery):
$integration_context
EOF
fi

cat <<EOF

IMPORTANT: If you were in the middle of implementing a task, re-read
the task prompt AND the discovery sections it references before continuing.
Context compaction may have summarized away integration details that are
critical to the implementation.
EOF
