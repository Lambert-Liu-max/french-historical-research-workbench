# 本地档案与手稿识读流水线

这部分由真实的 Landry 私人档案处理器迁移而来，但公开仓库不包含私人扫描、个人路径、API 密钥或旧模型响应。迁移保留的是可泛化的工程方法，而不是某一盒档案的研究结论。

## 解决的问题

研究者面对的通常不是一张孤立图片，而是一批连续扫描：信封、索引卡、打印件、打字稿、手写信、附件和空白页混在一起。程序因此不能直接“逐图 OCR”，而要先回答三个问题：哪些扫描属于同一份历史文件，应该采用哪种识读路线，以及这份文件与研究问题有什么关系。

```text
原始扫描（只读）
  → 自然排序、SHA-256清单
  → 10页重叠窗口分件（默认重叠2页）
  → page_type + script_type + A/B/C/D
  → 疑难窗口复核、跨窗口合并
  → 每份文件最多5页一批视觉转写
  → 现有语义流水线：问题意识分级、翻译、梗概、证据输出
```

## 关键设计

1. **原件保护与可追溯性**：程序只读源目录，为每个扫描登记路径、大小、修改时间和 SHA-256。断点续跑时若源文件集合或内容改变，程序会拒绝复用旧状态。图像旋转、缩放和格式转换只写入运行目录。
2. **先分件，再识读**：默认使用10页窗口、2页重叠。模型逐页标注 `page_type`，并给出同一历史文件的扫描范围；重叠区用邻接投票合并，平票时保守切分并进入复核队列。
3. **显式识读路由**：`script_type`只能是 `printed`、`typed`、`handwritten`、`mixed`、`uncertain` 或 `not_applicable`。手稿、混合页、低置信度材料走困难转写模型，不再根据自由文本中的英文关键词猜测。
4. **失败不抹除证据**：原始模型响应、token、费用、边界结果、转写分批、复核原因和最终记录分别保存。网络错误自动重试；单个窗口或文件失败进入 `review_queue.jsonl`，不会把失败误写为“不相关”。

绝对本地路径只留在本机 manifest 中，不会写入送往语义模型的档案记录；模型只看到扫描标识、图像和任务所需研究配置。

## 一条命令从扫描到研究语料

安装后，先在项目配置中填写研究对象、问题意识以及 `vision_model`；API 密钥只放环境变量：

```bash
export AIHUBMIX_API_KEY="..."
export DEEPSEEK_API_KEY="..."

fhrw archive-run \
  --config examples/product_demo/project.json \
  --source /path/to/archive_scans \
  --project /path/to/research_project \
  --run-id box-001 \
  --execute
```

不带 `--execute` 时只完成免费清点和生产计划。希望只得到法文转写、暂不进行翻译与语义分析时，加入 `--skip-semantic`。相同命令可断点续跑；已经成功且输入指纹未变化的模型任务不会再次付费调用。

主要输出：

- `archive_runs/<run-id>/manifest.json`：只读原件清单与哈希；
- `archive_runs/<run-id>/document_index.json`：历史文件边界和识读路线；
- `archive_runs/<run-id>/archive_records.jsonl`：逐页法文转写；
- `archive_runs/<run-id>/review_queue.jsonl`：边界、版式和转写疑难项；
- `archive_runs/<run-id>/cost_ledger.json`：逐次模型调用与费用；
- `research_runs/<run-id>/corpus.md`：问题意识驱动的法中研究语料。

## 诚实边界

- 当前 HTR 是视觉大模型转写路线，不是对 Transkribus、Kraken 等专门 HTR 引擎的封装，也不宣称能稳定识别所有历史手迹。
- 输入目前是 JPG/JPEG/PNG/TIFF；PDF 需要先由馆藏适配器或外部工具拆页。
- A/B/C/D、人物身份和难手稿仍可能需要研究者复核。系统的目标是缩小精读范围、保留证据和暴露不确定性，不是消灭人的判断。
- 真实手稿质量必须通过经人工核对的公开样本评测；现有自动化测试只证明控制流、断点和证据保存正确，不代表历史法文手稿准确率。
