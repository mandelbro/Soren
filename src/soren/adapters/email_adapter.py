"""Email inbox discovery adapter (WI 4.4)."""

from soren.adapters.base import DISCOVERY_READY, register_adapter
from soren.models.persona import Persona
from soren.models.task_source import TaskSource


class EmailAdapter:
    """Discovery adapter for email inboxes (Gmail, Outlook, etc.)."""

    source_type = "email"
    expected_fields = [
        "sender",
        "subject",
        "preview",
        "timestamp",
        "provider_task_id",
        "requires_response",
    ]

    def navigation_prompt(self, source: TaskSource, persona: Persona) -> str:
        """Instructions for UI-TARS to reach inbox (~20 lines)."""
        url = source.url or "the email provider inbox"
        hint_section = ""
        if source.discovery_hint:
            hint_section = f"""
SITE HINT: {source.discovery_hint}
Use the above hint to guide your navigation on this specific site.
"""
        return f"""Navigate to {url}.

1. LOGIN: If you see a login page, use the stored credentials for this service.
   Enter username and password. If 2FA or CAPTCHA appears, STOP and output exactly:
   {DISCOVERY_READY} (we cannot proceed; human intervention required).

2. COOKIE BANNERS: Dismiss any cookie consent or banner overlays so the main content is visible.
{hint_section}
3. INBOX: Navigate to the Inbox. If filters exist, select "All Mail" or "Primary".

4. UNREAD: Look for a way to sort by "Unread" or filter to unread messages.
   If none exists, that's fine — we'll identify unread by visual styling (bold, indicator).

5. SCROLL: Scroll down to see recent messages (last 20-30 messages is sufficient).

6. ERROR DETECTION: If you see CAPTCHA, 2FA, or "verify your identity", output exactly:
   {DISCOVERY_READY} and stop.

7. COMPLETION: When the inbox with messages is visible and no blocking overlays remain,
   output exactly: {DISCOVERY_READY}

DO NOT open any email. DO NOT click on individual messages. Only reach the inbox view.
"""

    def extraction_prompt(self, source: TaskSource) -> str:
        """Instructions for Qwen3-VL to extract actionable emails (~30 lines)."""
        return """You are analyzing screenshots of an email inbox.
Identify every UNREAD email that appears to require a response.

Unread emails are typically shown with:
- Bold subject lines
- An unread dot/indicator
- Different background shading

For each unread email that needs a response, extract:
- sender: Who sent the email (name or address)
- subject: The email subject line
- preview: Any visible preview/snippet text
- timestamp: When it was received (as shown)
- provider_task_id: Any visible message ID (from URL, detail view, or list). Use null if none.
- requires_response: true if the email seems to need a reply (invitations, questions, requests).
  false for newsletters, promotions, automated notifications.

FILTER OUT: Promotional emails, newsletters, automated notifications, marketing.
Only include emails that a human would reasonably need to respond to.

Format as a numbered list:
1. sender: X | subject: "Y" | preview: ... | timestamp: ... | requires_response: true
2. ...

End with exactly: Unread emails requiring response: N
(where N is the count of actionable emails)
"""

    def structuring_prompt(self, source: TaskSource, raw_text: str) -> str:
        """Instructions for Gemma3:12B to convert to JSON (~20 lines)."""
        return f"""Convert this extraction to a JSON array of email task objects.

Schema per object:
- title (str): Use the subject line as title
- snippet (str): Use preview text, or sender + ": " + first 80 chars of subject
- provider_task_id (str | null): Message ID from URL or list. null if not found.
- sender (str): Sender name or address
- timestamp (str | null): Date/time as shown. null if unclear.
- requires_response (bool): true if email needs reply, false otherwise.
- task_type (str): Always "email"

Rules:
- Use null for unknown or unclear fields.
- Parse timestamps as shown; keep original format if ISO conversion unclear.
- Output a valid JSON array only; no markdown or extra text.

Raw extraction:
{raw_text}
"""


# Register on module load
register_adapter(EmailAdapter())
