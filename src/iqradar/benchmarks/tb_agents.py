"""Terminal-Bench agent compatibility shims.

The stock ``terminal-bench==0.2.x`` :class:`MiniSweAgent` parses the model
name with ``model_name.split("/")`` and therefore requires exactly
``provider/model``.  iq_radar's gateway model ids already carry a provider
prefix (e.g. ``gateway/deepseek-v4-flash``), so the LiteLLM-style name we
must hand to the agent is ``openai/gateway/deepseek-v4-flash`` — two
slashes — and the stock agent crashes at construction with
``ValueError: too many values to unpack (expected 2)``.

:class:`MiniSweAgentCompat` is a drop-in replacement that splits on the first
slash only and otherwise behaves identically to the stock agent.  It is wired
up through terminal-bench's ``--agent-import-path`` mechanism (see
``configs/benchmark.yaml``), so no changes to the installed harness are
required.

This module is imported inside the ``tb`` process (via ``PYTHONPATH``) and
must stay free of iq_radar's heavy dependencies.
"""

from __future__ import annotations

import os
import shlex

from terminal_bench.agents.installed_agents.abstract_installed_agent import (
    AbstractInstalledAgent,
)
from terminal_bench.agents.installed_agents.mini_swe_agent.mini_swe_agent import (
    MiniSweAgent,
)

from iqradar.benchmarks.mini_trajectory import MINI_TRAJECTORY_FILENAME

__all__ = ["MiniSweAgentCompat", "provider_of"]


def provider_of(model_name: str) -> str:
    """Return the provider prefix of a ``provider/model`` name.

    The stock agent uses ``model_name.split("/")`` which fails when the model
    part itself contains a slash (``openai/gateway/deepseek-v4-flash``);
    splitting on the first slash only keeps ``openai`` as the provider.
    """
    return model_name.split("/", 1)[0]


class MiniSweAgentCompat(MiniSweAgent):
    """MiniSweAgent variant that tolerates nested slashes in the model name.

    ``MiniSweAgent.__init__`` crashes on names such as
    ``openai/gateway/deepseek-v4-flash``; this class skips that constructor
    and initializes the base class directly, keeping identical environment,
    setup-script and command behavior otherwise.  With ``MSWEA_API_KEY`` set,
    the provider prefix is only informational, so ``openai/`` (the LiteLLM
    convention for an OpenAI-compatible endpoint) is what we expect here.
    """

    def __init__(self, model_name: str, *args, **kwargs) -> None:
        AbstractInstalledAgent.__init__(self, *args, **kwargs)
        self._model_name = model_name
        self._provider = provider_of(model_name)

    @property
    def _env(self) -> dict[str, str]:
        """Container env for the agent run.

        The stock ``MiniSweAgent._env`` forwards only ``MSWEA_API_KEY`` into
        the container (the ``OPENAI_API_KEY`` branch is unreachable whenever
        ``MSWEA_API_KEY`` is set, which is always the case here because
        ``InferenceEndpoint.agent_env()`` exports both). mini-swe-agent 2.x +
        litellm then builds an OpenAI client that requires ``OPENAI_API_KEY``
        and dies with ``InternalServerError: Missing credentials`` — every
        task on every model scored 0 that way (see run 66d8790f,
        hello-world). It also never forwards ``OPENAI_BASE_URL``, so the
        agent would default to api.openai.com instead of the gateway even
        with a key. Mirror cn_agent.openai_compat_container_env: export the
        key under both names plus the container-reachable gateway URL.
        """
        env = super()._env
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("MSWEA_API_KEY") or ""
        if key:
            env["OPENAI_API_KEY"] = key
            env.setdefault("MSWEA_API_KEY", key)
        base_url = os.environ.get("OPENAI_BASE_URL", "")
        if base_url:
            env["OPENAI_BASE_URL"] = base_url
        return env

    def _run_agent_commands(self, instruction: str) -> list:
        """Stock mini command plus ``-o`` trajectory persistence.

        The upstream harness hardcodes ``AgentResult(total_input_tokens=0)``
        for installed agents, so usage must come from the agent itself: mini
        writes its trajectory into the container's ``/agent-logs`` mount
        (bind-mounted to the trial's ``agent-logs/`` on the host), where
        ``terminal_bench.py`` picks it up at import time for agent_steps and
        cached-input tokens. A stale trajectory is removed first so a re-run
        never reports the previous attempt's usage.
        """
        commands = super()._run_agent_commands(instruction)
        trajectory = (
            f"{self.CONTAINER_AGENT_LOGS_PATH}/{MINI_TRAJECTORY_FILENAME}"
        )
        command = commands[0]
        command.command = (
            f"rm -f {shlex.quote(trajectory)} ; "
            f"{command.command} -o {shlex.quote(trajectory)}"
        )
        return commands
