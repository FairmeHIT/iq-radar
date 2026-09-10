from __future__ import annotations

import json
import ipaddress
import shutil
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated, Optional

import typer

from iqradar.config.loader import (
    anchor_benchmark_config_set,
    load_benchmark_config,
    load_model_config,
)
from iqradar.config.schema import ConfigEnvironmentError
from iqradar.ingest.redact import redact_secret_text
from iqradar.settings.inference import resolve_inference_endpoint

app = typer.Typer(no_args_is_help=True)
DEFAULT_GATEWAY_BASE_URL = "http://localhost:8080/v1"
DEFAULT_GATEWAY_CHAT_COMPLETIONS_URL = f"{DEFAULT_GATEWAY_BASE_URL}/chat/completions"
DEFAULT_GATEWAY_MODEL_NAME = "gateway/deepseek-v4-flash"


@app.callback()
def main(
    ctx: typer.Context,
    env_file: Annotated[Optional[Path], typer.Option("--env-file")] = Path(".env"),
) -> None:
    os.umask(0o077)
    if ctx.resilient_parsing:
        return
    if env_file is not None:
        _load_env_file(env_file)
    os.environ.setdefault("GATEWAY_BASE_URL", DEFAULT_GATEWAY_BASE_URL)
    os.environ.setdefault("GATEWAY_MODEL_NAME", DEFAULT_GATEWAY_MODEL_NAME)


@app.command()
def doctor(
    models: Annotated[Path, typer.Option("--models")] = Path("configs/models.yaml"),
    benchmark: Annotated[Path, typer.Option("--benchmark")] = Path("configs/benchmark.yaml"),
    skip_api_check: Annotated[bool, typer.Option("--skip-api-check")] = False,
) -> None:
    try:
        model_config = load_model_config(models)
        benchmark_config = anchor_benchmark_config_set(
            load_benchmark_config(benchmark),
            _benchmark_project_root(benchmark),
        )
        model_config.require_env()
    except (ConfigEnvironmentError, ValueError, OSError) as error:
        typer.echo(f"ERROR {error}")
        raise typer.Exit(1) from error

    typer.echo("OK model config")
    typer.echo("OK benchmark config")
    typer.echo("OK environment variables")
    typer.echo("OK docker" if shutil.which("docker") else "WARN docker not found")
    for name, config in benchmark_config.benchmarks.items():
        if config.tasks_path.exists():
            typer.echo(f"OK {name} tasks path")
        else:
            typer.echo(f"WARN {name} tasks path missing")
        env_file = config.resolved_env_file()
        if env_file.is_file():
            typer.echo(f"OK {name} env file")
        elif config.type == "api-eval":
            typer.echo(f"OK {name} uses gateway settings")
        else:
            typer.echo(f"WARN {name} env file missing: {env_file}")
    if skip_api_check:
        typer.echo("SKIP API connectivity")
    else:
        typer.echo("SKIP API connectivity: not implemented in local doctor")


@app.command()
def probe(
    url: Annotated[
        str,
        typer.Option("--url"),
    ] = DEFAULT_GATEWAY_CHAT_COMPLETIONS_URL,
    api_key_env: Annotated[str, typer.Option("--api-key-env")] = "GATEWAY_API_KEY",
    model: Annotated[Optional[str], typer.Option("--model")] = None,
    prompt: Annotated[str, typer.Option("--prompt")] = "你能做什么",
    timeout_sec: Annotated[int, typer.Option("--timeout-sec")] = 30,
    stream: Annotated[bool, typer.Option("--stream/--no-stream")] = False,
    portkey_provider: Annotated[str, typer.Option("--portkey-provider")] = "",
) -> None:
    if url == DEFAULT_GATEWAY_CHAT_COMPLETIONS_URL:
        inferred = resolve_inference_endpoint()
        url = (
            inferred.chat_completions_url()
            if inferred.base_url
            else os.environ.get("GATEWAY_BASE_URL", DEFAULT_GATEWAY_BASE_URL).rstrip("/")
            + "/chat/completions"
        )
    model = model or os.environ.get("GATEWAY_MODEL_NAME", DEFAULT_GATEWAY_MODEL_NAME)
    api_key = os.environ.get(api_key_env, "") if api_key_env else ""

    body = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if stream:
        body["stream"] = True

    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if portkey_provider:
        headers["x-portkey-provider"] = portkey_provider
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            if stream:
                typer.echo(f"OK {model}")
                for line in response:
                    line = line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data = line.removeprefix("data: ").strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                typer.echo(content, nl=False)
                    except json.JSONDecodeError:
                        continue
                typer.echo()
            else:
                body = response.read().decode("utf-8", errors="replace")
                typer.echo(f"OK {model}")
                typer.echo(_compact_json(body, (api_key, url)))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        typer.echo(f"ERROR HTTP {error.code}: {_compact_json(body, (api_key, url))}")
        raise typer.Exit(1) from error
    except OSError as error:
        typer.echo(f"ERROR request failed: {error.__class__.__name__}")
        raise typer.Exit(1) from error


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8080,
    data_path: Annotated[Path, typer.Option("--data-path")] = Path("data/aggregate/radar.json"),
    raw_runs_path: Annotated[Path, typer.Option("--raw-runs-path")] = Path("data/raw/runs"),
    reporting_path: Annotated[Path, typer.Option("--reporting-path")] = Path("data/reporting"),
    deepswe_runs_path: Annotated[Path, typer.Option("--deepswe-runs-path")] = Path("data/deepswe/runs"),
    models: Annotated[Path, typer.Option("--models")] = Path("configs/models.yaml"),
    benchmark: Annotated[Path, typer.Option("--benchmark")] = Path("configs/benchmark.yaml"),
    prices: Annotated[Path, typer.Option("--prices")] = Path("configs/prices.example.yaml"),
) -> None:
    if not _is_loopback_host(host):
        typer.echo("ERROR serve host must be a loopback address")
        raise typer.Exit(1)
    try:
        from iqradar.api.app import create_app
    except ModuleNotFoundError as error:
        typer.echo("ERROR Flask API is not implemented yet")
        raise typer.Exit(1) from error
    # Host-side CN mirror proxy for in-container apt (best-effort daemon;
    # benchmark backends fall back to per-suite mounts when it is down).
    from iqradar.shared.mirror_proxy import start_mirror_proxy

    if not start_mirror_proxy():
        typer.echo("WARNING mirror proxy failed to start; apt stays on upstream mirrors")
    create_app(
        data_path=data_path,
        raw_runs_path=raw_runs_path,
        reporting_path=reporting_path,
        deepswe_runs_path=deepswe_runs_path,
        models_path=models,
        benchmark_path=benchmark,
        prices_path=prices,
    ).run(host=host, port=port)


def _benchmark_project_root(config_path: Path) -> Path:
    absolute_config_path = config_path if config_path.is_absolute() else Path.cwd() / config_path
    parent = absolute_config_path.parent
    return parent.parent if parent.name == "configs" else parent


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _compact_json(text: str, known_secret_values: tuple[str, ...] = ()) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return redact_secret_text(text[:1000], known_secret_values)
    return redact_secret_text(json.dumps(data, ensure_ascii=False)[:1000], known_secret_values)


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not name:
            continue
        if not os.environ.get(name):
            os.environ[name] = _unquote_env_value(value.strip())


def _unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


if __name__ == "__main__":
    app()
