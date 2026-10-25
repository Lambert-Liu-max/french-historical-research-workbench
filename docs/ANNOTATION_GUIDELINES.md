# Pilot Benchmark 标注指南

## 标注单位

一个样本对应一次人物命中及其连续OCR上下文。标注者只能依据提供的文本和元数据，不使用模型预测作为答案来源。

## 任务一：材料分类

### 人物身份 `identity_status`

- `confirmed`：全名、职务、选区或明确语境能够确认目标人物；
- `probable`：只有姓氏或简称，但时间、议院和语境高度吻合；
- `uncertain`：证据不足或存在冲突；
- `false`：明确为他人或OCR误识别。

### 材料类型 `material_type`

- `formal_speech`：有明确发言者标记和连续发言；
- `interjection_or_response`：插话、简短回应或直接互动；
- `substantive_mention`：他人实质讨论其主张、报告或行动；
- `proposal_report_committee`：提案、报告、委员会或任命行为；
- `vote_or_attendance`：投票、出席、缺席或名单中的政治行为；
- `index_or_toc`：目录、索引或仅导航性条目；
- `irrelevant_or_ocr_error`：语义无关或OCR误识别；
- `uncertain`：无法稳定归类。

### 研究等级 `material_level`

- `A`：与核心研究问题直接相关，含可用于论证的明确证据；
- `B`：与主题、背景、人物网络或制度环境相关，值得泛读；
- `C`：当前关联较弱或证据不足，编目保留；
- `D`：表决、名单、目录等需要采用专门简化输出的材料；

等级描述的是材料与**当前研究问题**的关系，不是材料永久不变的价值。

## 任务二：事件重建

标注以下字段：

- `event_start`、`event_end`：页码与行号；
- `landry_ranges`：Landry实际发言的一个或多个范围；
- `speaker_before_landry`：必要时的直接前置发言者；
- `landry_response_target`：Landry直接回应对象；
- `responses_to_landry`：直接回应Landry的发言者；
- `outcome_present`：是否保留通过、否决、延期、转交等结果；
- `needs_review`：证据冲突、OCR严重损坏或边界无法确认时为真。

边界应满足“最小但语义完整”：包含理解互动所必需的信息，同时在进入下一无关议题前停止。

## 冲突处理

无法确认时不得强行选择。填写 `uncertain` 或 `needs_review=true`，并在 `annotation_note` 说明缺失证据。人工分歧经裁决后才可将 `gold_status` 改为 `expert_verified`。

