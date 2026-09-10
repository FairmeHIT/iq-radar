import type { ScenarioView } from './types'

export function createDemoScenarioView(scenarioId: string): ScenarioView {
  return {
    scenario_id: scenarioId,
    scenario_label: SCENARIO_LABELS[scenarioId] ?? scenarioId,
    scenario_description: SCENARIO_DESCRIPTIONS[scenarioId] ?? '',
    rankings: DEMO_RANKINGS,
    posters: DEMO_POSTERS,
  }
}

const SCENARIO_LABELS: Record<string, string> = {
  office: '办公效率',
  writing: '创意写作',
  backend: '后端开发',
  'frontend-ui': '前端与 UI',
  ops: '运维自动化',
  data: '数据分析',
  creative: '创意策划',
  agent: 'Agent 自动化',
}

const SCENARIO_DESCRIPTIONS: Record<string, string> = {
  office: '文档处理、表格、邮件、会议纪要、PPT 生成',
  writing: '文案、文章、翻译、润色、创意内容',
  backend: '代码生成、Bug 修复、重构、测试编写',
  'frontend-ui': '前端代码、UI 生成、设计稿转代码',
  ops: 'Shell 脚本、K8s、故障排查、监控告警',
  data: 'SQL、数据清洗、可视化、报表生成',
  creative: '头脑风暴、方案设计、营销创意、情感表达',
  agent: '多步任务编排、工具调用、浏览器操作、自主规划',
}

const DEMO_RANKINGS = [
  { rank: 1, model_name: 'claude-fable-5', model_display: 'Claude Fable 5', provider: 'Anthropic', composite_score: 95.2, scores: { 'arena-ai:code': 1692, 'arena-ai:text': 1506 }, sources: ['arena-ai'] },
  { rank: 2, model_name: 'kimi-k3', model_display: 'Kimi K3 Max', provider: 'Moonshot', composite_score: 91.8, scores: { 'arena-ai:code': 1674, 'arena-ai:text': 1480 }, sources: ['arena-ai'] },
  { rank: 3, model_name: 'qwen3', model_display: 'Qwen3.8 Max', provider: 'Alibaba', composite_score: 90.5, scores: { 'arena-ai:code': 1667, 'arena-ai:text': 1460 }, sources: ['arena-ai'] },
  { rank: 4, model_name: 'grok-4', model_display: 'Grok 4.6 High', provider: 'SpaceXAI', composite_score: 88.3, scores: { 'arena-ai:code': 1631, 'arena-ai:text': 1440 }, sources: ['arena-ai'] },
  { rank: 5, model_name: 'gpt-5', model_display: 'GPT 5.6 Sol', provider: 'OpenAI', composite_score: 87.1, scores: { 'arena-ai:code': 1622, 'arena-ai:text': 1420 }, sources: ['arena-ai'] },
  { rank: 6, model_name: 'gemini-2.5', model_display: 'Gemini 3.7 Flash', provider: 'Google', composite_score: 85.0, scores: { 'arena-ai:code': 1587, 'arena-ai:text': 1390 }, sources: ['arena-ai'] },
  { rank: 7, model_name: 'deepseek-v4', model_display: 'DeepSeek-V4 Flash', provider: 'DeepSeek', composite_score: 82.4, scores: { 'arena-ai:code': 1540, 'arena-ai:text': 1350, 'llm-stats:swe_bench_verified': 0.806 }, sources: ['arena-ai', 'llm-stats'] },
  { rank: 8, model_name: 'deepseek-r1', model_display: 'DeepSeek-R1', provider: 'DeepSeek', composite_score: 78.6, scores: { 'arena-ai:code': 1480, 'arena-ai:text': 1310 }, sources: ['arena-ai'] },
]

const DEMO_POSTERS = [
  { id: 'opencompass', name: 'OpenCompass CompassRank', url: 'https://rank.opencompass.org.cn/home', description: '亚洲最具代表性的多语言评测平台，支持合规性与中文任务测试', scenarios: ['office', 'writing', 'data', 'backend', 'frontend-ui', 'ops', 'creative', 'agent'], image_url: null, update_frequency: '每月', source_type: 'poster' },
  { id: 'superclue', name: 'SuperCLUE 通用榜', url: 'https://superclueai.com/homepage', description: '中文综合评测，涵盖数学推理、科学推理、代码生成、智能体Agent、精确指令遵循、幻觉控制六大任务', scenarios: ['office', 'writing', 'data', 'creative'], image_url: null, update_frequency: '每月', source_type: 'poster' },
  { id: 'scale-seal', name: 'Scale SEAL', url: 'https://labs.scale.com/leaderboard', description: '通过私有数据集与专家评审，比较前沿模型在鲁棒性与可靠性方面的差异', scenarios: ['backend', 'agent', 'data'], image_url: null, update_frequency: '不定期', source_type: 'poster' },
  { id: 'vellum', name: 'Vellum AI LLM Leaderboard', url: 'https://www.vellum.ai/llm-leaderboard', description: '跟踪最新模型，对比推理能力、上下文长度、成本与精度', scenarios: ['office', 'writing', 'backend', 'data'], image_url: null, update_frequency: '定期', source_type: 'poster' },
  { id: 'kilo-code', name: 'Kilo Code', url: 'https://kilo.ai/leaderboard', description: '开源 AI Coding Agent 平台排行榜', scenarios: ['backend', 'frontend-ui', 'agent'], image_url: null, update_frequency: '不定期', source_type: 'poster' },
  { id: 'codexradar', name: 'CodexRadar', url: 'https://codexradar.com', description: 'AI 编程能力雷达站，全方位对比各模型编码表现', scenarios: ['backend', 'frontend-ui', 'agent'], image_url: null, update_frequency: '每日', source_type: 'poster' },
  { id: 'onyx', name: 'Onyx Best LLM for Coding', url: 'https://onyx.app/best-llm-for-coding', description: '在软件工程、代码生成、竞技编程等多个基准上对模型排名', scenarios: ['backend', 'frontend-ui'], image_url: null, update_frequency: '不定期', source_type: 'poster' },
]