# 抽取输出契约

本目录保留给未来的 JSON Schema、示例和版本化契约文件。

当前契约以 [`docs/extraction-contract.md`](../docs/extraction-contract.md) 为准。
机器可读的 `extraction_schema.json` 由原 MKEA workspace 的 `mkea-bench audit`
生成，不随本独立包分发；如已拿到该文件，可放在本目录。

约定：

- 一条记录 = 一个可区分样品/合成条件；
- 字段名接受中文 label 或英文 key；
- 缺失值写 `null`；
- 建议每个非空字段带 `evidence`（页码 + 原文引文）；
- 不读取 `golden.jsonl`，避免泄漏。
