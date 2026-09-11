# IQRadar

本地优先的大模型评测与观测工作台。
对接任意 OpenAI 兼容网关，跑 coding-agent 与 API 基准，在本地仪表盘观察 IQ / 速度 / 成本 / 额度。

[English](README.md) | 中文

| 大盘页 | 测试页 |
|---|---|
| ![大盘页](screenshot/dashboard.png) | ![测试页](screenshot/test.png) |

## 主要功能

- **多基准** — DeepSWE（pier 驱动的 coding-agent 基准）· Terminal-Bench 2（Harbor + mini-swe-agent）· 10 项 API 评测（GPQA-Diamond、AIME 2024、MMLU-Pro、ARC-AGI-2、HLE、LiveCodeBench 等），在 `configs/benchmark.yaml` 增删。
- **任意 OpenAI 兼容网关** — 经 `/v1/models` 自动发现模型；测试页热填 baseURL + key，无需重启。
- **测试工作台** — 按模型 / 样本数 / 种子提交运行；实时 pier / trial / agent / verifier 日志；题目级进度；网关失败题可重测。
- **显式发布** — 运行完成不自动上大盘；勾选发布才合并进不可变版本化快照，可回撤。
- **指标** — `IQ = 通过率 × 1.5`、Wilson 95% 置信区间、耗时、成本与周额度（按 `configs/prices.yaml`）、雷达图展示。
- **榜单汇聚** — 聚合公开 LLM 榜单做横向参考。
- **本地私密** — 纯文件存储（无数据库）、仅绑定回环地址、日志自动脱敏。

## 快速开始

需要 Linux/WSL2、Python 3.12+（[uv](https://docs.astral.sh/uv/)）、Node 20+、Docker、OpenAI 兼容网关。

```bash
cp .env.example .env            # 网关地址 / 模型 / key
cp configs/prices.example.yaml configs/prices.yaml
./start.sh                      # http://127.0.0.1:8080
```

网关占了 8080？`IQ_RADAR_PORT=8081 ./start.sh`

## 使用

1. 测试页选模型 / 样本数 / 种子，提交运行，看实时日志。
2. 勾选已完成的运行 → **发布** → 合并进不可变大盘快照（可回撤）。
3. 打开大盘页看 IQ 雷达、额度雷达与运行明细。

网关热切换：测试页直接填 baseURL + key，无需重启。

## 注意

- 仅绑定回环地址，无鉴权，勿暴露公网。
- 基准检出放 `checkouts/`（见 `configs/benchmark.yaml`）。
- 更多文档见 `docs/`。

## 许可

[MIT](LICENSE)
