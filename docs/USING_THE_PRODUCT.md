# 使用产品MVP

这不是评测脚本，而是研究工作台的可运行入口。研究者用一个项目配置声明人物、年代、姓名变体、关键词与问题意识；同一套流水线可接收Gallica材料或本地档案。

## 1. 立即运行离线演示

```bash
export PYTHONPATH="$PWD/src"
python3 -m french_history_workbench run \
  --config examples/product_demo/project.json \
  --input examples/product_demo/records.jsonl \
  --output demo_runs \
  --run-id offline-demo
```

离线模式不调用模型，完成姓名/关键词初筛、A/B/C/D分级、复核队列、断点状态和Markdown输出。macOS也可双击`START_WORKBENCH.command`。

## 2. 用DeepSeek完成语义判断、翻译与梗概

```bash
export DEEPSEEK_API_KEY="你的密钥"
python3 -m french_history_workbench run \
  --config examples/product_demo/project.json \
  --input examples/product_demo/records.jsonl \
  --output demo_runs \
  --run-id deepseek-demo \
  --execute
```

输出目录包含：

- `corpus.md`：面向研究阅读的法中证据文件；
- `results.jsonl`：结构化判断与模型元数据；
- `review_queue.jsonl`：低置信度、OCR或证据问题；
- `state.json`：断点和费用；
- `summary.json`：运行统计。

重复同一命令时，相同输入和研究问题不会重复调用模型。

## 3. 从Gallica官方API建立候选材料

```bash
python3 -m french_history_workbench discover \
  --config examples/product_demo/project.json \
  --project demo_project \
  --limit 5 \
  --execute
```

`--limit 5`是快速预览，只处理至多5份文献；正式穷尽检索使用`--limit 0`。程序会：

- 按年份、议院和人物/OCR变体生成可追溯查询；
- 翻页读取全部SRU结果，并对ARK去重；
- 使用`startResult`翻完每份文献中最多10条一页的ContentSearch结果；
- 合并同一期相邻命中，再下载去重后的上下文ALTO；
- 取得官方印刷页码，并把无法验证、缺页或搜索层不一致的材料写入复核清单。

主要输出：

- `inputs/gallica_records.jsonl`：可直接交给`run`的事件候选；
- `audit/query_manifest.jsonl`：每条查询及其返回量；
- `audit/discovery_records.jsonl`：ARK去重后的文献；
- `audit/miss_review.jsonl`：未被静默删除的检索异常和漏检候选；
- `audit/discovery_summary.json`：本次检索统计。

请求间隔写在项目配置中；相同URL命中有效缓存时不会重新下载。缓存写入前验证XML并采用原子替换，Cloudflare HTML、空响应和损坏XML不会冒充成功数据。

## 4. 导入打印件与手稿

```bash
python3 -m french_history_workbench ingest-local \
  --source /path/to/archive_box \
  --output demo_project/inputs/local_records.jsonl

export AIHUBMIX_API_KEY="你的密钥"
python3 -m french_history_workbench transcribe-local \
  --config examples/product_demo/project.json \
  --input demo_project/inputs/local_records.jsonl \
  --output demo_project/inputs/local_transcribed.jsonl \
  --execute
```

视觉模型先区分打印件、打字稿、手稿、混合页或不可读页，再进行法文转写；低置信度页面进入人工复核。PDF批量拆页仍由馆藏适配器或外部OCR完成，MVP不会把未处理PDF伪装成已识读文本。

## 产品边界

MVP已经跑通“结构化材料 → 问题意识驱动分级 → 翻译/梗概 → 证据化Markdown”。Gallica发现与图片HTR提供真实接口，但公开仓库不附带私人档案、批量缓存或API密钥。生产级跨馆藏适配、PDF拆页和多人标注后台仍属于后续工程。

