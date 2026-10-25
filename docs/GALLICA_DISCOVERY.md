# Gallica高召回发现层

## 它解决什么问题

Gallica网页把书目检索、全文检索和馆藏展示混在同一界面，OCR错误又会让同一人物产生不同拼写。工作台因此不把“网页搜索一次没有结果”当成史料不存在，而是保存每个检索入口、返回结果和失败原因。

```text
人物、年份、议院与姓名/OCR变体
  → SRU查询计划与完整分页
  → ARK级文献合并
  → ContentSearch逐文献完整分页
  → 相邻页命中聚类
  → Pagination + 去重ALTO上下文
  → 本地姓名复核与缓存漏检审计
  → 事件候选 / miss_review
```

官方依据：[Gallica检索API](https://api.bnf.fr/fr/api-gallica-de-recherche)提供SRU与`startRecord`；[Gallica文献API](https://api.bnf.fr/fr/api-document-de-gallica)说明ContentSearch每次最多返回10项，可用`startResult`继续，并提供Pagination和逐页ALTO。

## 四个关键设计

1. **查询不再是黑箱。** `query_manifest.jsonl`记录年份、议院、查询词、CQL、报告结果数、实际取得数和错误。预览限制导致未执行的查询也会被明确标记。
2. **姓名词典分远程检索与本地复核。** 远程查询使用高价值姓名和OCR变体；本地ALTO复核支持大小写、标点、换行以及`Landri`、`Landy`、`L. Andry`等显式变体。没有姓氏的泛称不能单独证明身份。
3. **先合并，再扩上下文。** 同一期相邻命中先聚为事件，只下载一次去重页面；扫描页通过Pagination映射为印刷页。
4. **不确定性不等于删除。** SRU与ContentSearch不一致、ALTO无法验证、上下文缺页、分页缺失以及缓存中额外发现的人名都进入`miss_review.jsonl`。

## 可重复性

原始XML以请求URL的SHA-256为缓存键。再次运行时，只有文件存在、非空且能解析为XML才会复用；HTML安全验证页和损坏文件会触发重新请求。新文件先写临时文件并完成验证，再原子替换正式缓存。

当前自动测试覆盖：多变体查询、SRU分页、ContentSearch超过10项后的分页、印刷页映射、损坏缓存重取、跨行OCR姓名、相邻命中合并、上下文去重、异常清单以及断点重跑不重复下载。

## 能力边界

漏检审计只能检查已经下载到本地的ALTO；它不能读取Gallica从未数字化或从未提供OCR的页面。系统的目标是让检索路径和遗漏风险可见、可复查，而不是声称任何搜索引擎能够保证绝对穷尽。
