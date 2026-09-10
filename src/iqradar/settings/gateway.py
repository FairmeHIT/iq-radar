"""Runtime-configurable model-gateway endpoints for the test page.

The 测试 page can override two OpenAI-compatible endpoint pairs without
touching code or ``.env``:

- **模型列表获取** — the ``GET /v1/models`` pair used to discover the
  candidate model list;
- **模型调用** — the ``POST /v1/chat/completions`` pair every benchmark
  backend uses to invoke a model.

:class:`GatewaySettings` persists the overrides to a JSON file (0600) under
``data/settings/`` and mirrors them into the process environment so every
existing ``GATEWAY_*`` consumer picks them up immediately:

- the inference pair overrides ``GATEWAY_BASE_URL`` / ``GATEWAY_API_KEY``;
- the models pair gets dedicated ``IQRADAR_MODELS_BASE_URL`` /
  ``IQRADAR_MODELS_API_KEY`` variables (falling back to the inference pair
  when unset, which preserves the previous single-gateway behaviour).

Blank stored values mean "not overridden": the baseline environment values
(captured when the store is constructed, i.e. from ``.env``) apply again.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Mapping

from iqradar.shared.atomic_files import atomic_write_text

#: Environment variable mirrored with the *inference* (chat completions) pair.
INFERENCE_BASE_URL_ENV = "GATEWAY_BASE_URL"
INFERENCE_API_KEY_ENV = "GATEWAY_API_KEY"
#: Dedicated environment variables for the *models list* (GET /v1/models) pair.
MODELS_BASE_URL_ENV = "IQRADAR_MODELS_BASE_URL"
MODELS_API_KEY_ENV = "IQRADAR_MODELS_API_KEY"
#: Reasoning effort (推理强度) for new evaluations, set beside the inference pair.
INFERENCE_EFFORT_ENV = "IQRADAR_INFERENCE_EFFORT"

#: Allowed reasoning-effort values (matches deepswe.api ALLOWED_EFFORTS).
ALLOWED_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "max")

#: Payload/environment field order used everywhere (API, JSON file, env sync).
SETTING_FIELDS: tuple[str, ...] = (
    "models_base_url",
    "models_api_key",
    "inference_base_url",
    "inference_api_key",
    "inference_effort",
)

_FIELD_ENV_NAMES: dict[str, str] = {
    "models_base_url": MODELS_BASE_URL_ENV,
    "models_api_key": MODELS_API_KEY_ENV,
    "inference_base_url": INFERENCE_BASE_URL_ENV,
    "inference_api_key": INFERENCE_API_KEY_ENV,
    "inference_effort": INFERENCE_EFFORT_ENV,
}

MAX_URL_LENGTH = 2048
MAX_API_KEY_LENGTH = 1024


class GatewaySettingsError(ValueError):
    """Raised when a settings payload fails validation."""


class GatewaySettings:
    """File-backed overrides for the two gateway endpoint pairs.

    The store is read-safe under concurrency (guarded by a lock) and applies
    its values to ``os.environ`` on construction and after every save, so
    existing env-driven consumers see updates without a restart.
    """

    def __init__(self, path: Path, environ: dict[str, str] | None = None) -> None:
        self._path = path
        self._environ = os.environ if environ is None else environ
        self._lock = RLock()
        # Baseline = the values visible before any override is applied (i.e.
        # what ``.env`` provided). Clearing a stored value restores these.
        self._baseline: dict[str, str] = {
            env_name: self._environ.get(env_name, "") for env_name in _FIELD_ENV_NAMES.values()
        }
        self.apply_env()

    # --- persistence --------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, str]:
        """Return the stored overrides (empty strings when not overridden).

        Carries the file's ``updated_at`` timestamp through when present.
        """
        with self._lock:
            data: dict[str, str] = {field: "" for field in SETTING_FIELDS}
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = None
            if isinstance(raw, dict):
                for field in SETTING_FIELDS:
                    value = raw.get(field)
                    if isinstance(value, str):
                        data[field] = value.strip()
                updated_at = raw.get("updated_at")
                if isinstance(updated_at, str):
                    data["updated_at"] = updated_at
            return data

    def save(self, values: Mapping[str, str]) -> dict[str, str]:
        """Validate, persist, and apply a settings payload. Returns the state."""
        cleaned = validate_settings(values)
        with self._lock:
            payload = {field: cleaned[field] for field in SETTING_FIELDS}
            payload["updated_at"] = _utc_now_iso()
            atomic_write_text(
                self._path,
                json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
            )
        self.apply_env()
        return self.load()

    # --- environment sync ---------------------------------------------------

    def apply_env(self) -> None:
        """Mirror stored overrides into the process environment.

        A non-empty stored value replaces the environment variable; an empty
        one restores the baseline captured at construction time (or removes
        the variable when there was no baseline value either).
        """
        stored = self.load()
        with self._lock:
            for field in SETTING_FIELDS:
                env_name = _FIELD_ENV_NAMES[field]
                value = stored.get(field, "") or self._baseline.get(env_name, "")
                if value:
                    self._environ[env_name] = value
                else:
                    self._environ.pop(env_name, None)

    # --- effective values ---------------------------------------------------

    def models_base_url(self) -> str:
        """Effective base URL for ``GET /v1/models`` ("" when unset)."""
        with self._lock:
            override = self._environ.get(MODELS_BASE_URL_ENV, "").strip()
            if override:
                return override
            return self._environ.get(INFERENCE_BASE_URL_ENV, "").strip()

    def models_api_key(self) -> str:
        """Effective bearer key for ``GET /v1/models`` ("" when unset)."""
        with self._lock:
            override = self._environ.get(MODELS_API_KEY_ENV, "").strip()
            if override:
                return override
            return self._environ.get(INFERENCE_API_KEY_ENV, "").strip()

    def inference_base_url(self) -> str:
        """Effective base URL for ``POST /v1/chat/completions`` ("" when unset)."""
        with self._lock:
            return self._environ.get(INFERENCE_BASE_URL_ENV, "").strip()

    def inference_api_key(self) -> str:
        """Effective bearer key for ``POST /v1/chat/completions`` ("" when unset)."""
        with self._lock:
            return self._environ.get(INFERENCE_API_KEY_ENV, "").strip()

    def inference_effort(self) -> str:
        """Effective reasoning effort for new evaluations ("" = caller default)."""
        with self._lock:
            return self._environ.get(INFERENCE_EFFORT_ENV, "").strip()

    def as_dict(self) -> dict[str, str]:
        """Effective settings payload for the API/UI (values, not masks)."""
        stored = self.load()
        return {
            "models_base_url": self.models_base_url(),
            "models_api_key": self.models_api_key(),
            "inference_base_url": self.inference_base_url(),
            "inference_api_key": self.inference_api_key(),
            "inference_effort": self.inference_effort(),
            "updated_at": _stored_updated_at(stored),
        }


def validate_settings(values: Mapping[str, object]) -> dict[str, str]:
    """Normalize and validate a settings payload; returns the cleaned fields.

    Unknown fields are rejected so typos never silently no-op; URL fields must
    look like ``http(s)://...`` when non-empty; keys may be any non-empty
    string (blank = fall back to the ``.env`` baseline). ``inference_effort``
    is optional (missing → "") and must be one of :data:`ALLOWED_EFFORTS`
    when non-empty.
    """
    unknown = sorted(set(values) - set(SETTING_FIELDS))
    if unknown:
        raise GatewaySettingsError(f"unknown fields: {', '.join(unknown)}")
    cleaned: dict[str, str] = {}
    for field in SETTING_FIELDS:
        if field not in values:
            if field == "inference_effort":
                cleaned[field] = ""  # optional: older clients may omit it
                continue
            raise GatewaySettingsError(f"missing field: {field}")
        value = values[field]
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise GatewaySettingsError(f"{field} must be a string")
        value = value.strip()
        limit = MAX_URL_LENGTH if field.endswith("_base_url") else MAX_API_KEY_LENGTH
        if len(value) > limit:
            raise GatewaySettingsError(f"{field} is too long (max {limit} characters)")
        if field.endswith("_base_url") and value:
            lowered = value.lower()
            if not (lowered.startswith("http://") or lowered.startswith("https://")):
                raise GatewaySettingsError(f"{field} must start with http:// or https://")
        if field == "inference_effort" and value and value not in ALLOWED_EFFORTS:
            raise GatewaySettingsError(
                f"inference_effort must be one of {', '.join(ALLOWED_EFFORTS)}"
            )
        cleaned[field] = value
    return cleaned


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _stored_updated_at(stored: Mapping[str, str]) -> str:
    updated_at = stored.get("updated_at", "")
    return updated_at if isinstance(updated_at, str) else ""
