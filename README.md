# IQRadar

Local-first eval & monitoring workbench for LLMs.
Run coding-agent and API benchmarks against any OpenAI-compatible gateway; watch IQ, speed, cost and quota on a local dashboard.

English | [中文](README.zh-CN.md)

| Dashboard | Test page |
|---|---|
| ![Dashboard](screenshot/dashboard.png) | ![Test page](screenshot/test.png) |

**Benchmarks**: DeepSWE · Terminal-Bench 2 · 10 API evals (GPQA-Diamond, AIME 2024, MMLU-Pro, ARC-AGI-2, HLE, LiveCodeBench, …) — editable in `configs/benchmark.yaml`.

## Quick Start

Requires Linux/WSL2, Python 3.12+ ([uv](https://docs.astral.sh/uv/)), Node 20+, Docker, an OpenAI-compatible gateway.

```bash
cp .env.example .env            # gateway URL / model / key
cp configs/prices.example.yaml configs/prices.yaml
./start.sh                      # http://127.0.0.1:8080
```

Gateway on 8080? `IQ_RADAR_PORT=8081 ./start.sh`

## Use

1. Pick model / sample size / seed on the test page, submit a run, watch live logs.
2. Tick completed runs → **Publish** → merged into an immutable dashboard snapshot (withdrawable).
3. Metrics: `IQ = pass_rate × 1.5`, Wilson 95% CI, cost & weekly quota from `configs/prices.yaml`.

Gateway switching is hot — fill baseURL + key on the test page, no restart.

## Notes

- Loopback-only, no auth. Do not expose.
- Benchmark checkouts go under `checkouts/` (see `configs/benchmark.yaml`).
- More docs in `docs/`.

## License

[MIT](LICENSE)
