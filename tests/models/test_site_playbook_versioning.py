"""Tests for site playbook schema versioning infrastructure."""

import pytest
import yaml

from soren.models.site_playbook import (
    _MIGRATIONS,
    SCHEMA_VERSION,
    PlaybookValidationError,
    PlaybookVersionError,
    SitePlaybook,
    _migrate,
    _register_migration,
    load_and_validate,
    validate_credential_placeholders,
    validate_step_sequences,
)


def _minimal_playbook_dict(version: int = SCHEMA_VERSION) -> dict:
    return {
        "source_name": "test",
        "source_url": "https://test.com",
        "version": version,
        "flows": {
            "login": {
                "steps": [
                    {"seq": 1, "instruction": "Go to login", "expected_after": "Login page visible"},
                    {
                        "seq": 2,
                        "instruction": "Enter creds",
                        "expected_after": "Logged in",
                        "field": "credential:username",
                    },
                ],
            },
        },
    }


def _write_playbook(tmp_path, data: dict, filename: str = "test.yaml"):
    path = tmp_path / filename
    path.write_text(yaml.dump(data, sort_keys=False), encoding="utf-8")
    return path


class TestLoadAndValidateHappyPath:
    def test_loads_current_version(self, tmp_path):
        path = _write_playbook(tmp_path, _minimal_playbook_dict())
        playbook = load_and_validate(path)
        assert isinstance(playbook, SitePlaybook)
        assert playbook.source_name == "test"

    def test_credential_in_field_accepted(self, tmp_path):
        data = _minimal_playbook_dict()
        path = _write_playbook(tmp_path, data)
        playbook = load_and_validate(path)
        login_steps = playbook.flows.login.steps
        assert login_steps[1].field == "credential:username"


class TestVersionErrors:
    def test_future_version_raises(self, tmp_path):
        data = _minimal_playbook_dict(version=SCHEMA_VERSION + 1)
        path = _write_playbook(tmp_path, data)
        with pytest.raises(PlaybookVersionError, match="newer than"):
            load_and_validate(path)


class TestMigration:
    def test_old_version_triggers_migration(self, tmp_path):
        # Register a mock v0->v1 migration
        original_migrations = _MIGRATIONS.copy()
        try:

            @_register_migration(0, 1)
            def _v0_to_v1(raw: dict) -> dict:
                raw.setdefault("migrated", True)
                return raw

            data = _minimal_playbook_dict(version=0)
            path = _write_playbook(tmp_path, data)
            playbook = load_and_validate(path)
            assert playbook.version == SCHEMA_VERSION

            # Verify migration wrote back to disk
            reloaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            assert reloaded["version"] == SCHEMA_VERSION
        finally:
            _MIGRATIONS.clear()
            _MIGRATIONS.update(original_migrations)

    def test_missing_migration_raises(self, tmp_path):
        original_migrations = _MIGRATIONS.copy()
        try:
            _MIGRATIONS.clear()
            with pytest.raises(PlaybookVersionError, match="No migration path"):
                _migrate({"version": 0}, 0, 1)
        finally:
            _MIGRATIONS.clear()
            _MIGRATIONS.update(original_migrations)


class TestCredentialValidation:
    def test_credential_in_instruction_rejected(self, tmp_path):
        data = _minimal_playbook_dict()
        data["flows"]["login"]["steps"][0]["instruction"] = "Enter credential:password here"
        path = _write_playbook(tmp_path, data)
        with pytest.raises(PlaybookValidationError, match="credential placeholder"):
            load_and_validate(path)

    def test_credential_in_expected_after_rejected(self, tmp_path):
        data = _minimal_playbook_dict()
        data["flows"]["login"]["steps"][0]["expected_after"] = "Shows credential:token"
        path = _write_playbook(tmp_path, data)
        with pytest.raises(PlaybookValidationError, match="credential placeholder"):
            load_and_validate(path)

    def test_validate_returns_violations(self):
        raw = {
            "flows": {
                "login": {
                    "steps": [
                        {"seq": 1, "instruction": "Use credential:pass", "expected_after": "ok"},
                    ],
                },
            },
        }
        violations = validate_credential_placeholders(raw)
        assert len(violations) == 1
        assert "instruction" in violations[0]


class TestStepSequenceValidation:
    def test_contiguous_sequences_accepted(self, tmp_path):
        data = _minimal_playbook_dict()
        path = _write_playbook(tmp_path, data)
        playbook = load_and_validate(path)
        assert playbook is not None

    def test_non_contiguous_sequences_rejected(self, tmp_path):
        data = _minimal_playbook_dict()
        data["flows"]["login"]["steps"] = [
            {"seq": 1, "instruction": "Step 1", "expected_after": "Done 1"},
            {"seq": 3, "instruction": "Step 3", "expected_after": "Done 3"},
        ]
        path = _write_playbook(tmp_path, data)
        with pytest.raises(PlaybookValidationError, match="step sequence"):
            load_and_validate(path)

    def test_validate_returns_issues(self):
        raw = {
            "flows": {
                "discovery": {
                    "steps": [
                        {"seq": 1, "instruction": "a", "expected_after": "b"},
                        {"seq": 5, "instruction": "c", "expected_after": "d"},
                    ],
                },
            },
        }
        issues = validate_step_sequences(raw)
        assert len(issues) == 1
        assert "discovery" in issues[0]
