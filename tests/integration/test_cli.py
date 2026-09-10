from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iqradar.cli import _benchmark_project_root, app


runner = CliRunner()


def test_relative_benchmark_config_uses_absolute_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert _benchmark_project_root(Path("configs/benchmark.yaml")) == tmp_path


def missing_env_file(tmp_path: Path) -> str:
    return str(tmp_path / "missing.env")


def write_yaml(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def write_model_config(path: Path) -> Path:
    return write_yaml(
        path,
        """
models:
  - id: model-a
    display_name: Model A
    provider: openai-compatible
    model_name: openai/my-model
    env:
      base_url: LLM_BASE_URL
      api_key: LLM_API_KEY
      model: LLM_MODEL
    effort:
      supported: false
      values: {}
""",
    )


def write_benchmark_config(path: Path, tmp_path: Path) -> Path:
    return write_yaml(
        path,
        f"""
benchmarks:
  deep-swe:
    repo_url: https://github.com/datacurve-ai/deep-swe.git
    local_path: {tmp_path / "deep-swe"}
    tasks_path: {tmp_path / "tasks"}
    default_timeout_sec: 7200
    default_concurrency: 1
    artifact_root: {tmp_path / "artifacts"}
""",
    )


def test_doctor_reports_missing_env_names_without_secret_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://secret-host.example/v1")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_MODEL", "secret-model")

    result = runner.invoke(
        app,
        [
            "--env-file",
            missing_env_file(tmp_path),
            "doctor",
            "--models",
            str(write_model_config(tmp_path / "models.yaml")),
            "--benchmark",
            str(write_benchmark_config(tmp_path / "benchmark.yaml", tmp_path)),
            "--skip-api-check",
        ],
    )

    assert result.exit_code == 1
    assert "LLM_API_KEY" in result.output
    assert "https://secret-host.example/v1" not in result.output
    assert "secret-model" not in result.output


def test_doctor_reports_deepswe_env_file_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deep_swe_dir = tmp_path / "deep-swe"
    deep_swe_dir.mkdir(parents=True)
    (deep_swe_dir / ".env").write_text("OPENAI_BASE_URL=https://x/v1\n", encoding="utf-8")
    monkeypatch.setenv("LLM_BASE_URL", "https://models.example/v1")
    models = write_yaml(
        tmp_path / "models.yaml",
        """
models:
  - id: model-a
    display_name: Model A
    provider: openai-compatible
    model_name: openai/my-model
    env:
      base_url: LLM_BASE_URL
    effort:
      supported: false
      values: {}
""",
    )
    benchmark = write_benchmark_config(tmp_path / "benchmark.yaml", tmp_path)

    result = runner.invoke(
        app,
        [
            "--env-file",
            missing_env_file(tmp_path),
            "doctor",
            "--models",
            str(models),
            "--benchmark",
            str(benchmark),
            "--skip-api-check",
        ],
    )

    assert result.exit_code == 0
    assert "OK deep-swe env file" in result.output


def test_probe_omits_authorization_when_api_key_env_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
    captured_headers: list[dict[str, str]] = []

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

    def fake_urlopen(request: urllib.request.Request, *, timeout: int) -> FakeResponse:
        assert timeout == 30
        captured_headers.append(dict(request.header_items()))
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = runner.invoke(
        app,
        [
            "--env-file",
            missing_env_file(tmp_path),
            "probe",
            "--model",
            "gateway/example-35b",
        ],
    )

    assert result.exit_code == 0
    assert captured_headers
    assert "Authorization" not in captured_headers[0]
    assert "X-portkey-provider" not in captured_headers[0]


def test_probe_redacts_response_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATEWAY_API_KEY", "sk-probe-secret1234567890")

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "token sk-probe-secret1234567890 should not print"
                            }
                        }
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(_request: object, *, timeout: int) -> FakeResponse:
        assert timeout == 5
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = runner.invoke(
        app,
        [
            "--env-file",
            missing_env_file(tmp_path),
            "probe",
            "--model",
            "gateway/example-35b",
            "--timeout-sec",
            "5",
        ],
    )

    assert result.exit_code == 0
    assert "sk-probe-secret1234567890" not in result.output
    assert "[REDACTED_SECRET]" in result.output
