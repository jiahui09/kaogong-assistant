# 考公助手项目验收汇报

| 项 | 内容 |
|---|---|
| 项目路径 | `/workspace/project/kaogong-assistant` |
| 版本 | v1.0（任务书阶段 0–3 最小功能闭环） |
| 验收日期 | 2026-11-13 |
| 项目性质 | 个人自用，不发布、不对外部署、不做多用户隔离 |
| 验收结论 | **通过**：做题批分、错题/按需 AI 解析、知识库检索、个人学习规划均已跑通 |

---

## 1. 项目目标回顾

构建本地运行的考公学习助手，实现：

1. 行测真题做题、交卷、自动批分
2. 错题自动调用 AI 解析
3. 阅卷页每题可点击「AI 解析」按需解析
4. AI 具备考公知识库检索能力
5. AI 能基于用户做题统计生成个人学习规划

技术路线（未改选型）：

- 题库：公考脑库（GongkaoNaoku）只读 SQLite
- AI：pi SDK Agent + custom tools + pi-knowledge
- 前端：Streamlit
- 用户数据：独立 `user.db`
- 通信：Agent 本地 HTTP（`127.0.0.1:8787`），Streamlit 通过 HTTP 调用

---

## 2. 项目架构

```text
浏览器 / Streamlit UI (:8501)
        │  HTTP
        ▼
┌──────────────────────┐     HTTP JSON      ┌────────────────────────────┐
│  app/app.py          │ ─────────────────► │  agent/src/server.ts       │
│  做题 / 阅卷 / 规划  │ ◄───────────────── │  :8787                     │
└──────────┬───────────┘                    └─────────────┬──────────────┘
           │                                              │
           │ sqlite                                       │ createAgentSession
           ▼                                              ▼
┌──────────────────────┐                    ┌────────────────────────────┐
│ data/kaogong.db      │◄──只读── tools ───│ agent/src/agent.ts         │
│ (公考脑库题库)        │                    │ + customTools              │
│                      │                    │ + prompts/system.md        │
│ data/user.db         │◄──读写── 前端/工具─│                            │
│ (答题/错题/统计)      │                    │ agent/src/knowledge.ts     │
│                      │                    │ + pi-knowledge             │
│ data/knowledge-index │◄──索引────────────│                            │
│ (RAG 向量/FTS)       │                    └────────────────────────────┘
└──────────────────────┘
           ▲
           │ 源文件
┌──────────────────────┐
│ knowledge/*.md       │  考公方法论示例资料
└──────────────────────┘
```

### 2.1 分层说明

| 层 | 职责 | 关键实现 |
|---|---|---|
| 前端 | 选题筛选、作答、交卷批分、展示解析/规划 | `app/app.py` |
| Agent HTTP | 对外稳定 JSON API，隔离前后端语言 | `agent/src/server.ts` |
| Agent 运行时 | pi `createAgentSession`、内存会话、system prompt | `agent/src/agent.ts` |
| Tools | 题库查询、交卷明细、用户统计、知识库检索 | `agent/src/tools.ts` / `knowledge.ts` |
| 数据 | 题库只读；用户库可写；知识库索引独立存放 | `data/*` |

### 2.2 关键接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/health` | 健康检查 |
| POST | `/explain-wrong` | 交卷后批量错题解析 |
| POST | `/explain-one` | 单题按需解析 |
| POST | `/plan` | 基于统计 + 知识库生成学习规划 |
| GET | `/stats` | 调试：用户做题统计 |
| POST | `/search-knowledge` | 调试：知识库检索 |
| POST | `/index-knowledge` | 调试：重建知识库索引 |

### 2.3 版本锁定

**Python**

- `streamlit==1.40.2`（完整冻结见 `requirements-lock.md`）

**Agent / Node**

- `@earendil-works/pi-coding-agent@1.5.2`
- `@earendil-works/pi-ai@1.5.2`
- `typebox@1.3.27`
- `pi-knowledge@0.14.0`
- `tsx@4.23.15` / `typescript@5.9.3`

运行模型（本机环境）：`PI_PROVIDER=grok` / `PI_MODEL=grok-4.5`

---

## 3. 修改与新增文件清单

本项目为新建目录，全部为新增文件（未修改公考脑库原始库结构）。

### 3.1 根目录与文档

| 文件 | 说明 |
|---|---|
| `README.md` | 数据准备、启动方式、字段映射、接口示例、版本锁定 |
| `TASKS.md` | 分阶段执行记录与验收结果 |
| `ACCEPTANCE_REPORT.md` | 本验收汇报 |
| `requirements.txt` | Python 依赖 |
| `requirements-lock.md` | pip freeze 关键包锁定 |
| `.gitignore` | 忽略 venv、题库、用户库、知识库索引、node_modules 等 |

### 3.2 数据与知识资料

| 路径 | 说明 |
|---|---|
| `data/kaogong.db` | 公考脑库合并题库（约 148MB，27506 题，只读） |
| `data/user.db` | 用户答题记录（自建） |
| `data/knowledge-index/` | pi-knowledge 索引（`knowledge.db` + `vectors/`） |
| `knowledge/01-xingce-overview.md` | 行测备考总览 |
| `knowledge/02-ziliaofenxi-methods.md` | 资料分析速算与补弱方法 |
| `knowledge/03-study-plan-template.md` | 个人化规划模板 |

### 3.3 前端

| 文件 | 说明 |
|---|---|
| `app/app.py` | Streamlit 单页：做题 / 阅卷 / 学习规划 |

### 3.4 Agent 服务

| 文件 | 说明 |
|---|---|
| `agent/package.json` / `package-lock.json` | Node 依赖与脚本 |
| `agent/tsconfig.json` | TypeScript 配置 |
| `agent/prompts/system.md` | Agent 系统提示词 |
| `agent/src/server.ts` | 本地 HTTP 服务 |
| `agent/src/agent.ts` | 解析 / 规划会话编排 |
| `agent/src/tools.ts` | `get_question` / `get_submission_answers` / `get_user_stats` / `search_knowledge` |
| `agent/src/knowledge.ts` | pi-knowledge 引擎封装 |
| `agent/src/paths.ts` | 路径常量 |
| `agent/src/index_knowledge.ts` | 索引脚本 |
| `agent/src/verify_stage1.ts` | 阶段 1 验收 |
| `agent/src/verify_stage2.ts` | 阶段 2 验收 |
| `agent/src/verify_stage3.ts` | 阶段 3 验收 |

### 3.5 脚本

| 文件 | 说明 |
|---|---|
| `scripts/prepare_kaogong.sh` | clone + assemble 公考脑库 |
| `scripts/init_user_db.py` | 初始化 `user.db` |
| `scripts/verify_stage0.py` | 阶段 0 非交互批分验收 |
| `scripts/verify_stage2.py` | 阶段 2 前端侧接口/缓存验收 |

---

## 4. 分阶段实现效果与验收结果

### 4.1 阶段 0：做题 / 交卷 / 批分闭环

**效果**

- 支持按年份 / 省份 / 模块 / 题型筛选
- `st.radio` 作答，状态保存在 `st.session_state`
- 交卷后写入 `user.db`，展示得分与错题列表（题干、用户答案、正确答案）

**验收**

- `scripts/verify_stage0.py` → `[PASS]`
- Streamlit `http://127.0.0.1:8501` → HTTP 200
- 样例：10 题故意错 5 题，得分 50.0，`submissions`/`answers` 记录正确

### 4.2 阶段 1：错题自动 AI 解析

**效果**

- Agent 服务监听 `127.0.0.1:8787`
- 交卷后自动 `POST /explain-wrong`
- system prompt 强制先调 `get_question`，避免编造题目
- Agent 不可用时仅提示「AI 解析暂不可用」，批分不受影响

**验收**

- `npm run verify` → `[PASS]`
- `curl /explain-wrong` 返回 JSON；人工抽查 2 题解析命中正确答案，未编造选项

### 4.3 阶段 2：按需「AI 解析」按钮

**效果**

- 阅卷页错题与全部题目均可点击「AI 解析」
- 结果缓存于 `st.session_state.one_explanations`
- 同题同答案重复点击不重复请求
- 失败有明确提示，页面不崩溃

**验收**

- `npm run verify:stage2` → `[PASS]`
- `scripts/verify_stage2.py` → `[PASS]`
- Agent 关闭后按需解析正确降级

### 4.4 阶段 3：知识库 + 个人规划

**效果**

- `pi-knowledge` 索引 3 份方法论资料（14 chunks）
- `search_knowledge` 可检索相关片段
- `get_user_stats` 汇总总正确率与各模块正确率
- `POST /plan` 生成按天规划；Streamlit「学习规划」页可展示

**验收（实测摘录）**

- 检索「资料分析正确率偏低 怎么练」命中 `02-ziliaofenxi-methods.md`
- 规划明确引用真实统计：资料分析 **25%**、言语 **46.67%**、总正确率 **39.13%**
- Agent 关闭时规划降级；做题批分仍可用

### 4.5 当前运行状态（验收时）

| 服务 | 地址 | 状态 |
|---|---|---|
| Streamlit | `http://127.0.0.1:8501` | 运行中 |
| Agent | `http://127.0.0.1:8787` | 运行中 |
| 题库 | `data/kaogong.db` | 27506 题 |
| 用户库 | `data/user.db` | 已有答题与交卷记录 |

---

## 5. 使用方法指南

### 5.1 首次准备

```bash
cd /workspace/project/kaogong-assistant

# 1) 题库（若 data/kaogong.db 已存在可跳过）
bash scripts/prepare_kaogong.sh

# 2) Python 环境
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 scripts/init_user_db.py

# 3) Agent 依赖
cd agent
npm install
# 若 better-sqlite3 缺原生绑定：
npm rebuild better-sqlite3

# 4) 知识库索引（首次约数分钟）
npm run index:knowledge
cd ..
```

### 5.2 日常启动（两个终端）

**终端 A：Agent**

```bash
cd /workspace/project/kaogong-assistant/agent
npm run start
# => http://127.0.0.1:8787
```

**终端 B：Streamlit**

```bash
cd /workspace/project/kaogong-assistant
source .venv/bin/activate
streamlit run app/app.py --server.address 127.0.0.1 --server.port 8501
# => http://127.0.0.1:8501
```

### 5.3 页面操作流程

1. **做题**：左侧筛选年份/省份/模块/题型 →「开始做题 / 换一组」→ 选择选项 →「交卷批分」
2. **阅卷结果**：查看得分与错题；可看自动 AI 解析；也可点「AI 解析」按需生成
3. **学习规划**：切到「学习规划」页 → 选择天数 →「生成学习规划」

### 5.4 常用验收 / 调试命令

```bash
# 阶段验收
.venv/bin/python scripts/verify_stage0.py
cd agent && npm run verify
cd agent && npm run verify:stage2
cd agent && npm run verify:stage3

# 接口冒烟
curl -s http://127.0.0.1:8787/health
curl -s http://127.0.0.1:8787/stats
curl -s -X POST http://127.0.0.1:8787/explain-one \
  -H 'Content-Type: application/json' \
  -d '{"question_id":82934,"user_answer":"A"}'
curl -s -X POST http://127.0.0.1:8787/plan \
  -H 'Content-Type: application/json' \
  -d '{"days":7}'
```

### 5.5 降级行为（预期）

| 场景 | 预期 |
|---|---|
| Agent 服务未启动 | 做题/交卷/批分正常；解析与规划显示「暂不可用」 |
| 知识库索引异常 | 做题与解析不受影响；规划/检索可能失败并提示 |
| 某题无选项或无答案 | 前端跳过或按空答案计错，不崩溃 |

### 5.6 追加知识库资料

1. 把 Markdown/PDF 放入 `knowledge/`
2. 删除或更新索引后重建：

```bash
cd agent
# 若需强制重建，可删除 ../data/knowledge-index 后再执行：
npm run index:knowledge
```

---

## 6. 风险与已处理事项

| 风险 | 处理结果 |
|---|---|
| pi SDK / 插件 API 变更快 | 锁定 1.5.2；`Type` 改从 `typebox` 导入；不改插件源码 |
| 公考脑库字段与预期不符 | 以实际表结构为准，写入 README 字段映射 |
| Streamlit 状态丢失 | 答题/解析/规划状态均放 `st.session_state` |
| Agent 编造题目内容 | system prompt 强制先 `get_question`；人工抽查通过 |
| 知识库检索质量 | 先保证能命中相关片段；示例资料可继续扩充 |
| Python 3.14 / 原生模块安装 | venv + streamlit 降级；`better-sqlite3` approve/rebuild |

---

## 7. 明确未做（符合任务书）

- 不爬取网站、不引入付费版权题库
- 不做用户注册 / 登录 / 权限
- 不做申论主观题自动评分
- 不做云端部署 / Docker 化
- 不追求 UI 美观，只保证功能可用
- 不修改公考脑库原始数据库结构

---

## 8. 遗留与可选后续

1. 规划生成耗时约 1–2 分钟，可改为流式输出改善等待体验
2. `knowledge/` 目前仅 3 份方法论笔记，可持续追加后重建索引
3. 图形题默认降权（优先无图题）；若需练图形题，可关闭该筛选项并增强图片渲染
4. 批量错题解析耗时随错题数上升，可按题并发或限流

---

## 9. 最终结论

**考公助手 v1.0 验收通过。**

已形成可本地运行的最小闭环：

**题库做题 → 交卷批分 → 错题/按需 AI 解析 → 知识库检索 → 个人学习规划**

配套交付：

- 可运行代码与数据脚本
- README / TASKS / 本验收汇报
- 依赖锁文件与分阶段验收命令

建议日常使用保持 Agent 与 Streamlit 同时启动；仅做题时也可只开 Streamlit，AI 功能会自动降级提示。
