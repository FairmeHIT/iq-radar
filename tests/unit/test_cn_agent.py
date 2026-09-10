"""CnMiniSweAgent must forward UI 模型调用 credentials into the container.

Pier's MiniSweAgent.run() copies only MSWEA_API_KEY when that variable is
set. mini-swe-agent's litellm OpenAI client requires OPENAI_API_KEY, so the
adapter has to put it on the process env that docker exec receives.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

DEEP_SWE_DIR = Path(__file__).resolve().parents[2] / "checkouts" / "deep-swe"


def _install_pier_stubs() -> None:
    def _pkg(name: str) -> types.ModuleType:
        module = types.ModuleType(name)
        module.__path__ = []  # type: ignore[attr-defined]
        sys.modules[name] = module
        return module

    _pkg("pier")
    _pkg("pier.agents")
    _pkg("pier.agents.installed")
    _pkg("pier.models")
    _pkg("pier.models.agent")

    mini = types.ModuleType("pier.agents.installed.mini_swe_agent")

    class MiniSweAgent:
        def __init__(self, *args, **kwargs) -> None:
            self._extra_env: dict[str, str] = {}
            self._resolved_env_vars: dict[str, str] = {}

        def build_process_env(
            self,
            base: dict[str, str | None] | None = None,
            *,
            include_resolved_env: bool = True,
        ) -> dict[str, str]:
            env = {key: value for key, value in (base or {}).items() if value is not None}
            env.update(self._extra_env)
            if include_resolved_env:
                env.update(self._resolved_env_vars)
            return env

        def _get_env(self, key: str) -> str | None:
            if key in self._extra_env:
                return self._extra_env[key]
            return os.environ.get(key)

    mini.MiniSweAgent = MiniSweAgent
    sys.modules["pier.agents.installed.mini_swe_agent"] = mini

    install = types.ModuleType("pier.models.agent.install")

    class AgentInstallSpec:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class InstallStep:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    install.AgentInstallSpec = AgentInstallSpec
    install.InstallStep = InstallStep
    sys.modules["pier.models.agent.install"] = install


@pytest.fixture
def cn_agent_module():
    if not (DEEP_SWE_DIR / "cn_agent.py").is_file():
        pytest.skip("deep-swe checkout cn_agent.py is not present")
    _install_pier_stubs()
    sys.path.insert(0, str(DEEP_SWE_DIR))
    sys.modules.pop("cn_agent", None)
    import cn_agent

    return cn_agent


def test_openai_compat_env_copies_openai_key_when_mswea_is_also_set(cn_agent_module) -> None:
    env = cn_agent_module.openai_compat_container_env(
        {"LITELLM_LOCAL_MODEL_COST_MAP": "true"},
        openai_api_key="sk-ui",
        mswea_api_key="sk-ui",
    )
    assert env["OPENAI_API_KEY"] == "sk-ui"
    assert env["MSWEA_API_KEY"] == "sk-ui"


def test_openai_compat_env_uses_mswea_key_as_openai_fallback(cn_agent_module) -> None:
    env = cn_agent_module.openai_compat_container_env(
        {},
        openai_api_key=None,
        mswea_api_key="sk-mswea-only",
    )
    assert env["OPENAI_API_KEY"] == "sk-mswea-only"
    assert env["MSWEA_API_KEY"] == "sk-mswea-only"


def test_build_process_env_forwards_host_openai_key(
    cn_agent_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-ui")
    monkeypatch.setenv("MSWEA_API_KEY", "sk-from-ui")
    agent = cn_agent_module.CnMiniSweAgent()
    env = agent.build_process_env(
        {
            "LITELLM_LOCAL_MODEL_COST_MAP": "true",
            "MSWEA_CONFIGURED": "true",
        }
    )
    assert env["OPENAI_API_KEY"] == "sk-from-ui"
    assert env["MSWEA_API_KEY"] == "sk-from-ui"
    assert env["LITELLM_LOCAL_MODEL_COST_MAP"] == "true"
