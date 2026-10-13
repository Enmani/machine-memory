# 命令说明

以下 `memo`、`ptree`、`harvest` 是 [本机入口](local-paths.md) 中的命令别名，执行时替换成对应完整命令。项目名取自任务和实际项目，不用临时聊天目录猜测。

## 读取与查找

| 目的 | 命令 |
| --- | --- |
| 精确关键词或编号 | `memo recall "<regex>"` |
| 按意思查找 | `memo search "<question>" --project <project>` |
| 保留被替代的旧决定 | `memo search "<question>" --project <project> --history` |
| 展开二叉树摘要 | `memo zoom <a-b>` |
| 查看全部来源与关系 | `memo inspect <id>` |
| 找项目文件并核对历史 | `ptree find <project> "<question>"` |
| 按意思查项目历史并核对文件 | `ptree find <project> "<question>" --semantic` |
| 查看已索引项目 | `ptree projects` |
| 重开原对话 | `harvest show <reference> --grep "<word>"` |

`search` 按需使用已有 OMP 模型通道，默认最多 8 次检索调用；树摘要可能省略细节。只在已返回的证据不足时调整范围或预算，不机械重复查询。

`wake`、`zoom` 和 `recall` 保留原始历史；语义搜索和项目检索会归并重复项并跟到当前决定。`!` 表示引用文件缺失、Git 中后来改过，或只存在于未合并工作树，需要查看文件现状。

笔记尾部的 `[cx:...]`、`[cc:...]`、`[om:...]` 是 Codex、Claude Code、OMP 原对话入口。历史条目和模型摘要是证据线索，不等于已经验证的当前状态。

## 写入与关系

```text
memo note "[project] 最终结果、理由和成果位置"
memo relate <new-id> <old-id> replaces --reason "采用新决定的依据"
memo relate <new-id> <old-id> enriches --reason "新增的细节"
memo relate <new-id> <old-id> challenges --reason "尚未解决的分歧"
memo relate <new-id> <old-id> duplicate --reason "属于同一事实或事件的证据"
memo unlink <new-id> <old-id> <kind>
```

新编号来自 `note` 的实际输出，旧编号来自检索。同一项目内，新条目编号应晚于旧条目；关系不会删除原条目。恢复旧方案时写“决定改回……”等实际变化，不仅重复旧文字。

完全重复的内容在写入时自动归并，原观察日期和来源保留。`review` 默认只预览语义关系，`review --apply` 写入经过依据检查的关系；仅在需要整理记录时使用。现有三小时同步每次最多审阅 24 条，未处理完的记录留待后续运行。

模型通道不可用时，关键词检索和普通项目检索仍可使用。状态可用 `memo knowledge-status` 检查；不要额外启动服务、安装模型或修改定时任务来完成一次检索。
