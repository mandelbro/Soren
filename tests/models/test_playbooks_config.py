"""Tests for PlaybooksConfig extensions."""

from soren.models.playbooks import PlaybooksConfig


class TestPlaybooksConfig:
    def test_default_values(self):
        config = PlaybooksConfig()
        assert config.site_playbooks_dir == "data/playbooks/site"
        assert config.host_playbooks_dir == "data/playbooks/host"
        assert config.base_playbooks_dir == "data/playbooks/base"
        assert config.cascade_enabled is True
        assert config.auto_learn_host is True

    def test_existing_fields_preserved(self):
        config = PlaybooksConfig()
        assert config.base_dir == "data/playbooks"
        assert config.write_back is True

    def test_no_args_construction(self):
        config = PlaybooksConfig()
        assert config is not None

    def test_override_tier_directories(self):
        config = PlaybooksConfig(
            site_playbooks_dir="/custom/site",
            host_playbooks_dir="/custom/host",
            base_playbooks_dir="/custom/base",
            cascade_enabled=False,
            auto_learn_host=False,
        )
        assert config.site_playbooks_dir == "/custom/site"
        assert config.host_playbooks_dir == "/custom/host"
        assert config.base_playbooks_dir == "/custom/base"
        assert config.cascade_enabled is False
        assert config.auto_learn_host is False

    def test_backward_compatible_partial_override(self):
        config = PlaybooksConfig(base_dir="/old/path", write_back=False)
        assert config.base_dir == "/old/path"
        assert config.write_back is False
        # New fields use defaults
        assert config.site_playbooks_dir == "data/playbooks/site"
        assert config.cascade_enabled is True
