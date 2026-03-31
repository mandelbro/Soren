"""Generic form discovery adapter (WI 4.4)."""

from soren.adapters.base import DISCOVERY_READY, register_adapter
from soren.models.persona import Persona
from soren.models.task_source import TaskSource


class GenericFormAdapter:
    """Discovery adapter for fillable forms at known URLs."""

    source_type = "generic_form"
    expected_fields = [
        "form_title",
        "form_type",
        "fields",
        "submit_button_text",
        "provider_task_id",
    ]

    def navigation_prompt(self, source: TaskSource, persona: Persona) -> str:
        """Instructions for UI-TARS to reach form page (~15 lines)."""
        url = source.url or "the target URL"
        hint_section = ""
        if source.discovery_hint:
            hint_section = f"""
SITE HINT: {source.discovery_hint}
Use the above hint to guide your navigation on this specific site.
"""
        return f"""Navigate to {url}.

1. LOGIN: If you see a login page, use the stored credentials for this service.
   If 2FA or CAPTCHA appears, STOP and output exactly: {DISCOVERY_READY}

2. COOKIE BANNERS: Dismiss any cookie consent overlays.
{hint_section}
3. FORM: Look for input fields, dropdowns, text areas, checkboxes, submit buttons.
   If the form requires scrolling to see all fields, scroll down.

4. ERROR DETECTION: If you see CAPTCHA or 2FA, output exactly: {DISCOVERY_READY} and stop.

5. COMPLETION: When you can see the full form (or confirm there is no form),
   output exactly: {DISCOVERY_READY}

DO NOT fill in any fields. DO NOT submit anything.
"""

    def extraction_prompt(self, source: TaskSource) -> str:
        """Instructions for Qwen3-VL to extract form structure (~25 lines)."""
        return """You are analyzing a screenshot of a web page that may contain a form.

Determine:
1. Is there a fillable form visible? (yes/no)
2. What type of form? (application, registration, feedback, survey, contact, other)
3. What fields does the form contain?

For each form field, extract:
- label: The field label or placeholder text
- field_type: text, email, phone, dropdown, checkbox, radio, textarea, date, file
- required: Whether the field appears required (asterisk, "required" label)
- options: For dropdowns/radio buttons, list the visible options

Format:

Form found: [yes/no]
Form type: [type]
Form title: [title if visible, else "Untitled form"]

Fields:
1. [label] — [field_type] — required: [yes/no]
2. [label] — [field_type] — required: [yes/no]
...

Submit button text: [text on submit button]

If no form is found, output: Form found: no
"""

    def structuring_prompt(self, source: TaskSource, raw_text: str) -> str:
        """Instructions for Gemma3:12B to convert to JSON (~15 lines)."""
        url = source.url or ""
        return f"""Convert this extraction to a JSON array. If a form exists, one object; if not, empty array.

Schema per object:
- title (str): Form title or "Form at [URL]"
- snippet (str): Brief summary, e.g. "5 fields: name, email, message..."
- provider_task_id (str | null): Use source URL for dedup: "{url}". null if URL unknown.
- form_type (str): application, registration, feedback, survey, etc.
- form_title (str | null): Form title if visible
- fields (array): Each {{"label": str, "field_type": str, "required": bool, "options": []}}
- submit_button_text (str | null): Submit button label
- task_type (str): Always "form"

Rules:
- Use null for unknown fields.
- Output a valid JSON array only; no markdown or extra text.
- Empty array if no form found.

Raw extraction:
{raw_text}
"""


# Register on module load
register_adapter(GenericFormAdapter())
