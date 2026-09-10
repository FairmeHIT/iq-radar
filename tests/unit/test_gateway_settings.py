from __future__ import annotations

import json
from pathlib import Path

import pytest

from iqradar.settings.gateway import (
    GatewaySettings,
    GatewaySettingsError,
    validate_settings,
)


def test_blank_store_falls_back_to_baseline_environment(tmp_path: Path) -> None:
    env = {
        "GATEWAY_BASE_URL": "http://env.example/v1",
        "GATEWAY_API_KEY": "sk-env",
    }
    settings = GatewaySettings(tmp_path / "gateway.json", environ=env)

    assert settings.inference_base_url() == "http://env.example/v1"
    assert settings.inference_api_key() == "sk-env"
    # 模型列表对缺省回退到调用端点（旧行为：单一网关同时服务两者）。
    assert settings.models_base_url() == "http://env.example/v1"
    assert settings.models_api_key() == "sk-env"


def test_save_overrides_environment_immediately(tmp_path: Path) -> None:
    env: dict[str, str] = {"GATEWAY_BASE_URL": "http://env.example/v1"}
    settings = GatewaySettings(tmp_path / "gateway.json", environ=env)

    settings.save(
        {
            "models_base_url": "http://models.example/v1",
            "models_api_key": "sk-models",
            "inference_base_url": "http://chat.example/v1",
            "inference_api_key": "sk-chat",
        }
    )

    assert env["IQRADAR_MODELS_BASE_URL"] == "http://models.example/v1"
    assert env["IQRADAR_MODELS_API_KEY"] == "sk-models"
    assert env["GATEWAY_BASE_URL"] == "http://chat.example/v1"
    assert env["GATEWAY_API_KEY"] == "sk-chat"
    assert settings.models_base_url() == "http://models.example/v1"
    assert settings.inference_base_url() == "http://chat.example/v1"


def test_persisted_settings_apply_on_new_instance(tmp_path: Path) -> None:
    path = tmp_path / "gateway.json"
    env: dict[str, str] = {}
    GatewaySettings(path, environ=env).save(
        {
            "models_base_url": "http://models.example/v1",
            "models_api_key": "",
            "inference_base_url": "",
            "inference_api_key": "sk-chat",
        }
    )

    # 新进程/新实例：磁盘上的覆盖自动生效
    fresh_env: dict[str, str] = {}
    fresh = GatewaySettings(path, environ=fresh_env)
    assert fresh.models_base_url() == "http://models.example/v1"
    # 模型列表 key 未配置 → 回退到调用端点 key（单一网关语义）
    assert fresh.models_api_key() == "sk-chat"
    assert fresh.inference_base_url() == ""
    assert fresh.inference_api_key() == "sk-chat"
    assert fresh_env["GATEWAY_API_KEY"] == "sk-chat"


def test_clearing_stored_value_restores_baseline(tmp_path: Path) -> None:
    path = tmp_path / "gateway.json"
    env = {"GATEWAY_BASE_URL": "http://env.example/v1", "GATEWAY_API_KEY": "sk-env"}
    settings = GatewaySettings(path, environ=env)
    settings.save(
        {
            "models_base_url": "",
            "models_api_key": "",
            "inference_base_url": "http://chat.example/v1",
            "inference_api_key": "sk-chat",
        }
    )
    assert env["GATEWAY_BASE_URL"] == "http://chat.example/v1"

    settings.save(
        {
            "models_base_url": "",
            "models_api_key": "",
            "inference_base_url": "",
            "inference_api_key": "",
        }
    )
    # 清空后恢复构造时捕获的 .env 基线
    assert env["GATEWAY_BASE_URL"] == "http://env.example/v1"
    assert env["GATEWAY_API_KEY"] == "sk-env"


def test_saved_file_is_json_with_updated_at_and_0600(tmp_path: Path) -> None:
    path = tmp_path / "settings" / "gateway.json"
    settings = GatewaySettings(path, environ={})
    settings.save(
        {
            "models_base_url": "http://models.example/v1",
            "models_api_key": "sk-models",
            "inference_base_url": "",
            "inference_api_key": "",
        }
    )

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["models_base_url"] == "http://models.example/v1"
    assert raw["models_api_key"] == "sk-models"
    assert raw["updated_at"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_as_dict_returns_effective_values(tmp_path: Path) -> None:
    path = tmp_path / "gateway.json"
    env = {
        "GATEWAY_BASE_URL": "http://env.example/v1",
        "GATEWAY_API_KEY": "sk-env",
    }
    settings = GatewaySettings(path, environ=env)
    settings.save(
        {
            "models_base_url": "",
            "models_api_key": "sk-models",
            "inference_base_url": "",
            "inference_api_key": "",
        }
    )

    payload = settings.as_dict()
    assert payload["models_base_url"] == "http://env.example/v1"  # 回退
    assert payload["models_api_key"] == "sk-models"  # 覆盖
    assert payload["inference_base_url"] == "http://env.example/v1"
    assert payload["inference_api_key"] == "sk-env"
    assert payload["updated_at"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("models_base_url", "ftp://models.example/v1"),
        ("inference_base_url", "models.example/v1"),
        ("models_api_key", "x" * 2000),
    ],
)
def test_validate_settings_rejects_bad_values(
    tmp_path: Path, field: str, value: str
) -> None:
    values = {name: "" for name in (
        "models_base_url",
        "models_api_key",
        "inference_base_url",
        "inference_api_key",
    )}
    values[field] = value
    with pytest.raises(GatewaySettingsError):
        validate_settings(values)


def test_validate_settings_rejects_unknown_fields() -> None:
    with pytest.raises(GatewaySettingsError):
        validate_settings({"gateway_base_url": "http://x/v1"})


def test_validate_settings_strips_whitespace() -> None:
    cleaned = validate_settings(
        {
            "models_base_url": "  http://models.example/v1  ",
            "models_api_key": "  sk-models  ",
            "inference_base_url": None,
            "inference_api_key": "",
        }
    )
    assert cleaned["models_base_url"] == "http://models.example/v1"
    assert cleaned["models_api_key"] == "sk-models"
    assert cleaned["inference_base_url"] == ""
