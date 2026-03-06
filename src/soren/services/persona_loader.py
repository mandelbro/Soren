"""PersonaLoader — discover and load persona YAML files."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from soren.models.persona import Persona


def load_persona(path: Path) -> Persona:
    """Parse and validate a single YAML persona file."""
    raw = yaml.safe_load(path.read_text())
    return Persona.model_validate(raw)


def load_all(directory: Path) -> list[Persona]:
    """Discover and load all *.yaml files in directory."""
    personas: list[Persona] = []
    if not directory.exists():
        return personas
    for f in sorted(directory.glob("*.yaml")):
        try:
            personas.append(load_persona(f))
        except (ValidationError, yaml.YAMLError):
            continue
    return personas


def get_personas_dir() -> Path:
    """Return default config/personas path."""
    return Path(__file__).resolve().parents[3] / "config" / "personas"
