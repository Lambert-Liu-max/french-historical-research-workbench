# Pilot Benchmark 评测方案

## 评测目的

1. 判断AI能否完成材料分类和事件重建，以及哪些样本可以自动处理；
2. 定位错误来自任务定义、Prompt、输入上下文、工作流还是模型能力；
3. 在质量、成本、速度和稳定性之间选择模型与人工复核策略。

## 数据划分

- `development`：允许用于修改Prompt和示例；
- `test`：Prompt冻结后才运行，不针对测试结果继续调参；
- `gold_status=expert_verified`：计入正式指标；
- `expert_reviewed_seed`：已有高质量人工整理，但尚待本轮基准签字；
- `pending_review`或`silver`：只用于流程测试，不进入准确率。

## 对照方案

- 规则/固定窗口基线；
- DeepSeek；
- 千问；
- GPT。

所有模型使用同一输入、同一Prompt、同一JSON Schema和尽可能一致的采样参数。

## 指标

### 材料分类

- identity、material_type、material_level的Accuracy与Macro-F1；
- A/B有效材料召回率；
- 应送人工而未送人工的高风险漏报率；
- JSON Schema通过率。

### 事件重建

- 页面区间IoU；
- Landry发言覆盖率；
- 回应关系F1；
- 必要信息完整率；
- 无关上下文冗余率。

### 生产指标

- 单条成本、平均延迟、API失败率；
- 自动通过率与人工复核率。

## 诊断实验

- 同模型、不同Prompt：诊断Prompt影响；
- 同Prompt、不同模型：诊断模型能力；
- 自动上下文与人工完整上下文：诊断检索/工作流影响；
- 人工标注分歧：诊断标签定义是否清晰。

