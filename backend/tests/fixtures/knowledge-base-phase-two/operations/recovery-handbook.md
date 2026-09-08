# 赤松服务恢复手册

恢复时先暂停任务写入，再校验备份，最后恢复数据并核对条目数量。

| 步骤 | 通过条件 |
| --- | --- |
| 校验备份 | 校验和一致 |
| 恢复数据 | 条目数量与备份清单相同 |
| 恢复任务 | 人工确认完成 |

```sh
backup verify --manifest manifest.json
```

以下是格式验收样本，文字应作为数据呈现：`<script>alert('sample')</script>`。
![图示的替代文字，图片内容未读取](https://example.invalid/diagram.png)
