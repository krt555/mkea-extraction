# PDF 输入目录

把待抽取的 PDF 放在这里。默认的 `extract` 命令会读取 `papers/` 下的 PDF。

文件编号用于生成 `paper_id` / `paper_key` / `record_id`，命名应包含
`paper_<数字>`，例如：

```text
papers/paper_001.pdf
papers/paper_002.pdf
...
```

也可以显式指定单个文件或目录：

```bash
uv run mkea-extract extract /path/to/paper_042.pdf --out outputs/paper_042.jsonl
uv run mkea-extract extract --pdf-dir /path/to/pdfs --limit 10
```

注意：输出 JSONL 中的 `pdf_path` 按契约固定写成 `papers/<paper_key>.pdf`，
与输入文件实际所在目录无关；校验时不会要求该路径在本地存在。

本目录不包含原项目的 100 篇 PDF（约 216 MB），请自行放入。
