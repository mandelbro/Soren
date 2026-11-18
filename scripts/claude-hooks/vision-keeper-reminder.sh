#!/bin/bash
# Vision-Keeper reminder hook — prevents drift from Soren RFC architectural decisions.
#
# Runs on every UserPromptSubmit. Reads the RFC's load-bearing sections
# (Goals, Non-Goals, and Alternatives Considered verdicts) and emits them
# as a system reminder so Claude cannot drift between turns as context
# compresses.
#
# This is the *first line of defense* against architectural drift. The
# second line is the Vision-Keeper subagent at .claude/agents/vision-keeper.md,
# which Claude is instructed to consult before proposing architectural
# changes that touch execution modality, inference location, or the
# VM isolation boundary.
#
# If this hook fires but the RFC file is missing or unreadable, emit a
# warning instead of failing — the hook should never block prompt submission.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RFC_FILE="${REPO_ROOT}/docs/rfc.md"
ADR_DIR="${REPO_ROOT}/docs/adr"

if [[ ! -r "$RFC_FILE" ]]; then
  cat <<EOF
[VISION-KEEPER] Warning: ${RFC_FILE} not found. Architectural guardrail disabled.
Claude should still hold the following invariants (from prior RFC state):
  - Vision-based execution (UI-TARS screenshots), NOT DOM automation
  - Local or Ollama cloud inference only (no other hosted providers)
  - VM-isolated execution for bot-detection avoidance
EOF
  exit 0
fi

# Print the load-bearing tenets as a tight reminder.
# We intentionally do NOT dump the whole RFC — the point is a short, ever-present
# check, not a context-flooding dump. The subagent handles deep review.
cat <<'EOF'
[VISION-KEEPER — RFC INVARIANTS]
The following are load-bearing architectural decisions from docs/rfc.md.
DO NOT propose changes that contradict them without first consulting the
Vision-Keeper subagent.

Goals that must be preserved:
  * Goal 4: VM-isolated execution for bot-detection avoidance
  * Goal 5: Vision-based task execution via UI-TARS (screenshots + mouse/keyboard,
    simulating a real human user)
  * Goal 6: Local or Ollama cloud inference — each model role runs locally
    (MLX/Ollama) or as an Ollama `-cloud` model, chosen per role in settings.
    Credentials never go to a cloud model's prompt.
  * Goal 8: Encrypted credentials delivered via GUI input simulation;
    never via API traffic or environment variables.

Architectural alternatives EXPLICITLY REJECTED in the RFC:
  * DOM-based automation (Playwright, Selenium, BrowserMCP, JS injection):
    rejected because "the entire value proposition of Soren depends on
    undetectable automation" (§Alternatives Considered #1).
  * Host-side browser execution: breaks VM isolation and bot-detection guarantees.
  * Hosted LLM providers other than Ollama cloud (Anthropic, OpenAI, etc.).

Before proposing ANY change that:
  (a) introduces a new execution modality (DOM, accessibility API, extension-based),
  (b) moves inference to a hosted provider other than Ollama cloud, or sends
      credentials to a cloud model, OR
  (c) punctures the VM isolation boundary (host-side execution, network tunneling
      out of the VM for task execution purposes),
you MUST invoke the vision-keeper subagent (see .claude/agents/vision-keeper.md)
to audit the proposal against the current RFC. The subagent has no emotional
investment in your proposal and will cite specific RFC sections if it detects
a contradiction.

If the RFC itself needs to evolve (e.g., the user clarifies that the vision
has expanded), update docs/rfc.md FIRST, then propose implementation changes
against the updated spec. Do not work around the RFC; update it. For decisions
that refine or supersede specific RFC sections, write an ADR (see docs/adr/)
and let the ADR be the authoritative spec for that area. The vision-keeper
subagent reads both the RFC and the ADRs on every audit; when they conflict,
the more recent ADR wins.
EOF

# List the currently published ADRs so Claude sees which refinements are in
# effect. A missing ADR for a topic means the RFC is still authoritative
# there; a present ADR means the ADR refines or overrides the RFC.
if [[ -d "$ADR_DIR" ]]; then
  adr_files=$(find "$ADR_DIR" -maxdepth 1 -name "[0-9]*.md" -type f 2>/dev/null | sort)
  if [[ -n "$adr_files" ]]; then
    echo ""
    echo "[VISION-KEEPER — PUBLISHED ADRs]"
    echo "The following ADRs refine or supersede specific RFC sections. When"
    echo "a proposal touches an area covered by an ADR, the ADR's decision is"
    echo "authoritative. Read the full ADR before proposing changes in these areas:"
    while IFS= read -r adr; do
      # Extract the first line (title) from each ADR file
      title=$(head -n 1 "$adr" | sed 's/^# //')
      rel_path=${adr#"$REPO_ROOT/"}
      echo "  * ${title}"
      echo "    (${rel_path})"
    done <<< "$adr_files"
  fi
fi
