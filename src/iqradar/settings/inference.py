"""Single source of truth for eval-time model invocation.

The test-page **模型调用** pair (``POST /v1/chat/completions``) is the only
place IQRadar reads a URL and API key for scoring a model. :class:`GatewaySettings`
already folds blank UI fields back to the ``GATEWAY_*`` .env baseline.

Every backend, subprocess, and container inherits from
:class:`InferenceEndpoint` — in-process via this module, in Docker via the
OpenAI-compatible env vars :meth:`InferenceEndpoint.agent_env` projects
(``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` / ``MSWEA_API_KEY``). Benchmark
``.env`` ``OPENAI_*`` files are not a competing invocation source.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from iqradar.shared.container_net import container_reachable_url

MISSING_INFERENCE_ENDPOINT = (
    "模型调用 endpoint is not configured (set base URL on the test page)"
)


def gateway_setting(gateway_settings: object | None, name: str) -> str:
    """Read one GatewaySettings getter. Duck-typed; missing store yields \"\"."""
    if gateway_settings is None:
        return ""
    getter = getattr(gateway_settings, name, None)
    if not callable(getter):
        return ""
    try:
        value = getter()
    except Exception:  # pragma: no cover - defensive
        return ""
    return value.strip() if isinstance(value, str) else ""


@dataclass(frozen=True)
class InferenceEndpoint:
    """Resolved 模型调用 URL + bearer key."""

    base_url: str
    api_key: str

    def chat_completions_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def auth_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def for_container(self) -> InferenceEndpoint:
        """Same credentials with a Docker-reachable host."""
        return InferenceEndpoint(
            base_url=container_reachable_url(self.base_url),
            api_key=self.api_key,
        )

    def agent_env(self) -> dict[str, str]:
        """Env vars mini-swe-agent / litellm / pier read inside agents."""
        env: dict[str, str] = {}
        if self.base_url:
            env["OPENAI_BASE_URL"] = self.base_url
        if self.api_key:
            env["OPENAI_API_KEY"] = self.api_key
            env["MSWEA_API_KEY"] = self.api_key
        return env

    def docker_env_args(self) -> list[str]:
        """``docker run -e KEY=VALUE`` fragments for :meth:`agent_env`."""
        args: list[str] = []
        for key, value in self.agent_env().items():
            args.extend(["-e", f"{key}={value}"])
        return args


def resolve_inference_endpoint(
    gateway_settings: object | None = None,
) -> InferenceEndpoint:
    """Resolve the 模型调用 pair.

    With a settings store, use its effective inference URL/key (UI override,
    else the ``GATEWAY_*`` baseline the store captured). Without a store
    (CLI), read ``GATEWAY_BASE_URL`` / ``GATEWAY_API_KEY`` from the process
    environment — the same baseline the UI uses when fields are blank.
    """
    base_url = gateway_setting(gateway_settings, "inference_base_url")
    api_key = gateway_setting(gateway_settings, "inference_api_key")
    if gateway_settings is None:
        base_url = base_url or os.environ.get("GATEWAY_BASE_URL", "").strip()
        api_key = api_key or os.environ.get("GATEWAY_API_KEY", "").strip()
    return InferenceEndpoint(base_url=base_url, api_key=api_key)
