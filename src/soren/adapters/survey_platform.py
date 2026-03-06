"""Survey platform discovery adapter (WI 4.3)."""

from soren.adapters.base import DISCOVERY_READY, register_adapter
from soren.models.persona import Persona
from soren.models.task_source import TaskSource


class SurveyPlatformAdapter:
    """Discovery adapter for survey platforms (Prolific, etc.)."""

    source_type = "survey_platform"
    expected_fields = [
        "title",
        "estimated_duration_minutes",
        "estimated_reward",
        "reward_currency",
        "provider_task_id",
    ]

    def navigation_prompt(self, source: TaskSource, persona: Persona) -> str:
        """Instructions for UI-TARS to reach survey dashboard (~25 lines)."""
        url = source.url or "the survey platform dashboard"
        return f"""Navigate to {url}.

1. LOGIN: If you see a login page, use the stored credentials for this service.
   Enter username and password. If 2FA or CAPTCHA appears, STOP and output exactly:
   {DISCOVERY_READY} (we cannot proceed; human intervention required).

2. COOKIE BANNERS: Dismiss any cookie consent or banner overlays (Accept, I Agree, etc.) so the main content is visible.

3. DASHBOARD: Navigate to the survey/task listing page (dashboard, studies, or
   available tasks). Ensure you are on the page that shows available surveys.

4. SCROLL: Scroll down to load all visible survey items. Many platforms
   lazy-load; keep scrolling until no new items appear or you reach the bottom.

5. ERROR DETECTION: If you see CAPTCHA, 2FA prompt, "verify your identity", or
   similar human-verification blocks, output exactly: {DISCOVERY_READY} and stop.

6. COMPLETION: When the full task list is visible and no blocking overlays remain, output exactly: {DISCOVERY_READY}
"""

    def extraction_prompt(self, source: TaskSource) -> str:
        """Instructions for Qwen3-VL to extract survey data from screenshots (~35 lines)."""
        return """You are analyzing screenshots of a survey platform dashboard. Extract every survey/task visible.

For EACH survey, provide:
- title: The survey or study title as shown
- duration: Estimated time (e.g. "15 min", "1 hour", "30 minutes")
- reward: Amount and currency (e.g. "$2.50", "£1.20", "3.00 GBP")
- provider_task_id: Any visible survey ID, reference number, or unique code
  (from table, URL, or badge). Use null if none visible.
- status: Available, full, completed, or other status if shown
- additional_info: Any extra text (e.g. "Prolific approved", "Desktop only")

Format as a numbered list:
1. title: "Survey Title" | duration: 15 min | reward: $2.50 | id: ABC123 | status: available
2. ...

MULTI-SCREENSHOT DEDUP: These screenshots may overlap (scroll capture). If the
same survey appears in multiple screenshots, list it only ONCE. Use consistent
ordering (top-to-bottom, left-to-right).

End with exactly: Total surveys found: N
(where N is the count of unique surveys)
"""

    def structuring_prompt(self, source: TaskSource, raw_text: str) -> str:
        """Instructions for Gemma3:12B to convert to JSON (~20 lines)."""
        return f"""Convert this extraction to a JSON array of survey objects.

Schema per object:
- title (str): Survey title
- estimated_duration_minutes (int | null): Parse duration strings.
  "15 min" -> 15, "1 hour" -> 60, "30 minutes" -> 30. null if unclear.
- estimated_reward (float | null): Numeric value only. "$2.50" -> 2.5, "£1.20" -> 1.2. null if unclear.
- reward_currency (str | null): "USD", "GBP", "EUR", etc. null if unclear.
- provider_task_id (str | null): Survey ID or reference. null if not found.

Rules:
- Use null for unknown or unclear fields.
- Duration: extract minutes; convert hours (1 hour = 60).
- Reward: strip currency symbols, keep numeric value.
- Output a valid JSON array only; no markdown or extra text.

Raw extraction:
{raw_text}
"""


# Register on module load
register_adapter(SurveyPlatformAdapter())
