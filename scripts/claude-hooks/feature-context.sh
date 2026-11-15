#!/bin/bash
# scripts/claude-hooks/feature-context.sh
#
# UserPromptSubmit hook: loads integration requirements from the active
# feature's discovery document into context on every prompt.
#
# This is the "Layer 2" companion to the Vision-Keeper reminder (Layer 1).
# Vision-Keeper prevents RFC-level drift. Feature-context prevents
# integration-level drift — building features that technically align with
# the RFC but don't connect to the system around them.
#
# The hook detects which feature has pending tasks, finds its discovery
# document, and extracts:
#   1. The one-liner (what the feature is supposed to do)
#   2. Integration Requirements (data flow, assumptions, smoke test)
#   3. Any sections the current tasks reference

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Find active feature (first task directory with pending tasks)
for idx in "$REPO_ROOT"/tasks/*/tasks-index.md; do
  [ -f "$idx" ] || continue
  dir=$(dirname "$idx")
  feature=$(basename "$dir")

  # Check for pending tasks (grep -c with multiple files outputs per-file counts; sum them)
  pending=$(grep -c 'Complete: \[ \]' "$dir"/tasks-*.md 2>/dev/null | awk -F: '{s+=$NF} END {print s+0}')
  if [ "$pending" -eq 0 ]; then
    continue
  fi

  discovery="$REPO_ROOT/docs/discovery/${feature}.md"
  if [ ! -f "$discovery" ]; then
    continue
  fi

  # Extract one-liner
  one_liner=$(sed -n '/^## The One-Liner/,/^##/{/^## The One-Liner/d;/^##/d;p;}' "$discovery" 2>/dev/null | head -3 | sed '/^$/d')

  # Extract Integration Requirements section (if it exists)
  integration=$(sed -n '/^## Integration Requirements/,/^## /{/^## Integration Requirements/d;/^##/d;p;}' "$discovery" 2>/dev/null | head -50)

  if [ -n "$one_liner" ] || [ -n "$integration" ]; then
    echo "[FEATURE CONTEXT — ${feature}]"
    echo "Active feature with ${pending} pending tasks."
    echo ""

    if [ -n "$one_liner" ]; then
      echo "Vision: ${one_liner}"
      echo ""
    fi

    if [ -n "$integration" ]; then
      echo "Integration Requirements (from discovery):"
      echo "$integration"
    else
      echo "WARNING: Discovery doc has no Integration Requirements section."
      echo "Before implementing tasks, verify: what data flows in from existing"
      echo "components? What assumptions does the feature name imply? What"
      echo "would a human assume this feature does if they only read the title?"
    fi

    echo ""
    echo "When implementing tasks for this feature, verify that each task's"
    echo "acceptance criteria include at least one outcome a user would recognize"
    echo "(not just mechanism checks like 'state machine implemented')."
  fi

  # Only emit for the first active feature
  break
done
