# TASKS — 考公助手执行记录

## 阶段 0：数据准备与最小做题闭环（不接 AI）

**状态**：已完成，等待确认后进入阶段 1  
**日期**：2026-11-13

### 完成了什么

1. 克隆公考脑库 `jangviktor-web/GongkaoNaoku`，运行 `assemble_db.py`，得到 `data/kaogong.db`（约 148MB，27506 题）。
2. 梳理并写入 README 的实际表结构：`questions` / `appearances` / `materials` / `review_verified`。
3. 初始化 `data/user.db`：`submissions` + `answers`。
4. 实现 `app/app.py`：
   - 按年份 / 省份 / 模块 / 题型筛选
   - `st.radio` 作答，状态存 `st.session_state`
   - 交卷批分并写入 `user.db`
   - 阅卷页展示得分与错题（题干、用户答案、正确答案）
5. 编写 `scripts/prepare_kaogong.sh`、`scripts/init_user_db.py`、`requirements.txt`、`README.md`。

### 如何验证

```bash
cd kaogong-assistant
bash scripts/prepare_kaogong.sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 scripts/init_user_db.py
python3 scripts/verify_stage0.py   # 非交互批分验收
streamlit run app/app.py --server.address 127.0.0.1 --server.port 8501
```

人工验收：打开页面 → 选题 ≥10 道 → 交卷 → 看得分与错题 → 检查 `user.db` 有记录。

### 验收结果（已跑通）

- `scripts/verify_stage0.py` → `[PASS]`，submission_id=1，10 题中故意错 5 题，得分 50.0；`user.db` 中 submissions/answers 记录正确。
- `streamlit run app/app.py` → `http://127.0.0.1:8501` 返回 HTTP 200。

### 遇到的问题

1. `fetch_content` 访问 GitHub raw 被本机 SSRF/代理规则拦截；改用 `git clone` 成功拿到仓库与分卷库。
2. 系统 Python 为 externally-managed；已用项目内 `.venv` 安装依赖。
3. `streamlit==1.51.0` 在 Python 3.14 下因 pyarrow 无轮子装失败；降级到 `streamlit==1.40.2` 并锁定到 `requirements-lock.md`。
4. Streamlit `st.radio` 默认会选中第一项；已加「(请选择)」占位，避免未作答被记成 A。
5. 图形题含图较多；筛选默认「优先无图题」，保证阶段 0 可读。

### 下一阶段计划（阶段 1）

- 初始化 `agent/` Node/TypeScript + pi SDK
- custom tools：`get_question` / `get_submission_answers`
- HTTP：`POST /explain-wrong` @ `127.0.0.1:8787`
- Streamlit 交卷后调用解析；服务不可用时降级提示

---

## 阶段 1：pi Agent 本地服务 + 错题自动解析

**状态**：已完成，等待确认后进入阶段 2  
**日期**：2026-11-13

### 完成了什么

1. 初始化 `agent/`（TypeScript + tsx），安装并锁定：
   - `@earendil-works/pi-coding-agent@1.5.2`
   - `@earendil-works/pi-ai@1.5.2`
   - `typebox@1.3.27`
2. custom tools：`get_question` / `get_submission_answers`（`node:sqlite` 只读打开题库与 user.db）。
3. `explainWrongAnswers(submissionId)`：`SessionManager.inMemory()` + system prompt 强制先调工具。
4. HTTP 服务 `127.0.0.1:8787`：`GET /health`、`POST /explain-wrong`（顺带预留 `/explain-one`）。
5. Streamlit 交卷后自动请求解析；服务不可用时降级提示，不影响批分。

### 如何验证

```bash
cd agent && npm run verify                 # 直接调用 Agent 解析 2 道错题
npm run start                              # 另开终端
curl -s http://127.0.0.1:8787/health
curl -s -X POST http://127.0.0.1:8787/explain-wrong \
  -H 'Content-Type: application/json' -d '{"submission_id":2}'
# 关闭 agent 后再交卷：页面应显示「AI 解析暂不可用」，得分仍写入 user.db
```

### 验收结果（已跑通）

- `npm run verify` → `[PASS]`；2 道错题解析均命中正确答案字母，内容与题干一致，未编造选项。
- `curl /explain-wrong` → 返回 JSON，`question_id` 为 89047 / 84443。
- Agent 关闭后 `fetch_wrong_explanations` → `AI 解析暂不可用`；`save_submission` 仍正常。

### 遇到的问题

1. `@earendil-works/pi-ai@1.5.2` 不再导出 `Type`；按当前生态改用 `typebox` 定义工具参数。
2. 官方示例里的 `@sinclair/typebox` / 旧版 `Type` 导入路径已过时；以已安装包实际导出为准，不改插件源码。
3. 批量解析耗时随错题数增加；验收时用精简 submission（2 题）控制成本。

### 下一阶段计划（阶段 2）

- 完善并验收 `POST /explain-one`
- Streamlit 阅卷页每题「AI 解析」按钮 + session 缓存
- README 补充接口请求/响应示例

---

## 阶段 2：按需解析按钮

**状态**：已完成，等待确认后进入阶段 3  
**日期**：2026-11-13

### 完成了什么

1. 确认并验收 `POST /explain-one`：一次性 `SessionManager.inMemory()` 会话，入参 `{question_id, user_answer}`，返回 `{text}`。
2. Streamlit 阅卷页：
   - 错题列表与全部题目均提供「AI 解析」按钮
   - 结果缓存于 `st.session_state.one_explanations`（key=`question_id::USER_ANSWER`）
   - 重复点击命中缓存，不重复请求
   - 加载态 + Agent 不可用时明确错误提示
3. README 补充两个 Agent 接口的请求/响应示例。
4. 验收脚本：`agent/src/verify_stage2.ts`、`scripts/verify_stage2.py`。

### 如何验证

```bash
cd agent && npm run verify:stage2
# 或
curl -s -X POST http://127.0.0.1:8787/explain-one \
  -H 'Content-Type: application/json' \
  -d '{"question_id":82934,"user_answer":"A"}'

cd .. && .venv/bin/python scripts/verify_stage2.py
# 关闭 agent 后再点按钮：应提示「AI 解析暂不可用」，页面不崩
```

### 验收结果（已跑通）

- `verify_stage2.ts` → `[PASS]`，解析包含正确答案 B。
- `verify_stage2.py` → `[PASS]`。
- Agent 关闭后 `fetch_one_explanation` 返回明确错误；缓存命中逻辑成立。

### 遇到的问题

- 无阻塞性问题。批量自动解析结果会作为按需缓存种子，避免同一错题二次请求。

### 下一阶段计划（阶段 3）

- 安装/配置 pi-knowledge，索引 `knowledge/`
- custom tools：`search_knowledge` / `get_user_stats`
- `POST /plan` + Streamlit 学习规划入口
- 放入至少 3 份示例资料

---

## 阶段 3：知识库与个人规划

**状态**：已完成  
**日期**：2026-11-13

### 完成了什么

1. 安装 `pi-knowledge@0.14.0`，索引 `knowledge/` 下 3 份示例资料到 `data/knowledge-index/`（KB：`kaogong-methods`，14 chunks）。
2. custom tools：`search_knowledge`、`get_user_stats`；system prompt 要求规划前先统计、再检索。
3. Agent 接口：`POST /plan`，以及辅助 `/stats`、`/search-knowledge`、`/index-knowledge`。
4. Streamlit 增加「学习规划」页：选天数 → 生成 → 展示；失败提示不影响做题。
5. 示例资料：
   - `knowledge/01-xingce-overview.md`
   - `knowledge/02-ziliaofenxi-methods.md`
   - `knowledge/03-study-plan-template.md`

### 如何验证

```bash
cd agent
npm run index:knowledge
npm run verify:stage3
curl -s http://127.0.0.1:8787/stats
curl -s -X POST http://127.0.0.1:8787/plan \
  -H 'Content-Type: application/json' -d '{"days":7}'
```

### 验收结果（已跑通）

- `search_knowledge("资料分析正确率偏低 怎么练")` 命中 `02-ziliaofenxi-methods.md` 等片段。
- `/plan` 规划明确引用真实统计：资料分析 25%、言语 46.67%、总正确率 39.13%。
- Streamlit `fetch_study_plan` 成功；Agent 关闭时提示「学习规划暂不可用」，批分仍正常。

### 遇到的问题

1. npm `allowScripts` 默认拦截 `better-sqlite3` 等原生模块；需 `npm install-scripts approve ...` 后 `npm rebuild better-sqlite3`。
2. 首次本地 embedding 索引约 6 分钟（下载/加载模型）；后续复用索引。
3. `pi-knowledge` 不导出根路径 `engine`，实际从 `pi-knowledge/dist/src/engine.js` 导入 `KnowledgeEngine`。

### 遗留问题

- 规划生成耗时随模型而定（约 1–2 分钟），可接受于自用场景。
- 知识库目前仅方法论笔记；后续可继续往 `knowledge/` 追加并 `npm run index:knowledge`。

---

## 增强迭代（阶段 3 后）

**日期**：2026-11-13

### 完成了什么

1. **申论题库**：`scripts/prepare_shenlun.py` 从开源 SLB 导入 `data/shenlun.db`（344 套；河南 12 / 国考相关 34）。
2. **计时自动刷新**：套卷计时改为 `@st.fragment(run_every=1)`，无需用户操作也会每秒更新。
3. **错题本**：`wrong_book` 持久化；交卷自动入库；支持笔记、标记已掌握、按模块筛选、按需 AI。
4. **模块分离**：侧边栏「行测 / 申论」两大模块独立；行测含做题/阅卷/错题本/规划；申论独立开卷作答（不做自动评分）。

### 验证

- `prepare_shenlun.py` 成功；`list_shenlun_papers(True)` 返回 46 套重点卷。
- 交卷后 `wrong_book` 自动写入，笔记可保存。
- Streamlit / Agent 健康检查通过。

---

## 做题体验优化（答题卡 / 计时浮窗 / 草稿 / 性能）

**日期**：2026-11-13

### 完成了什么

1. **浮动计时弹窗**：右下角迷你计时，下滑离开主计时区后仍每秒刷新提醒。
2. **图片尺寸修复**：区分 `公式图`/小符号图与题干大图；公式按原始宽度显示，不再被拉满整行。
3. **答题卡**：显示已答/未答，点击跳题；改为**单题作答**模式。
4. **实时草稿**：答案写入 `user.db.quiz_drafts`；关闭页面后可恢复，交卷后清除。
5. **单题计时**：进入题目开始累计，切到下一题结算；并按题型汇总平均用时。
6. **性能**：不再一次渲染整卷所有题+图，显著降低卡顿。
7. **交卷反馈**：成功 toast + success 条 + 自动提示查看「阅卷结果」。
