"""REST endpoints for the test page's runtime gateway configuration.

Mounted under ``/api/gateway-settings``:

- ``GET``  — effective settings (form pre-fill);
- ``POST`` — validate + persist + apply immediately (no restart, no code edit);
- ``POST /test-models`` — probe ``GET <base>/models`` with the given pair and
  return the discovered model ids;
- ``POST /test-chat``   — probe ``POST <base>/chat/completions`` with a short
  prompt and return the model reply.

The probe endpoints accept explicit ``base_url``/``api_key`` values so the
user can verify a pair *before* saving it; when omitted they fall back to the
saved configuration.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from flask import Blueprint, request

from iqradar.ingest.redact import redact_secret_text
from iqradar.settings.gateway import (
    SETTING_FIELDS,
    GatewaySettings,
    GatewaySettingsError,
    validate_settings,
)
from iqradar.settings.inference import InferenceEndpoint
from iqradar.shared.api_responses import error, ok

PROBE_TIMEOUT_SEC = 15.0
CHAT_TIMEOUT_SEC = 30.0
MAX_LISTED_MODEL_IDS = 100
DEFAULT_CHAT_PROMPT = "Hello!"


def create_gateway_blueprint(gateway_settings: GatewaySettings) -> Blueprint:
    blueprint = Blueprint("gateway_settings", __name__)

    @blueprint.get("/api/gateway-settings")
    def get_settings():
        return ok(gateway_settings.as_dict())

    @blueprint.post("/api/gateway-settings")
    def save_settings():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error("JSON settings payload is required", 400)
        try:
            cleaned = validate_settings(payload)
            gateway_settings.save(cleaned)
        except GatewaySettingsError as caught:
            return error(str(caught), 400)
        return ok(gateway_settings.as_dict())

    @blueprint.post("/api/gateway-settings/test-models")
    def test_models():
        payload = request.get_json(silent=True)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return error("JSON payload is required", 400)
        base_url = _text(payload.get("base_url")) or gateway_settings.models_base_url()
        api_key = _text(payload.get("api_key")) or gateway_settings.models_api_key()
        if not base_url:
            return error("base_url is required", 400)
        started = time.monotonic()
        model_ids, probe_error = _fetch_model_ids(base_url, api_key)
        return ok(
            {
                "ok": probe_error is None,
                "base_url": base_url,
                "model_count": len(model_ids),
                "model_ids": model_ids[:MAX_LISTED_MODEL_IDS],
                "latency_ms": round((time.monotonic() - started) * 1000),
                "error": probe_error,
            }
        )

    @blueprint.post("/api/gateway-settings/test-chat")
    def test_chat():
        payload = request.get_json(silent=True)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return error("JSON payload is required", 400)
        model = _text(payload.get("model"))
        if not model:
            return error("model is required", 400)
        base_url = _text(payload.get("base_url")) or gateway_settings.inference_base_url()
        api_key = _text(payload.get("api_key")) or gateway_settings.inference_api_key()
        if not base_url:
            return error("base_url is required", 400)
        prompt = _text(payload.get("prompt")) or DEFAULT_CHAT_PROMPT
        started = time.monotonic()
        content, chat_error = _post_chat_completion(
            base_url, api_key, model, prompt, timeout_sec=CHAT_TIMEOUT_SEC
        )
        return ok(
            {
                "ok": chat_error is None,
                "base_url": base_url,
                "model": model,
                "content": content,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "error": chat_error,
            }
        )

    return blueprint


# ---------------------------------------------------------------------------
# Probe helpers (stdlib urllib, mirroring deepswe.api discovery behaviour)
# ---------------------------------------------------------------------------

def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _fetch_model_ids(base_url: str, api_key: str) -> tuple[list[str], str | None]:
    """GET ``<base_url>/models``; returns ``(model_ids, error)``."""
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        base_url.rstrip("/") + "/models", headers=headers, method="GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_SEC) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as caught:
        detail = caught.read().decode("utf-8", errors="replace")[:300]
        return [], _redacted(f"HTTP {caught.code}: {detail}", api_key)
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as caught:
        return [], _redacted(f"{caught.__class__.__name__}: {caught}", api_key)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return [], "unexpected response shape (missing data[])"
    ids = [
        str(item["id"]).strip()
        for item in data
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()
    ]
    return list(dict.fromkeys(ids)), None


def _post_chat_completion(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    *,
    timeout_sec: float,
) -> tuple[str | None, str | None]:
    """POST one minimal chat completion; returns ``(content, error)``."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    endpoint = InferenceEndpoint(base_url, api_key)
    request = urllib.request.Request(
        endpoint.chat_completions_url(),
        data=payload,
        headers=endpoint.auth_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as caught:
        detail = caught.read().decode("utf-8", errors="replace")[:300]
        return None, _redacted(f"HTTP {caught.code}: {detail}", api_key)
    except (OSError, urllib.error.URLError) as caught:
        return None, _redacted(f"{caught.__class__.__name__}: {caught}", api_key)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, "invalid JSON in response"
    if not isinstance(data, dict):
        return None, "unexpected response shape"
    choices = data.get("choices")
    content = ""
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            content = str(message.get("content") or message.get("reasoning_content") or "")
    return content, None


def _redacted(message: str, api_key: str) -> str:
    return redact_secret_text(message, (api_key,)) if api_key else message


__all__ = ["create_gateway_blueprint", "SETTING_FIELDS"]
