# 考公助手（自用版）

基于 **公考脑库（行测）+ SLB 申论真题 + Streamlit + pi Agent** 的本地学习助手。

> **定位**：个人自用学习工具。MIT 许可仅覆盖本仓库源码；**真题内容不随仓库分发**，请按 `NOTICE.md` 本地准备并遵守上游使用规范。

当前能力：行测练习/套卷计时、题库解析优先、错题本笔记、申论开卷练习、知识库检索、学习规划与对话辅导。

## 目录结构

```text
kaogong-assistant/
├── data/
│   ├── kaogong.db          # 行测题库（公考脑库，只读）
│   ├── shenlun.db          # 申论题库（SLB 真题导入，只读）
│   ├── user.db             # 用户数据（交卷/错题本/对话/档案）
│   └── knowledge-index/    # pi-knowledge 本地索引
├── knowledge/              # 考公方法论示例资料（Markdown）
├── agent/                  # pi Agent HTTP 服务
├── app/
│   └── app.py              # Streamlit 前端（行测/申论分模块）
├── scripts/
│   ├── prepare_kaogong.sh  # clone + assemble 行测库
│   ├── prepare_shenlun.py  # 导入申论真题库
│   └── init_user_db.py     # 初始化/升级 user.db
├── requirements.txt
├── TASKS.md
└── README.md
```

## 数据准备

### 1. 获取公考脑库 `kaogong.db`

```bash
bash scripts/prepare_kaogong.sh
```

脚本会：

1. 浅克隆 [jangviktor-web/GongkaoNaoku](https://github.com/jangviktor-web/GongkaoNaoku)
2. 运行其 `assemble_db.py` 合并 `kaogong.db.part-*`
3. 复制到 `data/kaogong.db`

若本地已有 `data/kaogong.db` 与 `data/90-图片/`，脚本会跳过对应步骤。
配图目录约 199MB，**不会进入 git**；含图题依赖该目录才能正常显示。

### 2. 导入申论题库 `shenlun.db`

```bash
python3 scripts/prepare_shenlun.py
```

来源：开源仓库 [mkih76/SLB](https://github.com/mkih76/SLB) 内置 `data/shenlun_zhenti/`（本地 Markdown 导入，不爬网）。
当前本地约 **344** 套申论卷（河南 12 / 国考相关 34）。详见 [NOTICE.md](./NOTICE.md)。

### 3. 初始化用户库

```bash
python3 scripts/init_user_db.py
```

会创建/升级：`submissions`、`answers`、`wrong_book`、`chat_messages`、`learner_profile`、`shenlun_attempts` 等。

## 启动 Agent（阶段 1）

```bash
cd kaogong-assistant/agent
npm install
npm run start   # http://127.0.0.1:8787
```

健康检查：

```bash
curl -s http://127.0.0.1:8787/health
```

### Agent 接口示例

#### 1) 批量错题解析 `POST /explain-wrong`

请求：

```bash
curl -s -X POST http://127.0.0.1:8787/explain-wrong \
  -H 'Content-Type: application/json' \
  -d '{"submission_id": 2}'
```

响应：

```json
{
  "explanations": [
    { "question_id": 89047, "text": "**正确答案：B ..." }
  ]
}
```

#### 2) 单题按需解析 `POST /explain-one`

请求：

```bash
curl -s -X POST http://127.0.0.1:8787/explain-one \
  -H 'Content-Type: application/json' \
  -d '{"question_id": 82934, "user_answer": "A"}'
```

响应：

```json
{
  "text": "你选的 A 项不正确，正确答案是 B ..."
}
```

> Agent 不可用时：交卷批分仍可用；按需按钮显示明确错误提示，页面不崩溃。Streamlit 用 `st.session_state.one_explanations` 缓存按需结果，同一 `question_id + user_answer` 重复点击不重复请求。

#### 3) 学习规划 `POST /plan`

请求：

```bash
curl -s -X POST http://127.0.0.1:8787/plan \
  -H 'Content-Type: application/json' \
  -d '{"days": 7}'
```

响应：

```json
{
  "plan": "# 7 天行测学习规划\n\n**本周重点：先补资料分析..."
}
```

辅助接口：

- `GET /stats`：查看 `get_user_stats` 汇总
- `POST /search-knowledge`：`{"query":"资料分析正确率偏低 怎么练"}`
- `POST /index-knowledge`：手动重建索引

## 启动 Streamlit

```bash
cd kaogong-assistant
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app/app.py --server.address 127.0.0.1 --server.port 8501
```

浏览器打开 <http://127.0.0.1:8501>：

1. 左侧筛选年份 / 省份 / 模块 / 题型
2. 点击「开始做题 / 换一组」
3. 用 radio 作答，点「交卷批分」
4. 在「阅卷结果」页查看得分、错题与 AI 解析

## 公考脑库字段映射（实际表结构）

来源：`GongkaoNaoku` 合并后的 `kaogong.db`（只读打开）。

### 主要表

| 表名 | 用途 | 约计行数 |
|---|---|---:|
| `questions` | 题目正文 | 27,506 |
| `appearances` | 题目在各试卷中的出现记录 | 58,890 |
| `materials` | 资料分析等材料 | 1,578 |
| `review_verified` | 疑点复核结论 | 53 |

### `questions` 字段（本项目使用）

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | INTEGER PK | 内部自增 id（API / 答题记录用它） |
| `qid` | TEXT UNIQUE | 稳定题号（公考脑库 SRS 主键） |
| `module` | TEXT | 模块：判断推理 / 言语理解与表达 / 常识判断 / 资料分析 / 数量关系 / 政治理论 |
| `category` | TEXT | 题型：逻辑判断、片段阅读、数学运算… |
| `kadian` | TEXT | 考点 |
| `region` | TEXT | 省份/地区（含国考、选调等） |
| `year` | TEXT | 年份 |
| `paper` | TEXT | 试卷名 |
| `stem` | TEXT | 题干（可能含 HTML） |
| `options` | TEXT(JSON) | `[{key,label,html,is_answer}]` |
| `answer` | TEXT | 正确答案字母，如 `A`/`B`/`C`/`D` |
| `analysis` | TEXT | 原始解析 |
| `material` | TEXT | 材料正文（资料分析等） |
| `material_ref` | TEXT | 材料分组键 |
| `has_image` | INT | 是否含图 |

> 说明：本项目以实际字段名为准；`user.db` 的 `answers.question_id` 对应 `questions.id`。

### 模块分布（当前库）

| module | 题量 |
|---|---:|
| 判断推理 | 7749 |
| 言语理解与表达 | 6957 |
| 常识判断 | 4844 |
| 资料分析 | 3649 |
| 数量关系 | 3223 |
| 政治理论 | 1084 |

## 用户库 `user.db` 结构

| 表 | 字段 |
|---|---|
| `submissions` | `id, created_at, total, correct, score` |
| `answers` | `id, submission_id, question_id, user_answer, is_correct` |

## 版本锁定

### Python

见 `requirements.txt`：

- `streamlit==1.40.2`（因本机 Python 3.14 下 `1.51.0` 依赖的 pyarrow 无预编译轮子，已降级到可用版本）

完整冻结版本见 `requirements-lock.md`。

### Agent（Node）

见 `agent/package.json` + `agent/package-lock.json`：

- `@earendil-works/pi-coding-agent@1.5.2`
- `@earendil-works/pi-ai@1.5.2`
- `typebox@1.3.27`（工具参数 schema；pi-ai 1.5.x 不再直接导出 `Type`）
- `pi-knowledge@0.14.0`
- `tsx@4.23.15` / `typescript@5.9.3`

当前默认模型取自环境：`PI_PROVIDER=grok` / `PI_MODEL=grok-4.5`。

## 知识库（pi-knowledge）

```bash
cd agent
npm install                 # 已含 pi-knowledge@0.14.0
# 若 better-sqlite3 缺原生绑定：
npm rebuild better-sqlite3
npm run index:knowledge
```

- 资料目录：`knowledge/`（示例 3 份 Markdown，文内注明来源）
- 索引位置：`data/knowledge-index/`（`knowledge.db` + `vectors/`）
- KB 名：`kaogong-methods`
- Agent tool：`search_knowledge(query)`；规划前还会调用 `get_user_stats()`

知识库异常时：做题 / 批分 / 错题解析不受影响，仅规划与知识检索降级。

## 明确不做（见任务书）

- 不爬题、不改公考脑库结构
- 不做用户系统 / 云端部署 / 申论自动评分
- 不把真题库文件提交到 git

## 许可与致谢

- 本仓库源码：`LICENSE`（MIT）
- 第三方题库与上游约束：`NOTICE.md`（必读）
- 主要致谢：
  - [jangviktor-web/GongkaoNaoku](https://github.com/jangviktor-web/GongkaoNaoku)
  - [ERRRC/xingcezhenti](https://github.com/ERRRC/xingcezhenti)
  - [mkih76/SLB](https://github.com/mkih76/SLB)
  - [nczz/pi-knowledge](https://github.com/nczz/pi-knowledge)
  - EarendilWorks / pi coding-agent 生态

使用前请确认：真题仅供个人学习；勿公开传播或商用。
