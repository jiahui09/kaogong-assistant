# NOTICE — 第三方来源声明与使用规范

本仓库（`kaogong-assistant`）是**个人自用学习工具**的源码与编排层。  
`LICENSE`（MIT）仅覆盖本仓库作者编写的软件代码与文档，**不覆盖**真题文本、材料、图片、官方解析等考试内容。

## 1. 上游项目致谢与引用

| 用途 | 项目 | 许可/约束（以原仓库为准） | 本仓库如何使用 |
|---|---|---|---|
| 行测题库结构与合并脚本 | [jangviktor-web/GongkaoNaoku](https://github.com/jangviktor-web/GongkaoNaoku)（公考脑库） | Apache-2.0（代码）；题库内容仅供个人学习 | 通过 `scripts/prepare_kaogong.sh` 克隆并 `assemble_db.py` 生成本地 `data/kaogong.db`（**不入库、不推送**） |
| 行测真题源头 | [ERRRC/xingcezhenti](https://github.com/ERRRC/xingcezhenti) | 真题版权归原作者/原平台；学习用途 | 由公考脑库间接使用；本仓库不直接分发其内容 |
| 申论真题 Markdown / 解析脚本 | [mkih76/SLB](https://github.com/mkih76/SLB)（申论帮） | MIT（软件）；题库内容仍受原版权约束 | 通过 `scripts/prepare_shenlun.py` 复用其 `import_zhenti.py` 与本地 `shenlun_zhenti/` 生成 `data/shenlun.db`（**不入库、不推送**） |
| Agent / RAG 运行时 | [EarendilWorks/pi](https://github.com/EarendilWorks/pi) 生态：`@earendil-works/pi-coding-agent`、`@earendil-works/pi-ai` | 以对应 npm 包许可为准 | Agent HTTP 服务依赖 |
| 本地知识库检索 | [nczz/pi-knowledge](https://github.com/nczz/pi-knowledge) | MIT | `search_knowledge` 工具与本地索引 |

如引用或二次分发本仓库代码，请保留本 NOTICE，并继续遵守上游项目要求。

## 2. 使用规范（必读）

1. **仅限个人学习 / 小范围学习交流**。不要把真题库、解析、材料用于商业售卖、公开镜像站或大规模传播。
2. **不要把 `data/kaogong.db`、`data/shenlun.db`、`data/user.db` 推送到公开仓库**。本仓库 `.gitignore` 已默认排除。
3. 题库版权归原作者与原题库平台；本工具只做本地读取、练习与辅导编排。
4. 申论模块当前**不做自动评分**；任何 AI 解析/规划仅供参考，不构成考试成绩或官方解释。
5. 若上游仓库更新许可或要求下架内容，使用者应停止对应数据的使用与分发，并自行删除本地题库文件。
6. 部署到公网前，请自行确认你所在地区的版权与考试资料合规要求；作者不对滥用行为负责。

## 3. 本地数据生成方式（不包含在 git 中）

```bash
# 行测（公考脑库）
bash scripts/prepare_kaogong.sh

# 申论（SLB 本地 Markdown 导入）
python3 scripts/prepare_shenlun.py

# 用户库
python3 scripts/init_user_db.py
```

## 4. 商标与品牌

“公务员考试 / 国考 / 省考 / 粉笔”等名称仅为描述备考场景；本项目与任何考试主管部门或培训机构无隶属关系。
