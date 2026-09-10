"""UI 「模型调用」is the only eval-time source for URL/key."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iqradar.settings.inference import (
    InferenceEndpoint,
    MISSING_INFERENCE_ENDPOINT,
    resolve_inference_endpoint,
)
from iqradar.settings.gateway import GatewaySettings


def test_resolve_reads_ui_inference_pair(tmp_path) -> None:
    path = tmp_path / "gateway.json"
    env: dict[str, str] = {}
    settings = GatewaySettings(path, environ=env)
    settings.save(
        {
            "models_base_url": "http://models.example/v1",
            "models_api_key": "sk-models",
            "inference_base_url": "http://chat.example/v1",
            "inference_api_key": "sk-chat",
        }
    )

    endpoint = resolve_inference_endpoint(settings)

    assert endpoint.base_url == "http://chat.example/v1"
    assert endpoint.api_key == "sk-chat"
    assert endpoint.chat_completions_url() == "http://chat.example/v1/chat/completions"
    assert endpoint.auth_headers()["Authorization"] == "Bearer sk-chat"


def test_resolve_does_not_use_models_list_pair(tmp_path) -> None:
    path = tmp_path / "gateway.json"
    env: dict[str, str] = {
        "GATEWAY_BASE_URL": "http://env-chat.example/v1",
        "GATEWAY_API_KEY": "sk-env",
    }
    settings = GatewaySettings(path, environ=env)
    settings.save(
        {
            "models_base_url": "http://models.example/v1",
            "models_api_key": "sk-models",
            "inference_base_url": "",
            "inference_api_key": "",
        }
    )

    endpoint = resolve_inference_endpoint(settings)

    assert endpoint.base_url == "http://env-chat.example/v1"
    assert endpoint.api_key == "sk-env"


def test_agent_env_projects_openai_and_mswea_keys() -> None:
    env = InferenceEndpoint(
        base_url="http://chat.example/v1", api_key="sk-ui"
    ).agent_env()

    assert env == {
        "OPENAI_BASE_URL": "http://chat.example/v1",
        "OPENAI_API_KEY": "sk-ui",
        "MSWEA_API_KEY": "sk-ui",
    }


def test_agent_env_empty_when_unconfigured() -> None:
    assert InferenceEndpoint(base_url="", api_key="").agent_env() == {}


def test_docker_env_args_are_open_ai_compat_flags() -> None:
    args = InferenceEndpoint(
        base_url="http://chat.example/v1", api_key="sk-ui"
    ).docker_env_args()

    assert args == [
        "-e",
        "OPENAI_BASE_URL=http://chat.example/v1",
        "-e",
        "OPENAI_API_KEY=sk-ui",
        "-e",
        "MSWEA_API_KEY=sk-ui",
    ]


def test_for_container_rewrites_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    from iqradar.shared import container_net

    monkeypatch.setattr(container_net, "_is_docker_desktop", lambda: True)
    monkeypatch.setattr(container_net, "wsl_eth0_ip", lambda: "172.23.175.33")

    hosted = InferenceEndpoint(
        base_url="http://127.0.0.1:8080/v1", api_key="sk-ui"
    ).for_container()

    assert hosted.base_url == "http://172.23.175.33:8080/v1"
    assert hosted.api_key == "sk-ui"


def test_resolve_without_store_uses_gateway_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATEWAY_BASE_URL", "http://cli.example/v1")
    monkeypatch.setenv("GATEWAY_API_KEY", "sk-cli")

    endpoint = resolve_inference_endpoint(None)

    assert endpoint.base_url == "http://cli.example/v1"
    assert endpoint.api_key == "sk-cli"


def test_resolve_ignores_benchmark_env_file_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://checkout.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-checkout")

    endpoint = resolve_inference_endpoint(None)

    assert endpoint.base_url == ""
    assert endpoint.api_key == ""


def test_missing_endpoint_message() -> None:
    assert "模型调用" in MISSING_INFERENCE_ENDPOINT


def test_duck_typed_gateway_object() -> None:
    gateway = SimpleNamespace(
        inference_base_url=lambda: " http://duck.example/v1 ",
        inference_api_key=lambda: " sk-duck ",
    )

    endpoint = resolve_inference_endpoint(gateway)

    assert endpoint.base_url == "http://duck.example/v1"
    assert endpoint.api_key == "sk-duck"
