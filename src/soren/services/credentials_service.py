"""Encrypted credential storage and management for Soren."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from cryptography.fernet import Fernet, InvalidToken


@dataclass
class CredentialRef:
    """Credential metadata safe to display in CLI output."""

    ref: str
    service_type: str
    updated_at: str


@dataclass
class StoredCredential:
    """Full credential record for execution-time login handoff."""

    ref: str
    service_type: str
    username: str
    password: str
    metadata: dict[str, Any]
    updated_at: str


def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class CredentialStore:
    """Store encrypted credentials at rest using Fernet."""

    def __init__(
        self,
        encrypted_path: Path | str,
        *,
        key_path: Path | str | None = None,
        env_key_var: str = "SOREN_CREDENTIALS_KEY",
    ) -> None:
        self._encrypted_path = Path(encrypted_path)
        self._key_path = Path(key_path) if key_path else Path("data/credentials.key")
        self._env_key_var = env_key_var
        self._fernet = Fernet(self._resolve_key())

    def list_refs(self) -> list[CredentialRef]:
        """Return safe credential metadata, excluding secrets."""
        payload = self._read_payload()
        refs: list[CredentialRef] = []
        for item in payload.get("credentials", []):
            refs.append(
                CredentialRef(
                    ref=str(item.get("ref", "")),
                    service_type=str(item.get("service_type", "")),
                    updated_at=str(item.get("updated_at", "")),
                )
            )
        return refs

    def get(self, ref: str) -> StoredCredential:
        """Return full credential payload for a reference."""
        payload = self._read_payload()
        ref_clean = ref.strip()
        if not ref_clean:
            raise ValueError("Credential ref is required.")

        for item in payload.get("credentials", []):
            if str(item.get("ref", "")).strip() != ref_clean:
                continue
            return StoredCredential(
                ref=ref_clean,
                service_type=str(item.get("service_type", "")),
                username=str(item.get("username", "")),
                password=str(item.get("password", "")),
                metadata=item.get("metadata", {}) if isinstance(item.get("metadata", {}), dict) else {},
                updated_at=str(item.get("updated_at", "")),
            )

        raise KeyError(f"Credential ref not found: {ref_clean}")

    def add(
        self,
        *,
        ref: str,
        service_type: str,
        username: str,
        password: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add/update a credential record in encrypted storage."""
        payload = self._read_payload()
        records = payload.setdefault("credentials", [])
        now = _iso_now()
        clean_ref = ref.strip()
        clean_service = service_type.strip()
        if not clean_ref or not clean_service:
            raise ValueError("Credential ref and service_type are required.")

        replacement = {
            "ref": clean_ref,
            "service_type": clean_service,
            "username": username,
            "password": password,
            "metadata": metadata or {},
            "updated_at": now,
        }

        replaced = False
        for idx, record in enumerate(records):
            if record.get("ref") == clean_ref:
                records[idx] = replacement
                replaced = True
                break
        if not replaced:
            records.append(replacement)

        payload["version"] = 1
        payload["updated_at"] = now
        self._write_payload(payload)

    def encrypt_plaintext_file(self, plaintext_path: Path | str) -> int:
        """Encrypt plaintext YAML/JSON credentials into the managed encrypted store."""
        source = Path(plaintext_path)
        if not source.exists():
            raise FileNotFoundError(f"Plaintext file not found: {source}")

        raw = source.read_text(encoding="utf-8").strip()
        if not raw:
            raise ValueError("Plaintext credentials file is empty.")

        parsed: Any
        if source.suffix.lower() == ".json":
            parsed = json.loads(raw)
        else:
            parsed = yaml.safe_load(raw)

        records = self._normalize_records(parsed)
        payload = {"version": 1, "updated_at": _iso_now(), "credentials": records}
        self._write_payload(payload)
        return len(records)

    def _normalize_records(self, parsed: Any) -> list[dict[str, Any]]:
        """Normalize YAML/JSON credentials into canonical record format."""
        if isinstance(parsed, dict) and isinstance(parsed.get("credentials"), list):
            items = parsed["credentials"]
        elif isinstance(parsed, list):
            items = parsed
        elif isinstance(parsed, dict):
            items = []
            for ref, value in parsed.items():
                if not isinstance(value, dict):
                    continue
                record = {"ref": ref, **value}
                items.append(record)
        else:
            raise ValueError("Unsupported plaintext credential format.")

        normalized: list[dict[str, Any]] = []
        now = _iso_now()
        for item in items:
            if not isinstance(item, dict):
                continue
            ref = str(item.get("ref", "")).strip()
            service_type = str(item.get("service_type", "")).strip()
            username = str(item.get("username", ""))
            password = str(item.get("password", ""))
            if not ref or not service_type:
                continue
            normalized.append(
                {
                    "ref": ref,
                    "service_type": service_type,
                    "username": username,
                    "password": password,
                    "metadata": item.get("metadata", {}),
                    "updated_at": str(item.get("updated_at", now)),
                }
            )

        if not normalized:
            raise ValueError("No valid credential records found in plaintext file.")
        return normalized

    def _resolve_key(self) -> bytes:
        """Load key from env or key file, generating a new key when needed."""
        env_value = os.environ.get(self._env_key_var, "").strip()
        if env_value:
            return env_value.encode("utf-8")

        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        if self._key_path.exists():
            return self._key_path.read_text(encoding="utf-8").strip().encode("utf-8")

        key = Fernet.generate_key()
        self._key_path.write_text(key.decode("utf-8"), encoding="utf-8")
        os.chmod(self._key_path, 0o600)
        return key

    def _read_payload(self) -> dict[str, Any]:
        """Read and decrypt credential payload. Returns empty payload if file absent."""
        if not self._encrypted_path.exists():
            return {"version": 1, "credentials": []}
        encrypted = self._encrypted_path.read_bytes()
        try:
            plaintext = self._fernet.decrypt(encrypted).decode("utf-8")
        except InvalidToken as e:
            raise ValueError("Failed to decrypt credentials. Check SOREN_CREDENTIALS_KEY/key file.") from e
        data = json.loads(plaintext)
        if not isinstance(data, dict):
            raise ValueError("Invalid encrypted credential payload.")
        data.setdefault("credentials", [])
        return data

    def _write_payload(self, payload: dict[str, Any]) -> None:
        """Encrypt and persist payload to disk."""
        self._encrypted_path.parent.mkdir(parents=True, exist_ok=True)
        plaintext = json.dumps(payload, sort_keys=True).encode("utf-8")
        encrypted = self._fernet.encrypt(plaintext)
        self._encrypted_path.write_bytes(encrypted)
