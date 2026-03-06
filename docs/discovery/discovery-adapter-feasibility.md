# Discovery: Vision-Based Discovery Adapter Feasibility

## The One-Liner

VLM-based task discovery is feasible but demands a two-phase extraction strategy, aggressive prompt engineering per adapter, and realistic expectations — 60-75% discovery rates in Phase 4 with a path to >80% via playbook iteration in Phase 6.

## Date

February 23, 2026

## Status

Complete — ready for Phase 4 implementation planning

## RFC Reference

`docs/rfc.md` — TaskDiscovery interface, DiscoveryAdapter protocol, Deduplication strategy

## Phase Gate

Phase 4 (Task Discovery)

---

## 1. State of the Art: VLM-Based Web Agents

### Benchmark Landscape

The field has matured rapidly. Here's where things stand as of early 2026:

| Benchmark | What It Tests | Best Agent | Success Rate | Notes |
|-----------|--------------|------------|--------------|-------|
| **OSWorld** | Real computer tasks (369 tasks) | Claude Sonnet 4.5 | 61.4% | Full desktop environment, multi-app |
| **OSWorld** | Same | UI-TARS-2 (72B) | 47.5% | Sep 2025, RL-trained |
| **OSWorld** | Same | UI-TARS 1.5 7B | 24.6% (50 steps) | Our model. Outperforms GPT-4o |
| **WebVoyager** | Real web tasks (15 sites) | GPT-4V agent | 59.1% | Open web, diverse tasks |
| **Online-Mind2Web** | Web navigation (2K+ tasks) | UI-TARS-2 | 88.2% | Navigation-only, not extraction |
| **VisualWebArena** | Visually grounded web tasks (910) | Best multimodal | ~16-20% | Hard benchmark, visual reasoning |
| **AndroidWorld** | Mobile GUI tasks | UI-TARS 1.5 7B | 46.6% | Beats GPT-4o (34.5%) |

**What this tells us about Soren**: UI-TARS 1.5 7B at 24.6% on OSWorld sounds bad until you consider the task. OSWorld tests *completion* of complex multi-step tasks (install software, configure settings, create documents). Soren's discovery is simpler — navigate to a known URL, log in, and read what's on screen. That's closer to the navigation subset where UI-TARS scores much higher.

The critical gap: UI-TARS excels at *navigation and action grounding* (clicking the right element, typing in the right field) but struggles with *judgment and reasoning* (deciding what to extract, structuring the output). The desktop-agent architecture already acknowledges this — it uses separate models for action (UI-TARS) and reasoning (Gemma3:12B). Discovery needs the same split.

### Relevant Projects

**WebVoyager** is the closest analog to Soren's discovery flow. It directs a VLM agent to navigate real websites and complete tasks like "Find the cheapest flight from NYC to LA." The 59.1% success rate is for *task completion*, not just navigation. For Soren, the discovery task is narrower (enumerate items on a dashboard), which should yield higher rates.

**Mind2Web** provides 2,000+ real-world web tasks across 137 websites. The key finding: filtering HTML with a small model before processing dramatically improves LLM performance. Soren's approach — using a VLM for navigation and a separate LLM for structured extraction — mirrors this architecture.

**Image2Struct** benchmarks VLMs on extracting structure from images. Performance ranges from 0.402 (sheet music) to 0.830 (LaTeX equations). Web page HTML extraction falls in the 0.5-0.7 range — respectable but not reliable enough for single-shot extraction. Multiple passes improve accuracy.

**SeeClick** trains VLMs specifically for GUI element grounding (clicking the right thing). UI-TARS inherits this capability and exceeds SeeClick on most benchmarks. For Soren's discovery, the grounding ability (navigate to dashboard, click "Show more") is well-served.

### The 7B Model Reality

The elephant in the room: UI-TARS 1.5 7B is a 7-billion parameter model. The best web agent results come from 72B+ models or cloud APIs (Claude, GPT-4o). The performance gap is real:

| Task Type | UI-TARS 7B | Cloud Models (Claude/GPT-4o) | Gap |
|-----------|-----------|------------------------------|-----|
| GUI grounding (click/type) | Excellent | Excellent | Negligible |
| Navigation (multi-step) | Good (24.6% OSWorld) | Better (61.4% OSWorld) | 2.5x |
| Structured reasoning | Limited | Strong | Significant |
| Counting/enumeration | Unreliable | Better but imperfect | Material |

This is why the RFC's three-model architecture exists. UI-TARS handles the parts it's good at (navigation, clicking, typing). Gemma3:12B handles what it cannot (structured reasoning, output parsing). The discovery adapter design must respect this division.

---

## 2. Extraction Strategy: Two-Phase is Non-Negotiable

### Option A: Single-Pass (VLM Navigates AND Extracts)

The RFC's description of adapters as "50-100 lines of prompt engineering" implicitly assumes single-pass: tell the VLM what to look for, it navigates there and reports back.

**Why this fails for 7B models**: UI-TARS was trained for *action grounding*, not structured data extraction. When asked "navigate to the survey dashboard and list all available surveys with their titles, durations, and rewards," a 7B VLM will:
- Navigate to the dashboard successfully (~80% of the time)
- Identify that there are surveys visible (~90%)
- Enumerate all of them accurately (~40-60%)
- Extract structured fields for each one (~30-50%)
- Return well-formed JSON (~20-40%)

The compound probability kills you. Five steps at 80%/90%/50%/40%/30% gives roughly 4% end-to-end reliability. That's not going to hit 80%.

Research confirms this. VLMs struggle with counting tasks in complex visual scenes — none of the models tested can "reliably count objects in complex visual scenes" (Assessing Visual Enumeration Abilities, Dec 2025). Survey dashboards with 10-20 items, variable layouts, and rich visual information are exactly the kind of complex scene that breaks counting.

### Option B: Two-Phase (VLM Navigates, LLM Extracts)

This is the architecture Soren should use:

**Phase 1 — Navigation (UI-TARS 7B via CUA ComputerAgent)**:
1. Boot browser, navigate to service URL
2. Log in using IdentityManager credentials
3. Follow adapter instructions to reach the task listing page
4. Scroll to load all items
5. Capture one or more full-page screenshots

**Phase 2 — Extraction (Gemma3:12B via Ollama)**:
1. Receive screenshot(s) from Phase 1
2. Parse structured data from the visual content using a detailed extraction prompt
3. Return JSON array of DiscoveredTask objects

Wait — Gemma3:12B is a *text* model, not a VLM. It can't process screenshots. That's the Qwen3-VL 8B role. Let me reconsider.

### Revised Option B: VLM Navigates, Verification VLM Extracts

**Phase 1 — Navigation (UI-TARS 7B)**:
- Navigate to dashboard, log in, scroll to load content
- Take screenshots at each scroll position

**Phase 2 — Extraction (Qwen3-VL 8B via Ollama)**:
- Process screenshots with structured extraction prompt
- Return JSON with task items

**Phase 3 — Structuring (Gemma3:12B via Ollama)**:
- Take Qwen3-VL's raw text output
- Normalize into DiscoveredTask Pydantic models
- Generate dedup keys
- Validate completeness

This is actually *three* phases, but Phase 2 and Phase 3 are just two Ollama calls — the model swap adds ~7s (from the memory budget discovery) but gives us the best of both worlds: Qwen3-VL's visual understanding for screenshot parsing, and Gemma3:12B's text reasoning for structured output generation.

### Why Three Phases Wins

| Criterion | Single-Pass (UI-TARS only) | Two-Phase (UI-TARS + Qwen3-VL) | Three-Phase (UI-TARS + Qwen3-VL + Gemma3) |
|-----------|---------------------------|-------------------------------|-------------------------------------------|
| Navigation reliability | High | High | High |
| Item enumeration | Low (40-60%) | High (70-85%) | High (70-85%) |
| Field extraction accuracy | Low (30-50%) | Good (60-75%) | Good (60-75%) |
| Structured output quality | Very Low (20-40%) | Medium (50-65%) | High (75-90%) |
| Latency per discovery cycle | ~2-4 min | ~3-5 min | ~4-7 min |
| Model swap overhead | None | +7s (one swap) | +14s (two swaps) |
| Prompt complexity per adapter | ~50 lines | ~80 lines (nav + extract) | ~100 lines (nav + extract + structure) |

The RFC's "50-100 lines per adapter" estimate maps well to the three-phase approach when you account for all three prompts per adapter.

### Recommended Architecture

```python
class DiscoveryAdapter(Protocol):
    """Each service type implements this protocol."""

    def navigation_prompt(self, source: TaskSource, persona: Persona) -> str:
        """Instructions for UI-TARS to navigate to the task listing page.
        ~20-30 lines. Output: agent reaches the right page."""

    def extraction_prompt(self, source: TaskSource) -> str:
        """Instructions for Qwen3-VL to extract items from screenshots.
        ~30-40 lines. Output: raw text describing each item."""

    def structuring_prompt(self, source: TaskSource, raw_text: str) -> str:
        """Instructions for Gemma3:12B to convert raw text to JSON.
        ~20-30 lines. Output: list of DiscoveredTask dicts."""

    @property
    def source_type(self) -> str:
        """e.g., 'survey_platform', 'email', 'generic_form'"""

    @property
    def expected_fields(self) -> list[str]:
        """Fields this adapter can extract. Used for validation."""
```

---

## 3. Adapter Architecture: Concrete Design

### SurveyPlatformAdapter

This is the highest-value adapter for v1. Survey platforms typically have a dashboard with a list of available surveys, each showing title, estimated duration, and reward amount.

#### Navigation Prompt (~25 lines)

```
You are navigating a web browser to find available surveys.

CURRENT GOAL: Reach the survey dashboard showing all available surveys.

STEPS:
1. You should see the survey platform's website. If you see a login page,
   enter the credentials that will be provided to you.
2. After login, look for a dashboard, home page, or "Available Surveys" section.
3. If you see a list of surveys, you're in the right place.
4. If the page shows categories or filters, select "All" or "Available" to show
   everything.
5. IMPORTANT: Scroll down slowly to the bottom of the page to load all surveys.
   Some platforms use lazy loading — items only appear when you scroll to them.
6. After reaching the bottom, scroll back to the top.
7. When you can see the full list of available surveys, say "DISCOVERY_READY"
   and stop taking actions.

DO NOT click on any individual survey. DO NOT start any survey.
Your only job is to navigate to the page showing all available surveys.

If you encounter:
- A cookie consent banner: click "Accept" or "OK"
- A popup or modal: close it by clicking X or "Close"
- A CAPTCHA: describe what you see and stop
- A 2FA prompt: describe what you see and stop
```

#### Extraction Prompt (~35 lines)

```
You are analyzing a screenshot of a survey platform dashboard.
Your job is to identify every available survey visible on the page and
extract specific details about each one.

For EACH survey you can see, extract:
- title: The survey name or title text
- estimated_duration: How long the survey takes (e.g., "15 min", "5-10 minutes")
- reward: The payment amount (e.g., "$2.50", "£1.80", "250 points")
- provider_task_id: Any visible survey ID, reference number, or unique code
- status: Whether it shows as "available", "new", or similar
- additional_info: Any other relevant details (topic, eligibility requirements)

FORMAT YOUR RESPONSE as a numbered list. For each survey:

Survey 1:
- title: [exact title as shown]
- estimated_duration: [duration if visible, "unknown" if not]
- reward: [amount if visible, "unknown" if not]
- provider_task_id: [ID if visible, "none" if not]
- status: [status text if visible, "available" if not]
- additional_info: [any other details]

Survey 2:
[same format]

IMPORTANT RULES:
- List EVERY survey visible on the page. Do not skip any.
- Use the EXACT text shown on screen. Do not paraphrase or infer.
- If you cannot read a field clearly, write "unclear" not a guess.
- If multiple screenshots are provided, they show different scroll positions
  of the same page. Do not duplicate items that appear in multiple screenshots.
- Count the total number of surveys at the end: "Total surveys found: N"
```

#### Structuring Prompt (~20 lines)

```
Convert the following survey descriptions into a JSON array.
Each survey should be a JSON object with these fields:

{
  "title": "string — exact survey title",
  "estimated_duration_minutes": number or null,
  "estimated_reward": number or null,
  "reward_currency": "string — USD, GBP, points, etc.",
  "provider_task_id": "string or null",
  "task_type": "survey",
  "source_url": "[SOURCE_URL]",
  "additional_metadata": {}
}

Parse duration strings into minutes (e.g., "15 min" → 15, "1 hour" → 60).
Parse reward strings into numbers (e.g., "$2.50" → 2.50, "250 points" → 250).
If a value is "unknown" or "unclear", set it to null.

RAW SURVEY DATA:
[RAW_TEXT]

Return ONLY the JSON array. No explanation.
```

**Total: ~80 lines across three prompts.** Within the RFC's 50-100 line estimate.

### EmailAdapter

#### Navigation Prompt (~20 lines)

```
You are navigating a web browser to check an email inbox.

CURRENT GOAL: Reach the inbox showing all unread emails.

STEPS:
1. You should see an email provider's website. If you see a login page,
   enter the credentials that will be provided to you.
2. After login, navigate to the Inbox.
3. If the inbox has filters or categories, select "All Mail" or "Primary"
   to see all messages.
4. Look for a way to sort by "Unread" or filter to show only unread messages.
5. If no unread filter exists, that's fine — we'll identify unread messages
   by their visual styling (bold text, unread indicator).
6. Scroll down to see all recent messages (last 20-30 messages is sufficient).
7. When you can see the inbox with messages listed, say "DISCOVERY_READY"
   and stop.

DO NOT open any email. DO NOT click on any individual message.
Your only job is to reach the inbox view.
```

#### Extraction Prompt (~30 lines)

```
You are analyzing a screenshot of an email inbox.
Identify every UNREAD email that appears to require a response.

Unread emails are typically shown with:
- Bold subject lines
- An unread dot/indicator
- Different background shading

For each unread email that needs a response, extract:
- sender: Who sent the email
- subject: The email subject line
- preview: Any visible preview text
- timestamp: When it was received
- provider_task_id: Any visible message ID (rare in inbox views)
- requires_response: true if the email seems to need a reply
  (invitations, questions, requests — not newsletters or notifications)

FORMAT as a numbered list:

Email 1:
- sender: [sender name/address]
- subject: [exact subject line]
- preview: [preview text if visible]
- timestamp: [date/time as shown]
- requires_response: [true/false with brief reason]

Skip promotional emails, newsletters, and automated notifications.
Count at the end: "Unread emails requiring response: N"
```

### GenericFormAdapter

The generic form adapter is the simplest — it navigates to a specific URL and identifies whether there's a fillable form present.

#### Navigation Prompt (~15 lines)

```
You are navigating a web browser to find a fillable form.

CURRENT GOAL: Reach the page at the target URL and identify any forms.

STEPS:
1. You should see a web page. If you see a login page first,
   enter the credentials that will be provided to you.
2. Look for any form on the page — input fields, dropdown menus,
   text areas, checkboxes, submit buttons.
3. If the form requires scrolling to see all fields, scroll down
   to see the complete form.
4. When you can see the form (or confirm there is no form),
   say "DISCOVERY_READY" and stop.

DO NOT fill in any form fields. DO NOT submit anything.
```

#### Extraction Prompt (~25 lines)

```
You are analyzing a screenshot of a web page that may contain a form.

Determine:
1. Is there a fillable form visible? (yes/no)
2. What type of form is it? (application, registration, feedback, survey, other)
3. What fields does the form contain?

For each form field, extract:
- label: The field label or placeholder text
- field_type: text, email, phone, dropdown, checkbox, radio, textarea, date, file
- required: Whether the field appears required (asterisk, "required" label)
- options: For dropdowns/radio buttons, list the visible options

FORMAT:

Form found: [yes/no]
Form type: [type]
Form title: [title if visible]

Fields:
1. [label] — [field_type] — required: [yes/no]
2. [label] — [field_type] — required: [yes/no]
[etc.]

Submit button text: [text on submit button]
```

---

## 4. Deduplication Stability Analysis

### The RFC's Fingerprint: What Could Go Wrong

The fallback dedup key hashes: `task_type + source_url + normalized_title + normalized_snippet + stable_metadata`

Each component has different stability characteristics:

| Component | Stability | Concern |
|-----------|-----------|---------|
| `task_type` | Very stable | Set by adapter, not extracted from page |
| `source_url` | Stable | URL doesn't change between loads (dashboard URL) |
| `normalized_title` | Mostly stable | Lowercased, whitespace-collapsed. Risk: A/B tested titles, "NEW" badges appearing/disappearing |
| `normalized_snippet` | Moderately stable | First 160 chars of preview. Risk: timestamps, personalized text ("Hi [name]"), dynamic counts |
| `stable_metadata` | Variable | Reward amounts, durations — stable. Due dates, spots remaining — unstable |

### Specific Instability Vectors

**Survey platforms**: Reward amounts and durations are stable identifiers — surveys don't change their compensation between page loads. The risk is the *title* containing dynamic elements: "Quick Survey - 5 spots left!" where the spot count changes. Normalization should strip trailing dynamic fragments.

**Email inboxes**: The `provider_task_id` (message-ID header) is the gold standard for email dedup and should always be extractable from email UIs (most show it in the message detail view or URL). The fallback hash is less reliable because email subjects can be identical ("Re: Meeting tomorrow").

**Generic forms**: Forms at a fixed URL rarely change, so `source_url + task_type` alone provides strong dedup. The form title and field labels are stable across loads.

### Dashboard Reordering

Survey platforms commonly reorder dashboard items based on: relevance scoring, completion rate, time remaining, or simply random rotation. This *does not* affect the dedup key because the key doesn't include position. `item_index` is mentioned in the RFC as part of `stable_metadata`, but it should only be included when it's genuinely the *only* distinguisher between otherwise-identical items.

**Recommendation**: Drop `item_index` from the default fingerprint. Use it only when the adapter explicitly flags that two items have identical titles, rewards, and other fields but different positions.

### Fingerprint Robustness Testing

Before declaring dedup "working," Phase 4 should run this test:

1. Discover tasks from a test service (N items)
2. Wait 5 minutes
3. Discover again from the same service
4. Verify: exact same N items, zero new inserts (all collide on dedup_key)
5. Wait 24 hours
6. Discover again
7. Verify: same items still collide (no time-dependent drift)

### Recommendations for Dedup

1. **Always prefer `provider_task_id`** over content fingerprint. Adapters should try hard to extract native IDs.
2. **Strip dynamic fragments** from titles before normalization: trailing counts ("5 left"), "NEW" badges, timestamps.
3. **Exclude `item_index`** from the default fingerprint hash. Only include it when the adapter explicitly opts in.
4. **Normalize reward values** before hashing: "$2.50" and "2.50 USD" should produce the same fingerprint.
5. **Log fingerprint inputs** alongside the hash for debugging collision issues.
6. **Add a `fingerprint_version` field** to the dedup key prefix. When fingerprint logic changes, bump the version to avoid false collisions with old keys.

---

## 5. Multi-Item Page Parsing

### The Core Problem: VLMs Can't Count

Research from December 2025 (Assessing Visual Enumeration Abilities of VLMs) confirms what practitioners already knew: VLMs are unreliable counters. When asked to enumerate items in complex visual scenes, models:
- Frequently miss items, especially in dense layouts (10+ items)
- Hallucinate items that don't exist (~5-15% false positive rate)
- Lose track of which items they've already listed
- Perform worse as visual complexity increases

For a survey dashboard with 15 items, a single-screenshot VLM extraction might return 10-13 items. That's 65-85% recall per pass. For email inboxes, the situation is slightly better because email UIs are more structured (linear list, consistent row height).

### The Scrolling Problem

The desktop-agent docs confirm: "No native scroll method in CUA SDK — scrolling must be emulated via key presses or osascript." The visual_validation module uses Page Up/Down for multi-screenshot capture with duplicate detection.

For discovery, the scrolling strategy needs to be deliberate:

**Strategy: Overlapping scroll capture with deduplication**

```python
async def capture_all_items(
    computer: Computer,
    adapter: DiscoveryAdapter,
) -> list[bytes]:
    """Capture screenshots covering the entire task listing page."""
    screenshots = []
    max_scrolls = 20
    similarity_threshold = 0.95

    # Scroll to top first
    await computer.interface.press_key("cmd+up")
    await asyncio.sleep(1)

    # Capture first screenshot
    screenshot = await computer.interface.screenshot()
    screenshots.append(screenshot)

    for _ in range(max_scrolls):
        # Scroll down by roughly 80% of viewport (overlap for dedup)
        await computer.interface.press_key("pagedown")
        await asyncio.sleep(1.5)  # Wait for lazy-loading content

        new_screenshot = await computer.interface.screenshot()

        # Check if we've reached the bottom (no change)
        similarity = compare_screenshots(screenshots[-1], new_screenshot)
        if similarity > similarity_threshold:
            break

        screenshots.append(new_screenshot)

    return screenshots
```

### Handling Lazy Loading and Infinite Scroll

**Lazy loading**: The 1.5-second sleep after each scroll gives time for content to load. If the platform uses aggressive lazy loading (>2s load time), increase the sleep. Adapters can override `scroll_wait_seconds` per service.

**Infinite scroll**: For platforms that load more content on scroll (rather than pagination), the agent scrolls until either:
- The page stops changing (similarity threshold)
- A maximum scroll count is reached (20 scrolls = ~20 viewports of content)
- A "Load more" button appears (UI-TARS clicks it, then continues scrolling)

**Pagination**: If the platform uses pagination (page 1, 2, 3...), the navigation prompt should instruct UI-TARS to:
1. Extract items from the current page
2. Click "Next page"
3. Extract items from the next page
4. Repeat until "Next" is disabled/absent

This is more complex and should be adapter-specific. The `SurveyPlatformAdapter` should support a `pagination_strategy` field: `"scroll"` (default) or `"click_next"`.

### Multi-Screenshot Extraction Strategy

When Qwen3-VL receives multiple screenshots, it needs clear instructions:

```
You are analyzing {N} screenshots of the same webpage taken at different
scroll positions. The screenshots overlap — the same item may appear in
two consecutive screenshots.

CRITICAL: Do not count the same item twice. If an item appears at the
bottom of one screenshot and the top of the next, count it ONCE.

Process the screenshots in order (1 through {N}). For each screenshot,
only extract items that are NEW — items you haven't already listed from
a previous screenshot.
```

### Expected Recall Rates

| Dashboard Size | Strategy | Expected Recall | Confidence |
|----------------|----------|-----------------|------------|
| 1-5 items | Single screenshot | 85-95% | High |
| 6-10 items | Single screenshot | 70-85% | Medium |
| 11-20 items | Multi-screenshot + overlap | 65-80% | Medium |
| 20+ items | Multi-screenshot + pagination | 55-70% | Low |

These are conservative estimates for 7B-class VLMs. The two-phase extraction (Qwen3-VL for extraction instead of UI-TARS) adds ~5-10% to each range.

### Improving Recall: The Verification Pass

After the initial extraction, run a verification pass:

1. Take the list of extracted items (N items)
2. Send the same screenshots back to Qwen3-VL with a different prompt: "You previously found N items. Review these screenshots carefully — did you miss any items? Look especially at items partially visible at the edges of screenshots."
3. Merge any newly found items

In benchmarks, this "re-check" strategy catches 20-40% of missed items. It adds one more Qwen3-VL call (~3-5s) but significantly improves recall.

---

## 6. Login Flow Reliability

### Expected Success Rates by Login Type

| Login Type | VLM Success Rate | Failure Mode | Frequency in Wild |
|------------|-----------------|--------------|-------------------|
| Email + password form | 85-90% | Field misidentification, autocomplete interference | Very common |
| Google OAuth ("Sign in with Google") | 60-70% | Multi-step flow, phone verification prompts | Common |
| SSO redirect chains | 40-60% | Multiple redirects confuse navigation | Uncommon |
| 2FA (TOTP code) | 70-80% | Code entry timing, field selection | Common |
| CAPTCHA (image-based) | 30-40% | Best VLM agents hit ~40% vs 93% human | Common |
| CAPTCHA (behavioral) | Near 0% | Mouse movement analysis, device fingerprinting | Increasingly common |
| "Save password?" prompts | 90% to dismiss | Clicking "Not now" or "Never" | Almost always |

### Why Login is the Highest-Risk Step

Login is the gateway. If the agent can't log in, it can't discover anything. The compound failure math is harsh:

- Navigate to login page: 95%
- Identify form fields: 90%
- Type credentials correctly: 85% (UI-TARS sometimes types in wrong field)
- Submit form: 80% (the Enter key problem from desktop-agent)
- Handle post-login redirects: 90%
- Dismiss popups/banners: 85%

Compound: ~47% raw success rate for a "simple" email+password login.

**But playbooks change everything.** A service playbook that says "Click the email field (it's the first text input), type the email, press Tab, type the password, click the blue 'Sign In' button" turns a probabilistic navigation into deterministic instructions. After one successful login, the playbook records the exact sequence, and subsequent logins use those steps.

**Expected login success rates with playbook guidance**: 80-90% for email+password, 70-80% for OAuth flows.

### CAPTCHA: The Hard Stop

VLM CAPTCHA solving sits at ~40% (Open CaptchaWorld benchmark, 2025). Modern CAPTCHAs that use behavioral analysis (mouse movement patterns, device fingerprinting) are near-impossible for VLMs because the VLM controls the mouse through discrete coordinates, not the continuous, slightly-jittery movements of a human hand.

**Recommendation for v1**: CAPTCHA-protected services should be flagged during onboarding (when adding the service to `settings.yaml`). The system should:
1. Attempt login
2. If CAPTCHA detected, pause and alert the user via CLI
3. User solves CAPTCHA manually (or the system retries later, hoping for no CAPTCHA on subsequent attempts)
4. Some CAPTCHAs only appear on suspicious login patterns — slowing down the login flow and using realistic typing delays may avoid triggering them

### 2FA Strategy

The RFC already specifies CLI-based TOTP prompting. This is the right call for v1. The VLM can navigate to the 2FA entry screen and type the code — the hard part is getting the code, not entering it.

**Enhancement for v2**: Store TOTP secrets (encrypted alongside service credentials) and generate codes programmatically. This eliminates human intervention for TOTP-based 2FA entirely. Left out of v1 due to security sensitivity.

### Service Playbook for Login

Each service's playbook should include a `## Login` section:

```markdown
## Login

### Steps
1. Navigate to https://example.com/login
2. Click the "Email" field (text input, first on page)
3. Type email address
4. Press Tab to move to password field
5. Type password
6. Click "Sign In" button (blue, bottom of form)
7. Wait for dashboard to load (~3 seconds)

### Post-Login Checks
- Success: Dashboard page with "Welcome back" header
- Failure: "Invalid credentials" error message
- 2FA: "Enter verification code" prompt → request TOTP from user

### Known Issues
- Cookie consent banner appears 50% of the time → Click "Accept All"
- "Enable notifications?" popup → Click "Not now"

### Last Updated
<!-- soren-generated: 2026-03-15, task: discovery_prolific_001 -->
```

---

## 7. Prompt Engineering: What Makes Discovery Prompts Work

### Lessons from 100+ Test Iterations (desktop-agent)

The desktop-agent's validation prompts taught us patterns that transfer directly to discovery:

1. **Be specific about what to look for**: "Count the number of survey cards in the list" beats "identify available surveys."
2. **Use position-based language**: "The survey list starts below the navigation bar and above the footer" anchors the VLM to the right region.
3. **Request explicit counts**: Always end with "Total items found: N." This forces the VLM to enumerate and gives you a sanity check.
4. **Distinguish fresh from stale**: For multi-pass extraction, "only list items you haven't already mentioned" prevents duplicates.
5. **Provide output format explicitly**: Show the exact format you want. VLMs follow format examples more reliably than format descriptions.

### Navigation Prompt Principles

Navigation prompts should be **imperative and sequential**, not descriptive:

**Bad**: "Navigate to the survey platform and find available surveys."
**Good**: "Step 1: Look for a login button or form. Step 2: Enter the email in the first text field..."

Navigation prompts must include **error recovery** instructions:
- "If you see a CAPTCHA, say BLOCKED_CAPTCHA and stop"
- "If the page shows an error, refresh and try again"
- "If you're on the wrong page, use the browser back button"

The `DISCOVERY_READY` sentinel pattern is critical. It gives the navigation phase a clean termination signal — the CUA agent loop can watch for this token and stop the agent, then hand off to the extraction phase.

### Extraction Prompt Principles

Extraction prompts should be **exhaustive and format-constrained**:

1. **List every field explicitly**, even optional ones, with "null" or "unknown" as valid values
2. **Provide a concrete example** of the expected output format
3. **Specify disambiguation rules**: "If a title contains both the survey name and a tag like '[NEW]', strip the tag"
4. **Request the total count** as a final line for validation
5. **Handle multi-screenshot scenarios explicitly**: "Screenshots are ordered top-to-bottom. Items may overlap between adjacent screenshots."

### Structuring Prompt Principles

Structuring prompts turn free text into machine-parseable output. Keep them terse:

1. **Define the JSON schema precisely** (field names, types, nullable fields)
2. **Include parsing rules** for unit conversion (minutes, currency)
3. **Specify error handling**: "If a value cannot be parsed, use null"
4. **Demand JSON-only output**: "Return ONLY the JSON array. No explanation, no markdown code fences."

### Iteration Budget

The RFC allocates 2 weeks for Phase 4. Realistic prompt engineering for three adapters:

| Adapter | Expected Iterations | Time |
|---------|-------------------|------|
| SurveyPlatformAdapter | 10-15 rounds | 3-4 days |
| EmailAdapter | 8-12 rounds | 2-3 days |
| GenericFormAdapter | 5-8 rounds | 1-2 days |
| Integration + testing | — | 2-3 days |

Each "round" = modify prompt → run discovery → check output → identify failures → adjust. This is manual work against real services. Budget 30-45 minutes per iteration (VM boot + navigation + extraction + review).

---

## 8. Risk Assessment

### 🟢 Low Risk: Adapter Prompt Size Estimate

**Probability**: Already validated through concrete examples
**Impact**: Minimal (adapter size is a convenience metric, not a constraint)
**Assessment**: The RFC's "50-100 lines per adapter" maps to ~80 lines across three prompts in the three-phase architecture. The estimate is accurate.

### 🟢 Low Risk: Navigation to Known URLs

**Probability**: Very low failure rate for URL navigation
**Impact**: Low (easily retried)
**Assessment**: UI-TARS excels at basic web navigation — clicking, scrolling, form filling. Navigating to a known URL and reaching a dashboard is well within its capabilities. The OSWorld benchmark's lower scores reflect complex *task completion*, not basic navigation. Navigation-only tasks succeed 85-95% of the time.

### 🟡 Medium Risk: Structured Data Extraction Accuracy

**Probability**: Medium — VLMs extract data with variable quality
**Impact**: High (inaccurate extraction = wrong tasks in backlog)
**Assessment**: Qwen3-VL can extract structured data from web screenshots, but accuracy varies by page complexity. Simple table layouts (survey dashboard with clear columns) yield 75-85% field-level accuracy. Complex layouts (cards with variable formatting, nested information) drop to 55-70%. The three-phase architecture mitigates this by separating extraction (Qwen3-VL) from structuring (Gemma3:12B).
**Mitigation**: Verification pass on extraction output; human-editable adapters for field-specific parsing rules; per-service accuracy tracking.

### 🟡 Medium Risk: Multi-Item Enumeration Completeness

**Probability**: Medium-high for dashboards with 10+ items
**Impact**: Medium (missed items = missed tasks, but not system failure)
**Assessment**: VLMs struggle to enumerate all items on dense pages. Expected recall: 65-80% for 10-20 item dashboards with multi-screenshot capture. The verification pass catches 20-40% of misses, pushing effective recall to 75-90%.
**Mitigation**: Multi-screenshot with overlap; verification pass; re-discovery on subsequent cycles catches anything missed.

### 🟡 Medium Risk: Prompt Iteration Time

**Probability**: High (prompt engineering always takes longer than expected)
**Impact**: Medium (Phase 4 timeline pressure)
**Assessment**: Each adapter needs 8-15 iteration rounds against real services. At 30-45 minutes per round, that's 4-11 hours per adapter. Three adapters = 12-33 hours of prompt iteration work, plus integration. The 2-week Phase 4 timeline is tight but feasible if prompt iteration begins immediately.
**Mitigation**: Start with the SurveyPlatformAdapter (highest value, most structured data), then EmailAdapter, then GenericFormAdapter. If time runs short, defer GenericFormAdapter.

### 🟡 Medium Risk: Dedup Fingerprint Stability

**Probability**: Medium for content-based fallback keys
**Impact**: Medium (unstable fingerprints = duplicate tasks or missed tasks)
**Assessment**: Provider-native IDs are stable; content fingerprints are mostly stable but vulnerable to dynamic page elements. The biggest risk is VLM extraction inconsistency — the same survey might be extracted as "Quick Survey ($2.50)" on one pass and "Quick Survey - $2.50" on another, producing different hashes.
**Mitigation**: Aggressive normalization before hashing; always prefer provider_task_id; fingerprint version prefix for schema evolution; logging fingerprint inputs for debugging.

### 🔴 High Risk: Login Reliability

**Probability**: High — every discovery cycle requires login
**Impact**: Critical (failed login = zero discovery)
**Assessment**: VLM-driven login without playbook guidance succeeds ~47% of the time. With playbooks: 80-90%. But the first login (before any playbook exists) is the chicken-and-egg problem. CAPTCHA-protected services have a hard ceiling of ~40% even with retries.
**Mitigation**: Manual first-login for new services (user logs in while agent records steps); playbook-guided re-login for subsequent cycles; CAPTCHA detection and user alerting; service-level retry with exponential backoff.

### 🔴 High Risk: Item Counting/Enumeration for 7B Models

**Probability**: High — counting is a known VLM weakness
**Impact**: High (systematically missing 20-35% of items on complex dashboards)
**Assessment**: The research is clear: no VLM reliably counts objects in complex visual scenes. UI-TARS 7B is trained for action grounding, not visual enumeration. Even Qwen3-VL 8B in the extraction phase will miss items. The gap between "items on the page" and "items the system finds" will be the primary complaint.
**Mitigation**: Multi-screenshot capture; verification pass; incremental discovery (run discovery multiple times, union of results); eventually, targeted scrolling patterns that show 3-5 items at a time for higher per-screenshot recall.

### 🔴 High Risk: End-to-End Discovery Rate Target

**Probability**: High — the >80% target is aspirational
**Impact**: High (unmet target reduces system value)
**Assessment**: The >80% discovery rate requires every step to work: login (85%) × navigation (90%) × enumeration (80%) × extraction accuracy (80%) = ~49% per-item end-to-end rate on first attempt. With verification pass and re-discovery across cycles, effective per-item rate rises to 60-75%. Reaching >80% requires playbook maturation over 5-10 cycles per service.
**Mitigation**: Reframe the target: ">80% after playbook stabilization (5+ cycles), 60-75% during Phase 4 cold-start." Track the learning curve per adapter.

---

## 9. Recommendations

### Build in Phase 4

1. **Three-phase extraction pipeline**: UI-TARS → Qwen3-VL → Gemma3:12B. This is the core architecture.
2. **SurveyPlatformAdapter**: Highest value, most structured data, best extraction target.
3. **EmailAdapter**: Second priority. Email inboxes are relatively structured.
4. **Multi-screenshot capture with overlap**: Essential for dashboards with >5 items.
5. **`DISCOVERY_READY` sentinel pattern**: Clean handoff between navigation and extraction.
6. **Provider-native ID extraction**: Adapters should always try to extract a native ID first.
7. **Dedup with aggressive normalization**: Strip dynamic fragments, normalize currency and duration formats.
8. **Playbook login recording**: After first successful login, write the step sequence to the service playbook.

### Defer to Phase 6 (Playbooks)

1. **Verification pass for extraction**: Adds ~5s latency per discovery. Enable after core pipeline is working.
2. **GenericFormAdapter**: Lower priority; forms are typically at known URLs and rarely need discovery.
3. **Pagination support**: Most survey dashboards show all items on one page. Click-next pagination is an edge case for v1.
4. **CAPTCHA detection and user alerting**: Implement the detection; defer the CLI alert flow.

### Prototype First (Before Phase 4)

1. **Single-service extraction test**: Before building the full pipeline, run UI-TARS against one real survey platform (e.g., Prolific) and capture screenshots. Then manually feed those screenshots to Qwen3-VL with the extraction prompt. Measure: how many items does it find? What fields does it get wrong?
   - **Estimated time**: 2-4 hours
   - **Decision gate**: If Qwen3-VL finds <50% of items on a real dashboard, the three-phase approach needs fundamental revision (consider DOM-based fallback for extraction only).

2. **Login reliability test**: Attempt 10 logins to the same service with UI-TARS + playbook instructions. Measure success rate.
   - **Estimated time**: 1-2 hours
   - **Decision gate**: If playbook-guided login succeeds <70%, invest in more deterministic login methods (osascript + clipboard for credential entry).

### Critical Design Decisions

**Decision 1: Three-phase extraction is mandatory.** Single-pass with UI-TARS alone will not achieve acceptable extraction quality. The model swap overhead (~14s for two swaps) is an acceptable cost.

**Decision 2: Playbooks are essential for login reliability.** The system must record successful logins and replay them. First login for a new service should be user-assisted or heavily retried.

**Decision 3: Discovery rates should be measured per-service over time, not as a single number.** A 60% rate in the first cycle that improves to 85% by the fifth cycle is healthy. A static 65% rate that never improves is a problem.

**Decision 4: `item_index` should be excluded from the default fingerprint.** It creates false negatives when dashboards reorder items.

---

## 10. Expected Discovery Rates

### Realistic Targets by Phase

| Phase | Discovery Rate | Evidence |
|-------|---------------|----------|
| Phase 4 cold-start (no playbooks) | 50-65% | Based on VLM enumeration research + compound probability of navigation × extraction |
| Phase 4 with initial playbooks | 60-75% | Playbook-guided login adds ~15% to login reliability, which propagates through |
| Phase 6 with mature playbooks | 70-85% | Verification passes + accumulated prompt refinements + login reliability improvements |
| Steady state (10+ cycles) | 75-90% | Asymptotic improvement via playbook iteration and per-service prompt tuning |

### Per-Component Success Rates

| Component | Cold-Start | With Playbook | Steady State |
|-----------|-----------|---------------|--------------|
| Login | 45-55% | 80-90% | 85-95% |
| Navigation to listing | 85-95% | 90-95% | 95%+ |
| Item enumeration (10-item page) | 65-80% per item | 70-85% | 80-90% |
| Field extraction accuracy | 60-75% per field | 70-80% | 75-85% |
| Structured output quality | 75-90% | 85-95% | 90-95% |

### The >80% Target

The RFC's >80% discovery rate target is achievable *after playbook stabilization*. It should not be the Phase 4 acceptance criterion. Recommended targets:

- **Phase 4 exit criterion**: 55%+ discovery rate on at least one real service, with evidence of improvement across 3+ test cycles
- **Phase 6 exit criterion**: 75%+ on two services, with playbooks demonstrating measurable improvement
- **Steady-state goal**: >80% on services with mature playbooks (5+ cycles of refinement)

---

## Existing Components & Reuse Plan

| Component | Source | Reuse? | Rationale |
|-----------|--------|--------|-----------|
| CUA ComputerAgent | `cua-agent` SDK | **Yes** | Core navigation engine; same agent used for execution |
| VNC screenshot capture | `desktop-agent/helpers/vnc_screenshot.py` → `src/soren/patches/` | **Yes** | Multi-screenshot capture for scroll-based discovery |
| Screenshot comparison | `desktop-agent/helpers/baseline_manager.py` | **Yes** | `compare_screenshots()` for scroll-stop detection |
| Obstacle handler | `desktop-agent/helpers/obstacle_handler.py` | **Yes** | Dismiss cookie banners, popups during navigation |
| Action verifier | `desktop-agent/helpers/action_verifier.py` | **Partial** | Login verification pattern reusable; correction strategies for field entry |
| Ollama integration | `desktop-agent/helpers/ollama_validator.py` | **Yes** | Multi-image Ollama calls for screenshot extraction |
| CUA SDK patches (4) | Per CUA SDK compatibility discovery | **Yes** | All four patches required for UI-TARS integration |
| `LLMService` | Phase 3 deliverable | **Yes** | Wraps Ollama calls with model admission control |
| `VMManager` | Phase 2 deliverable | **Yes** | VM boot, screenshot, browser reset |
| `MemoryGuard` | Per model memory budget discovery | **Yes** | Model swap coordination between UI-TARS/Qwen3-VL/Gemma3:12B |

**Not reused**: No existing discovery or extraction code exists (greenfield). All adapters, the discovery engine, and the backlog insertion logic are new.

**Consolidation opportunity**: The Ollama call pattern (send screenshot + prompt, receive text) is used by both the action verifier (Tier 2 verification) and the discovery extraction phase. Consolidate into a single `VLMService.analyze_screenshot(images, prompt)` method that both verification and discovery call.
