"""Configuration loader for Soren settings."""

from pathlib import Path

import yaml
from pydantic import BaseModel

from soren.models.llm import LLMConfig
from soren.models.playbooks import PlaybooksConfig
from soren.models.scheduler import SchedulerConfig
from soren.models.verification import VerificationConfig
from soren.models.vlm import VLMConfig
from soren.models.vm import VMConfig


class SorenSettings(BaseModel):
    """Soren application settings."""

    vm: VMConfig = VMConfig()
    llm: LLMConfig = LLMConfig()
    vlm: VLMConfig = VLMConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    verification: VerificationConfig = VerificationConfig()
    playbooks: PlaybooksConfig = PlaybooksConfig()


def load_settings(path: Path | None = None) -> SorenSettings:
    """Load settings from YAML, merging with defaults."""
    _default_path = Path(__file__).resolve().parents[3] / "config" / "settings.yaml"
    config_path = path or _default_path
    defaults = SorenSettings()

    if not config_path.exists():
        return defaults

    raw = yaml.safe_load(config_path.read_text() or "{}")
    if not raw:
        return defaults

    vm_raw = raw.get("vm", {})
    vm = VMConfig.model_validate(vm_raw) if vm_raw else defaults.vm

    llm_raw = raw.get("llm", {})
    llm = LLMConfig.model_validate(llm_raw) if llm_raw else defaults.llm

    vlm_raw = raw.get("vlm", {})
    vlm = VLMConfig.model_validate(vlm_raw) if vlm_raw else defaults.vlm

    scheduler_raw = raw.get("scheduler", {})
    scheduler = SchedulerConfig.model_validate(scheduler_raw) if scheduler_raw else defaults.scheduler

    verification_raw = raw.get("verification", {})
    verification = VerificationConfig.model_validate(verification_raw) if verification_raw else defaults.verification

    playbooks_raw = raw.get("playbooks", {})
    playbooks = PlaybooksConfig.model_validate(playbooks_raw) if playbooks_raw else defaults.playbooks

    return SorenSettings(
        vm=vm, llm=llm, vlm=vlm, scheduler=scheduler, verification=verification, playbooks=playbooks
    )
