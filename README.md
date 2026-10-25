# French Historical Research Workbench

面向法国近现代史研究者的**研究级史料发现、语义标注与事件重建工作流**。

本项目源于阿道夫·兰德里（Adolphe Landry）议会档案与私人档案研究。它不是把所有材料简单交给大模型摘要，而是将历史研究者的判断拆成可标注、可评测、可复核的任务：

1. 从 Gallica 与法国议会官方档案中发现文献和页内命中；
2. 判断人物身份、材料类型和研究相关性；
3. 将单页命中重建为包含议题、发言和回应关系的历史事件；
4. 保存原文、页码、ARK、模型响应与人工复核状态，形成完整证据链。

## 为什么需要它

传统工作流要求研究者反复尝试关键词、逐条打开结果，并在阅读后才能判断材料价值。项目把这一过程转化为：

```text
研究对象与问题意识
  → 多层检索与查询扩展
  → OCR / HTR 与页面缓存
  → 人物和材料类型标注
  → 自适应事件重建
  → 研究相关性分级
  → 人工复核与证据化输出
```

## 人机分工

| 承担者 | 任务 |
|---|---|
| Python | API、下载、缓存、去重、重试、断点、结构化输出 |
| 规则 | 姓名变体、目录/名单、关键词与明显程序信号 |
| 大模型 | 模糊语义分类、事件边界、回应关系、翻译与摘要 |
| 研究者 | 定义标注标准、处理疑难样本、作最终历史解释 |
| Codex | 原型开发、代码维护、测试和故障诊断 |

## 已验证规模

- Gallica 本地缓存：13,057 页 ALTO OCR、2,399 份 ContentSearch、1,131 份 Pagination；
- 事件库：3,921 个候选事件，其中638个具有明确发言信号；
- 战后官方档案：处理588份参议院PDF，从488项候选中收入68项专题材料；
- 首批7个复杂事件完成法文、中文、边界、回应关系和引用信息的端到端验证；
- 日常运行由本地 Python 与模型 API 完成，Agent 不负责长期下载和等待。

以上数字描述工程运行结果，不等同于模型准确率。模型质量由本仓库的 Pilot Benchmark 单独评测。

## Pilot Benchmark

首版评测聚焦两个核心任务：

- **材料分类**：人物身份、正式发言/回应/提及/表决/目录、A/B/C/D等级；
- **事件重建**：议题起止、Landry发言范围、回应对象、直接回应和上下文冗余。

评测集采用分层抽样。现有7个完整事件只作为 `expert_reviewed_seed`；研究者最终签字后才升级为 `expert_verified`。其他机器或规则标签标记为 `silver_needs_expert_review` 或 `pending_review`，不会被计入正式准确率。

详见：

- [产品与任务定义](docs/PRODUCT_SPEC.md)
- [系统架构与数据流](docs/ARCHITECTURE.md)
- [Landry真实案例](docs/CASE_STUDY.md)
- [标注指南](docs/ANNOTATION_GUIDELINES.md)
- [评测方案](docs/EVALUATION_PROTOCOL.md)
- [30条Pilot结果与错误分析](docs/PILOT_RESULTS.md)
- [简历与面试表述](docs/RESUME_AND_INTERVIEW.md)
- [当前能力边界](docs/LIMITATIONS.md)

## 快速查看

本仓库不包含大规模OCR缓存或私人档案。无需API密钥即可查看公开样例并检查评测计划：

```bash
git clone <your-repository-url>
cd french-historical-research-workbench

# 检查30条Pilot候选集是否为合法JSONL
python3 -c 'import json, pathlib; rows=[json.loads(x) for x in pathlib.Path("evaluation/data/pilot_30.jsonl").read_text().splitlines() if x.strip()]; print(f"{len(rows)} samples")'

# 仅生成三条评测计划；默认dry-run，不调用任何付费API
python3 evaluation/run_benchmark.py --run-id pilot-plan --limit 3
```

公开样例：

- [1931年完整议会事件：原文、译文、回应链与出处](examples/public/complete_event_1931.md)
- [10项事件边界校准报告](examples/public/boundary_pilot_report.md)
- [1929—1933年Gallica API检索Pilot](examples/public/gallica_api_pilot_report.md)

如需实际比较模型，先复制并填写示例配置与本地环境变量，再显式启用付费执行：

```bash
cp .env.example .env

# 将API密钥只保存在本地环境中，不要提交.env
set -a; source .env; set +a
python3 evaluation/run_benchmark.py \
  --run-id pilot-v1 \
  --execute \
  --models deepseek,qwen,gpt \
  --budget-usd 5
```

当前示例配置使用DeepSeek官方接口，并通过AIHubMix调用千问与GPT。评测入口默认不发起API请求；`--execute`、模型列表和预算上限必须同时由操作者明确给出。正式准确率只计算经研究者签字为`expert_verified`的样本。模型名称和价格只是示例，付费运行前必须按供应商当期信息核对。

已经完成的DeepSeek v1/v2诊断结果见[30条Pilot结果](docs/PILOT_RESULTS.md)。仓库不提交原始API响应，只公开可核查的汇总指标与评测限制。

## 从现有研究语料重建Pilot（可选）

公开仓库已经附带脱敏后的Pilot候选集。只有拥有原始Landry研究语料的维护者才需要重新生成：

```bash
python3 src/build_pilot_dataset.py \
  --source-root /path/to/private-landry-corpus \
  --output-dir evaluation/data
```

该命令不会把机器标签自动升级为金标准；人工裁决应记录在`pilot_30_human_review.csv`中。

## 仓库状态

这是从真实研究生产系统提炼的作品集版本。目前保留可公开的评测框架和小型样例；大规模OCR缓存、私人档案图像、API密钥和包含个人路径的运行日志不进入公开仓库。

代码与原创文档采用[MIT License](LICENSE)。示例中引用的第三方档案、OCR和元数据仍受其来源机构的权利声明与使用条款约束。

