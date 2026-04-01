"""Tests for max_instruction_tokens safety valve on PersonaContext (Task 022).

Verifies token limit truncation:
- Content under limit passes unchanged
- Content over limit truncates lowest-priority sections first
- Base playbook truncated before host content
- Site playbook is last to be truncated
- Warning logged on truncation
- Truncation marker appears in output
- None limit (backward compat) does not truncate
- Custom token_counter function used when provided
"""

from __future__ import annotations

import logging

from soren.models.persona import Persona
from soren.models.task import Task
from soren.services.persona_context import PersonaContext


def _make_persona() -> Persona:
    return Persona(name="Test User", id="p1", task_sources=[], enabled=True)


def _make_task() -> Task:
    return Task(
        id="t1",
        dedup_key="k1",
        title="Complete survey",
        description="Fill out the survey",
        source_name="prolific",
        task_type="survey",
    )


def _long_text(approx_tokens: int) -> str:
    """Generate text that is approximately the given number of tokens (4 chars ≈ 1 token)."""
    return "word " * approx_tokens


class TestContentUnderLimitUnchanged:
    """Content under the token limit passes through unchanged."""

    def test_short_content_unchanged(self) -> None:
        playbook = "Short playbook instructions."
        result = PersonaContext.build(
            _make_persona(),
            _make_task(),
            playbook_content=playbook,
            max_instruction_tokens=4000,
        )
        assert "Short playbook instructions." in result
        assert "truncated" not in result.lower()


class TestContentOverLimitTruncates:
    """Content over the limit truncates lowest-priority sections first."""

    def test_long_playbook_truncated(self) -> None:
        # Build playbook with base (lowest priority) section that's very long
        base = "## General Guidelines\n" + _long_text(500)
        host = "## Host Instructions\n" + _long_text(100)
        site = "## Site-Specific Instructions\n" + _long_text(100)
        playbook = f"{site}\n\n{host}\n\n{base}"

        result = PersonaContext.build(
            _make_persona(),
            _make_task(),
            playbook_content=playbook,
            max_instruction_tokens=300,
        )
        # Site content should be preserved (highest priority)
        assert "Site-Specific Instructions" in result
        # Truncation marker should appear
        assert "truncated" in result.lower()


class TestBasePlaybookTruncatedBeforeHost:
    """Base playbook content truncated before host content."""

    def test_base_dropped_before_host(self) -> None:
        base = "## General Guidelines\n" + _long_text(200)
        host = "## Host Instructions\nHost guidance here."
        site = "## Site-Specific Instructions\nSite steps here."
        playbook = f"{site}\n\n{host}\n\n{base}"

        # Limit allows site + host (~25 tokens) but not base (~255 tokens)
        result = PersonaContext.build(
            _make_persona(),
            _make_task(),
            playbook_content=playbook,
            max_instruction_tokens=100,
        )
        assert "Site-Specific Instructions" in result
        assert "Host Instructions" in result
        # Base should be removed (lowest priority)
        assert "General Guidelines" not in result


class TestSitePlaybookLastToTruncate:
    """Site playbook content is the last to be truncated."""

    def test_site_preserved_longest(self) -> None:
        base = "## General Guidelines\n" + _long_text(200)
        host = "## Host Instructions\n" + _long_text(200)
        site = "## Site-Specific Instructions\nCritical site steps."
        playbook = f"{site}\n\n{host}\n\n{base}"

        # Very tight limit should still keep site content
        result = PersonaContext.build(
            _make_persona(),
            _make_task(),
            playbook_content=playbook,
            max_instruction_tokens=100,
        )
        assert "Site-Specific Instructions" in result


class TestWarningLoggedOnTruncation:
    """Warning logged when truncation occurs."""

    def test_truncation_logs_warning(self, caplog) -> None:
        playbook = _long_text(500)

        with caplog.at_level(logging.WARNING, logger="soren.services.persona_context"):
            PersonaContext.build(
                _make_persona(),
                _make_task(),
                playbook_content=playbook,
                max_instruction_tokens=50,
            )
        assert any("truncat" in record.message.lower() for record in caplog.records)


class TestTruncationMarkerInOutput:
    """Truncation marker appears in output when content is truncated."""

    def test_marker_present(self) -> None:
        playbook = _long_text(500)
        result = PersonaContext.build(
            _make_persona(),
            _make_task(),
            playbook_content=playbook,
            max_instruction_tokens=50,
        )
        assert "lower-priority instructions were truncated" in result


class TestNoneLimitNoTruncation:
    """None limit (backward compat) does not truncate."""

    def test_none_limit_preserves_all(self) -> None:
        long_playbook = _long_text(2000)
        result = PersonaContext.build(
            _make_persona(),
            _make_task(),
            playbook_content=long_playbook,
            max_instruction_tokens=None,
        )
        # All content should be present (no truncation)
        assert "truncated" not in result.lower()
        # The long text should be fully present in the playbook section
        assert "word word word" in result


class TestMemoriesNotDroppedWhenPlaybookTruncated:
    """Memories survive when playbook is truncated (uses post-truncation budget)."""

    def test_memories_preserved_after_playbook_truncation(self) -> None:
        base = "## General Guidelines\n" + _long_text(300)  # Will be truncated
        memories = ["Short memory entry."]
        result = PersonaContext.build(
            _make_persona(),
            _make_task(),
            playbook_content=base,
            memories=memories,
            max_instruction_tokens=50,
        )
        assert "Short memory entry." in result


class TestCustomTokenCounter:
    """Custom token_counter function is used when provided."""

    def test_custom_counter_used(self) -> None:
        call_count = 0

        def counting_counter(text: str) -> int:
            nonlocal call_count
            call_count += 1
            return len(text) // 4  # Same heuristic but tracked

        playbook = _long_text(500)
        PersonaContext.build(
            _make_persona(),
            _make_task(),
            playbook_content=playbook,
            max_instruction_tokens=50,
            token_counter=counting_counter,
        )
        assert call_count > 0
