"""Tests for SitePlaybook Pydantic models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from soren.models.site_playbook import (
    SCHEMA_VERSION,
    DiscoveryFlow,
    ExecutionFlow,
    FlowPlaybook,
    LoginFlow,
    PlaybookStep,
    SiteFlows,
    SitePlaybook,
)


def _step(seq: int = 1, **overrides) -> dict:
    return {
        "seq": seq,
        "instruction": f"Do step {seq}",
        "expected_after": f"Step {seq} done",
        **overrides,
    }


class TestPlaybookStep:
    def test_round_trip(self):
        step = PlaybookStep(**_step(1))
        data = step.model_dump()
        rebuilt = PlaybookStep.model_validate(data)
        assert rebuilt.seq == 1
        assert rebuilt.instruction == "Do step 1"

    def test_credential_field_placeholder(self):
        step = PlaybookStep(**_step(1, field="credential:username"))
        assert step.field == "credential:username"

    def test_tab_transition_fields(self):
        step = PlaybookStep(**_step(1, triggers_new_tab=True, host_domain="qualtrics.com"))
        assert step.triggers_new_tab is True
        assert step.host_domain == "qualtrics.com"


class TestFlowPlaybook:
    def test_confidence_in_bounds(self):
        flow = FlowPlaybook(confidence=0.85, steps=[PlaybookStep(**_step(1))])
        assert flow.confidence == 0.85

    def test_confidence_rejects_above_one(self):
        with pytest.raises(ValidationError):
            FlowPlaybook(confidence=1.5, steps=[PlaybookStep(**_step(1))])

    def test_confidence_rejects_below_zero(self):
        with pytest.raises(ValidationError):
            FlowPlaybook(confidence=-0.1, steps=[PlaybookStep(**_step(1))])

    def test_max_steps_default(self):
        flow = FlowPlaybook(steps=[PlaybookStep(**_step(1))])
        assert flow.max_steps == 20


class TestLoginFlow:
    def test_defaults(self):
        flow = LoginFlow(steps=[PlaybookStep(**_step(1))])
        assert flow.auth_complete_indicator == ""
        assert flow.fallback_on_failure == "stop_and_report"

    def test_fallback_generic(self):
        flow = LoginFlow(
            steps=[PlaybookStep(**_step(1))],
            fallback_on_failure="generic",
        )
        assert flow.fallback_on_failure == "generic"


class TestSitePlaybook:
    def test_full_construction(self):
        playbook = SitePlaybook(
            source_name="sproutful",
            source_url="https://sproutful.com",
            flows=SiteFlows(
                login=LoginFlow(
                    steps=[PlaybookStep(**_step(1)), PlaybookStep(**_step(2))],
                    auth_complete_indicator="Dashboard loaded",
                ),
                discovery=DiscoveryFlow(
                    steps=[PlaybookStep(**_step(1))],
                    discovery_ready_indicator="Task list visible",
                ),
                execution={
                    "survey": ExecutionFlow(
                        steps=[PlaybookStep(**_step(1))],
                        completion_indicator="Thank you",
                    ),
                },
            ),
        )
        assert playbook.source_name == "sproutful"
        assert playbook.flows.login is not None
        assert playbook.flows.discovery is not None
        assert "survey" in playbook.flows.execution

    def test_minimal_construction(self):
        playbook = SitePlaybook(
            source_name="test",
            source_url="https://test.com",
            flows=SiteFlows(
                login=LoginFlow(steps=[PlaybookStep(**_step(1))]),
            ),
        )
        assert playbook.flows.discovery is None
        assert playbook.flows.execution is None

    def test_version_defaults_to_schema_version(self):
        playbook = SitePlaybook(
            source_name="test",
            source_url="https://test.com",
            flows=SiteFlows(),
        )
        assert playbook.version == SCHEMA_VERSION

    def test_created_at_is_utc(self):
        playbook = SitePlaybook(
            source_name="test",
            source_url="https://test.com",
            flows=SiteFlows(),
        )
        assert playbook.created_at.tzinfo is not None

    def test_round_trip_from_dict(self):
        data = {
            "source_name": "test",
            "source_url": "https://test.com",
            "created_at": datetime.now(UTC).isoformat(),
            "version": 1,
            "flows": {
                "login": {
                    "steps": [_step(1)],
                    "auth_complete_indicator": "done",
                },
            },
        }
        playbook = SitePlaybook.model_validate(data)
        dumped = playbook.model_dump()
        rebuilt = SitePlaybook.model_validate(dumped)
        assert rebuilt.source_name == "test"

    def test_execution_keyed_by_task_type(self):
        playbook = SitePlaybook(
            source_name="multi",
            source_url="https://multi.com",
            flows=SiteFlows(
                execution={
                    "survey": ExecutionFlow(
                        steps=[PlaybookStep(**_step(1))],
                    ),
                    "email_reply": ExecutionFlow(
                        steps=[PlaybookStep(**_step(1))],
                    ),
                },
            ),
        )
        assert "survey" in playbook.flows.execution
        assert "email_reply" in playbook.flows.execution
