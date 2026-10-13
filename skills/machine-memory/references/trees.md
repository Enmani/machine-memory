# 项目、论文和研究树

具体入口以 [本机入口](local-paths.md) 为准。原资料各有自己的树，记忆只记录做过什么，并保存指针，不复制资料正文。

## 项目

`ptree projects` 列出已索引项目。`ptree wake <project>` 查看当前文件概览，`ptree zoom <project> <id>` 展开分支，`ptree node <project> <path>` 将文件或目录与相关历史一起查看。

`ptree history <project> wake|zoom|recall` 是全局 `[project]` 笔记的派生副本。只向全局 `memo` 写笔记，不向项目副本写入。项目尚未索引时可直接检索全局记录；一次查询不需要顺带注册新项目。

## 论文

配置了 PaperTree 时，`paper-tree wake`、`paper-tree search "<question>"`、`paper-tree zoom <path>`、`paper-tree paper <id>` 定位原论文。论文归档和阅读状态仍遵守论文库自己的治理规则。

涉及论文的记忆可带 `paper:<id>`，编号必须来自 PaperTree。`memo recall "paper:12"` 查看围绕该论文做过的工作。

## PhD 研究

配置了研究树时，`research wake`、`research search <words>`、`research zoom <id>`、`research node <id>` 查看研究节点。

涉及具体节点的记忆可带 `node:<id>`，编号必须来自研究树。`memo recall "node:K3"` 查看围绕该节点做过的工作。

本机入口未列出的树不视为已经配置，不猜路径，也不为了普通记忆查询额外安装它。
