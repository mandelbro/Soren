"""Tests for HostPlaybook and BasePlaybook models."""

import pytest
from pydantic import ValidationError

from soren.models.host_playbook import BasePlaybook, HostPlaybook


class TestHostPlaybook:
    def test_full_construction(self):
        hp = HostPlaybook(
            host_domain="quickform.example",
            url_patterns=["*quickform.example/jfe/form/*"],
            learned_from="human",
            navigation={"cookie_selector": "#onetrust-accept"},
            quirks=["animation delay after each page"],
            confidence=0.9,
        )
        assert hp.host_domain == "quickform.example"
        assert hp.learned_from == "human"
        assert len(hp.quirks) == 1

    def test_minimal_construction(self):
        hp = HostPlaybook(host_domain="example.com")
        assert hp.url_patterns == []
        assert hp.quirks == []
        assert hp.confidence == 0.0
        assert hp.learned_from == "auto"
        assert hp.verified_at is None

    def test_matches_url_positive(self):
        hp = HostPlaybook(
            host_domain="quickform.example",
            url_patterns=["*quickform.example/jfe/form/*", "*quickform.example/SE/*"],
        )
        assert hp.matches_url("https://quickform.example/jfe/form/SV_abc123") is True

    def test_matches_url_negative(self):
        hp = HostPlaybook(
            host_domain="quickform.example",
            url_patterns=["*quickform.example/jfe/form/*"],
        )
        assert hp.matches_url("https://pollbox.example/r/abc") is False

    def test_matches_url_empty_patterns(self):
        hp = HostPlaybook(host_domain="example.com")
        assert hp.matches_url("https://example.com/anything") is False

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            HostPlaybook(host_domain="x.com", confidence=1.5)
        with pytest.raises(ValidationError):
            HostPlaybook(host_domain="x.com", confidence=-0.1)


class TestBasePlaybook:
    def test_from_survey_yaml_structure(self):
        bp = BasePlaybook(
            task_type="survey",
            version=1,
            patterns={
                "navigation": {
                    "advance_buttons": ["Next", "Continue"],
                    "submit_buttons": ["Submit", "Finish"],
                },
                "question_types": {
                    "multiple_choice": {"interaction": "Click a radio button"},
                },
                "completion": {
                    "success_indicators": ["Thank you"],
                },
            },
        )
        assert bp.task_type == "survey"
        assert "navigation" in bp.patterns
        assert "question_types" in bp.patterns

    def test_empty_patterns(self):
        bp = BasePlaybook(task_type="email_reply")
        assert bp.patterns == {}
        assert bp.version == 1

    def test_round_trip(self):
        data = {"task_type": "form", "version": 1, "patterns": {"key": "value"}}
        bp = BasePlaybook.model_validate(data)
        dumped = bp.model_dump()
        rebuilt = BasePlaybook.model_validate(dumped)
        assert rebuilt.task_type == "form"
        assert rebuilt.patterns == {"key": "value"}
