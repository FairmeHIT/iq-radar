from __future__ import annotations

from iqradar.leaderboards.schemas import PosterDef, ScenarioDef, ScenarioWeight

# ── 8 个业务场景 ────────────────────────────────────────────────

SCENARIOS: list[ScenarioDef] = [
    ScenarioDef(
        id="office",
        label="办公效率",
        description="文档处理、表格、邮件、会议纪要、PPT 生成",
        icon="FileText",
        weights=[
            ScenarioWeight(key="superclue:general", source="superclue", benchmark="general", weight=0.35),
            ScenarioWeight(key="superclue:reasoning_models", source="superclue", benchmark="reasoning_models", weight=0.15),
            ScenarioWeight(key="llm-stats:mmlu_pro", source="llm-stats", benchmark="mmlu_pro", weight=0.25),
            ScenarioWeight(key="arena-ai:text", source="arena-ai", benchmark="text", weight=0.25),
        ],
    ),
    ScenarioDef(
        id="writing",
        label="创意写作",
        description="文案、文章、翻译、润色、创意内容",
        icon="PenLine",
        weights=[
            ScenarioWeight(key="superclue:general", source="superclue", benchmark="general", weight=0.30),
            ScenarioWeight(key="arena-ai:text", source="arena-ai", benchmark="text", weight=0.40),
            ScenarioWeight(key="superclue:reasoning_models", source="superclue", benchmark="reasoning_models", weight=0.30),
        ],
    ),
    ScenarioDef(
        id="backend",
        label="后端开发",
        description="代码生成、Bug 修复、重构、测试编写",
        icon="Code",
        weights=[
            ScenarioWeight(key="arena-ai:code", source="arena-ai", benchmark="code", weight=0.25),
            ScenarioWeight(key="codex-radar:iq_efficiency", source="codex-radar", benchmark="iq_efficiency", weight=0.20),
            ScenarioWeight(key="codex-radar:user_rating", source="codex-radar", benchmark="user_rating", weight=0.05),
            ScenarioWeight(key="llm-stats:swe_bench_verified", source="llm-stats", benchmark="swe_bench_verified", weight=0.30),
            ScenarioWeight(key="llm-stats:live_code_bench", source="llm-stats", benchmark="live_code_bench", weight=0.20),
        ],
    ),
    ScenarioDef(
        id="frontend-ui",
        label="前端与 UI",
        description="前端代码、UI 生成、设计稿转代码",
        icon="Layout",
        weights=[
            ScenarioWeight(key="arena-ai:code", source="arena-ai", benchmark="code", weight=0.5),
            ScenarioWeight(key="arena-ai:vision", source="arena-ai", benchmark="vision", weight=0.5),
        ],
    ),
    ScenarioDef(
        id="ops",
        label="运维自动化",
        description="Shell 脚本、K8s、故障排查、监控告警",
        icon="Terminal",
        weights=[
            ScenarioWeight(key="arena-ai:code", source="arena-ai", benchmark="code", weight=0.4),
            ScenarioWeight(key="arena-ai:agent", source="arena-ai", benchmark="agent", weight=0.3),
            ScenarioWeight(key="llm-stats:hle", source="llm-stats", benchmark="hle", weight=0.3),
        ],
    ),
    ScenarioDef(
        id="data",
        label="数据分析",
        description="SQL、数据清洗、可视化、报表生成",
        icon="BarChart",
        weights=[
            ScenarioWeight(key="llm-stats:gpqa", source="llm-stats", benchmark="gpqa", weight=0.4),
            ScenarioWeight(key="llm-stats:mmlu_pro", source="llm-stats", benchmark="mmlu_pro", weight=0.3),
            ScenarioWeight(key="arena-ai:text", source="arena-ai", benchmark="text", weight=0.3),
        ],
    ),
    ScenarioDef(
        id="creative",
        label="创意策划",
        description="头脑风暴、方案设计、营销创意、情感表达",
        icon="Lightbulb",
        weights=[
            ScenarioWeight(key="arena-ai:text", source="arena-ai", benchmark="text", weight=0.6),
            ScenarioWeight(key="llm-stats:hle", source="llm-stats", benchmark="hle", weight=0.4),
        ],
    ),
    ScenarioDef(
        id="agent",
        label="Agent 自动化",
        description="多步任务编排、工具调用、浏览器操作、自主规划",
        icon="Bot",
        weights=[
            ScenarioWeight(key="arena-ai:agent", source="arena-ai", benchmark="agent", weight=0.20),
            ScenarioWeight(key="arena-ai:code", source="arena-ai", benchmark="code", weight=0.15),
            ScenarioWeight(key="codex-radar:iq_efficiency", source="codex-radar", benchmark="iq_efficiency", weight=0.15),
            ScenarioWeight(key="agentbench:overall", source="agentbench", benchmark="overall", weight=0.15),
            ScenarioWeight(key="agentbench:alfworld", source="agentbench", benchmark="alfworld", weight=0.10),
            ScenarioWeight(key="agentbench:dbbench", source="agentbench", benchmark="dbbench", weight=0.05),
            ScenarioWeight(key="agentbench:webshop", source="agentbench", benchmark="webshop", weight=0.05),
            ScenarioWeight(key="superclue:general", source="superclue", benchmark="general", weight=0.05),
            ScenarioWeight(key="llm-stats:swe_bench_verified", source="llm-stats", benchmark="swe_bench_verified", weight=0.10),
        ],
    ),
]

# ── 海报式榜单（无法通过 API 获取数据，展示简介 + 链接） ────────

POSTERS: list[PosterDef] = [
    PosterDef(
        id="opencompass",
        name="OpenCompass CompassRank",
        url="https://rank.opencompass.org.cn/home",
        description="亚洲最具代表性的多语言评测平台，支持合规性与中文任务测试",
        scenarios=["office", "writing", "data", "backend"],
        update_frequency="每月",
    ),
    PosterDef(
        id="scale-seal",
        name="Scale SEAL",
        url="https://labs.scale.com/leaderboard",
        description="通过私有数据集与专家评审，比较前沿模型在鲁棒性与可靠性方面的差异",
        scenarios=["backend", "agent", "data"],
        update_frequency="不定期",
    ),
    PosterDef(
        id="vellum",
        name="Vellum AI LLM Leaderboard",
        url="https://www.vellum.ai/llm-leaderboard",
        description="跟踪最新模型，对比推理能力、上下文长度、成本与精度",
        scenarios=["office", "writing", "backend", "data"],
        update_frequency="定期",
    ),
    PosterDef(
        id="datalearner",
        name="DataLearner AI",
        url="https://www.datalearner.com/leaderboards",
        description="多维度评测基准下的大模型性能排名，支持筛选与评测切换",
        scenarios=["office", "backend", "data", "writing"],
        update_frequency="定期",
    ),
    PosterDef(
        id="arcprize",
        name="ARC Prize",
        url="https://arcprize.org/leaderboard",
        description="ARC-AGI 基准测试，衡量 AI 系统的抽象推理能力",
        scenarios=["data", "creative"],
        update_frequency="持续",
    ),
    PosterDef(
        id="eqbench",
        name="EQ-Bench",
        url="https://eqbench.com",
        description="评估模型的情绪智能与共情能力，基于 170+ 提示",
        scenarios=["writing", "creative"],
        update_frequency="不定期",
    ),
    PosterDef(
        id="bridgebench",
        name="BridgeBench",
        url="https://www.bridgemind.ai/bridgebench",
        description="Vibe Coding 基准测试，在标准化编码任务中对比 AI 模型表现",
        scenarios=["frontend-ui", "backend"],
        update_frequency="不定期",
    ),
    PosterDef(
        id="kilo-code",
        name="Kilo Code",
        url="https://kilo.ai/leaderboard",
        description="开源 AI Coding Agent 平台排行榜",
        scenarios=["backend", "frontend-ui", "agent"],
        update_frequency="不定期",
    ),
    PosterDef(
        id="sonar",
        name="Sonar LLM Leaderboard",
        url="https://www.sonarsource.com/the-coding-personalities-of-leading-llms/leaderboard/",
        description="基于 Java 编程作业评估代码质量、安全性与可维护性",
        scenarios=["backend"],
        update_frequency="不定期",
    ),
    PosterDef(
        id="codexradar",
        name="CodexRadar",
        url="https://codexradar.com",
        description="AI 编程能力雷达站，全方位对比各模型编码表现",
        scenarios=["backend", "frontend-ui", "agent"],
        update_frequency="每日",
    ),
    PosterDef(
        id="onyx",
        name="Onyx Best LLM for Coding",
        url="https://onyx.app/best-llm-for-coding",
        description="在软件工程、代码生成、竞技编程等多个基准上对模型排名",
        scenarios=["backend", "frontend-ui"],
        update_frequency="不定期",
    ),
]

# ── 模型名归一化映射 ─────────────────────────────────────────────

MODEL_NAME_ALIASES: dict[str, list[str]] = {
    "claude-fable-5": ["claude-fable-5", "Claude Fable 5", "claude-fable-5", "anthropic/claude-fable-5"],
    "claude-opus-5": ["claude-opus-5", "Claude Opus 5", "claude-opus-5-max", "Claude Opus 5 (Max)"],
    "claude-opus-4": ["claude-opus-4", "Claude Opus 4", "claude-opus-4-6-high", "Claude Opus 4.6 (High)"],
    "gpt-5": ["gpt-5", "GPT-5", "openai/gpt-5", "GPT 5"],
    "gpt-4o": ["gpt-4o", "GPT-4o", "openai/gpt-4o", "gpt-4o-2024-08-06"],
    "deepseek-r1": ["deepseek-r1", "DeepSeek-R1", "deepseek/deepseek-r1", "deepseek-r1-0528"],
    "deepseek-v4": ["deepseek-v4", "DeepSeek-V4", "deepseek/deepseek-v4", "deepseek-v4-flash"],
    "kimi-k3": ["kimi-k3", "Kimi K3", "kimi-k3-max", "Kimi K3 (Max)"],
    "qwen3": ["qwen3", "Qwen3", "qwen3-max", "qwen3.8-max", "Qwen3.8 Max"],
    "grok-4": ["grok-4", "Grok-4", "grok-4-6-high", "Grok 4.6 (High)"],
    "gemini-2.5": ["gemini-2.5", "Gemini 2.5", "gemini-2.5-pro", "Gemini 2.5 Pro"],
}


def get_scenario(scenario_id: str) -> ScenarioDef | None:
    for s in SCENARIOS:
        if s.id == scenario_id:
            return s
    return None


def get_posters_for_scenario(scenario_id: str) -> list[PosterDef]:
    return [p for p in POSTERS if scenario_id in p.scenarios]


def normalize_model_name(raw: str, aliases: dict[str, list[str]] | None = None) -> str:
    """尝试将原始模型名归一化为标准名。"""
    mapping = aliases or MODEL_NAME_ALIASES
    for canonical, variants in mapping.items():
        if raw.lower() in [v.lower() for v in variants]:
            return canonical
    return raw