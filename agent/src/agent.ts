import fs from "node:fs";
import {
  createAgentSession,
  DefaultResourceLoader,
  getAgentDir,
  SessionManager,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import {
  customTools,
  getSubmissionAnswersById,
  getUserStats,
  getRecentChat,
} from "./tools.ts";
import { searchKnowledge } from "./knowledge.ts";
import { ROOT, SYSTEM_PROMPT_PATH } from "./paths.ts";

export type ExplanationItem = {
  question_id: number;
  text: string;
};

function loadSystemPrompt(): string {
  return fs.readFileSync(SYSTEM_PROMPT_PATH, "utf8");
}

async function createKaogongSession(options?: {
  extraTools?: ToolDefinition[];
}) {
  const agentDir = getAgentDir();
  const systemPrompt = loadSystemPrompt();
  const loader = new DefaultResourceLoader({
    cwd: ROOT,
    agentDir,
    systemPromptOverride: () => systemPrompt,
    appendSystemPromptOverride: () => [],
    noExtensions: true,
    noSkills: true,
    noPromptTemplates: true,
    noThemes: true,
  });
  await loader.reload();

  const { session } = await createAgentSession({
    cwd: ROOT,
    agentDir,
    resourceLoader: loader,
    sessionManager: SessionManager.inMemory(ROOT),
    // 不做代码编辑；只保留自定义题库工具
    noTools: "builtin",
    customTools: [...customTools, ...(options?.extraTools || [])],
  });
  return session;
}

function parseExplanations(text: string, fallbackIds: number[]): ExplanationItem[] {
  const items: ExplanationItem[] = [];
  const re =
    /<<<QUESTION_ID:(\d+)>>>\s*([\s\S]*?)<<<END>>>/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    items.push({
      question_id: Number(m[1]),
      text: m[2].trim(),
    });
  }
  if (items.length > 0) return items;

  // 模型未按格式输出时：整段文本挂到每道错题（至少不丢内容）
  const body = text.trim();
  if (!body) return [];
  if (fallbackIds.length === 1) {
    return [{ question_id: fallbackIds[0], text: body }];
  }
  return fallbackIds.map((id) => ({
    question_id: id,
    text: body,
  }));
}

/**
 * 对某次交卷的错题批量生成 AI 解析。
 * 每次调用使用独立内存会话，避免污染历史。
 */
export async function explainWrongAnswers(
  submissionId: number
): Promise<ExplanationItem[]> {
  const answers = getSubmissionAnswersById(submissionId);
  if (answers.length === 0) {
    throw new Error(`submission_id=${submissionId} 不存在或没有作答记录`);
  }
  const wrong = answers.filter((a) => !a.is_correct);
  if (wrong.length === 0) {
    return [];
  }

  const session = await createKaogongSession();
  try {
    const wrongList = wrong
      .map(
        (w, i) =>
          `${i + 1}. question_id=${w.question_id}；用户答案=${w.user_answer}；正确答案=${w.correct_answer || "?"}`
      )
      .join("\n");

    const prompt = `请解析 submission_id=${submissionId} 的全部错题。

步骤要求：
1. 先调用 get_submission_answers(${submissionId}) 确认错题列表；
2. 对每道错题调用 get_question(question_id) 获取真实题目后再解析；
3. 严格按 system prompt 中的 <<<QUESTION_ID:n>>> ... <<<END>>> 格式输出。

已知错题概览（仍须用工具核对）：
${wrongList}`;

    await session.prompt(prompt);
    const text = session.getLastAssistantText() || "";
    return parseExplanations(
      text,
      wrong.map((w) => w.question_id)
    );
  } finally {
    session.dispose();
  }
}

/**
 * 单题按需解析（一次性内存会话）。
 */
export async function explainOne(
  questionId: number,
  userAnswer: string
): Promise<string> {
  const session = await createKaogongSession();
  try {
    const prompt = `请解析下面这道题。

要求：
1. 必须先调用 get_question(${questionId}) 获取真实题目、选项、正确答案和原始解析；
2. 用户答案是：${userAnswer || "(未作答)"}；
3. 说明对错原因、考查点与解题思路；
4. 直接输出解析正文，不要使用批量分隔符。`;

    await session.prompt(prompt);
    return (session.getLastAssistantText() || "").trim();
  } finally {
    session.dispose();
  }
}

/**
 * 基于用户统计 + 知识库检索生成学习规划。
 */
export async function makeStudyPlan(days: number): Promise<string> {
  const safeDays = Math.max(1, Math.min(180, Math.floor(days || 7)));
  const stats = getUserStats();
  // 确定性预取：统计/档案/对话直接读库，检索与会话初始化并行，
  // 避免模型用三轮网络往返调度已知的读取步骤。
  const memory = { profile: stats.learner_profile, recent_chat: getRecentChat(8) };
  const [knowledge, session] = await Promise.all([
    searchKnowledge(`${stats.weakest_modules.join(" ")} ${stats.learner_profile?.target_exam || "国考 河南"} 学习计划 备考方法`, 3),
    createKaogongSession(),
  ]);
  try {
    const prompt = `请为用户生成一份 ${safeDays} 天的考公学习规划（行测为主，可兼顾申论方法）。

服务端已执行本轮可信预取（满足 system 中的数据读取与检索要求），直接据此生成规划；仅在信息不足时补充工具查询。
学习者档案与近期对话：${JSON.stringify(memory)}
知识库检索结果：${JSON.stringify(knowledge)}
真实统计：${JSON.stringify(stats)}

已知统计摘要：
- 总做题 ${stats.total_answered}，正确率 ${stats.accuracy}% ，错题 ${stats.wrong_count}
- 练习交卷 ${stats.practice_submissions}，套卷交卷 ${stats.exam_submissions}，套卷平均用时 ${stats.avg_exam_duration_sec ?? "未知"} 秒
- 弱项模块：${stats.weakest_modules.join("、") || "暂无"}
- 各模块：${stats.modules
      .map((m) => `${m.module}${m.accuracy}%(${m.correct}/${m.total})`)
      .join("；") || "暂无"}
- 档案目标：${stats.learner_profile?.target_exam || "未知" }；重点地区：${stats.learner_profile?.focus_regions || "未知"}

计划要求：
- 明确点出用户真实弱项与套卷用时问题（不要泛泛而谈）
- 国考/河南省考可分别给出侧重点
- 给出可执行的每日/每周任务与验收标准
- 使用简体中文 Markdown`;

    await session.prompt(prompt);
    return (session.getLastAssistantText() || "").trim();
  } finally {
    session.dispose();
  }
}

/**
 * 针对性对话辅导（读取统计 + 档案 + 近期对话 + 知识库）。
 */
export async function chatCoach(message: string): Promise<string> {
  const text = (message || "").trim();
  if (!text) throw new Error("message 不能为空");
  const stats = getUserStats();
  const session = await createKaogongSession();
  try {
    const prompt = `你是考公一对一辅导助手。请回答用户问题，并给出可执行建议。

硬性步骤：
1. 调用 get_user_stats()；
2. 调用 get_learner_memory()；
3. 视需要调用 search_knowledge()（方法论、申论框架、河南/国考差异等）；
4. 结合真实数据回答，不要空谈。

用户问题：
${text}

已知摘要（仍须工具核对）：
- 正确率 ${stats.accuracy}% ，弱项：${stats.weakest_modules.join("、") || "暂无"}
- 目标：${stats.learner_profile?.target_exam || "未知"}；重点：${stats.learner_profile?.focus_regions || "未知"}

输出要求：简体中文，先给结论，再给分步建议。`;

    await session.prompt(prompt);
    return (session.getLastAssistantText() || "").trim();
  } finally {
    session.dispose();
  }
}
