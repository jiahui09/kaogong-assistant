import { DatabaseSync } from "node:sqlite";
import { Type } from "typebox";
import { defineTool } from "@earendil-works/pi-coding-agent";
import { searchKnowledge } from "./knowledge.ts";
import { KAOGONG_DB, USER_DB } from "./paths.ts";

type Option = {
  key?: string;
  label?: string;
  html?: string;
  is_answer?: boolean;
};

function openReadonly(dbPath: string): DatabaseSync {
  return new DatabaseSync(dbPath, { readOnly: true });
}

function stripHtml(text: string | null | undefined): string {
  if (!text) return "";
  return text.replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim();
}

function parseOptions(raw: string | null | undefined): Option[] {
  if (!raw) return [];
  try {
    const data = JSON.parse(raw);
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

export type QuestionRecord = {
  question_id: number;
  qid: string;
  module: string;
  category: string;
  kadian: string;
  region: string;
  year: string;
  stem: string;
  options: Array<{ key: string; label: string }>;
  answer: string;
  analysis: string;
  material: string;
};

export type SubmissionAnswerRecord = {
  question_id: number;
  user_answer: string;
  is_correct: boolean;
  stem?: string;
  correct_answer?: string;
  module?: string;
  category?: string;
};

export function getQuestionById(questionId: number): QuestionRecord | null {
  const db = openReadonly(KAOGONG_DB);
  try {
    const row = db
      .prepare(
        `SELECT id, qid, module, category, kadian, region, year, stem, options, answer, analysis, material
         FROM questions WHERE id = ?`
      )
      .get(questionId) as
      | {
          id: number;
          qid: string;
          module: string;
          category: string;
          kadian: string;
          region: string;
          year: string;
          stem: string;
          options: string;
          answer: string;
          analysis: string;
          material: string;
        }
      | undefined;
    if (!row) return null;
    const options = parseOptions(row.options).map((o) => ({
      key: String(o.key || "").toUpperCase(),
      label: stripHtml(o.label || o.html || ""),
    }));
    return {
      question_id: row.id,
      qid: row.qid,
      module: row.module || "",
      category: row.category || "",
      kadian: row.kadian || "",
      region: row.region || "",
      year: row.year || "",
      stem: stripHtml(row.stem),
      options,
      answer: String(row.answer || "").toUpperCase(),
      analysis: stripHtml(row.analysis),
      material: stripHtml(row.material),
    };
  } finally {
    db.close();
  }
}

export function getSubmissionAnswersById(
  submissionId: number
): SubmissionAnswerRecord[] {
  const userDb = openReadonly(USER_DB);
  let answers: Array<{
    question_id: number;
    user_answer: string;
    is_correct: number;
  }> = [];
  try {
    answers = userDb
      .prepare(
        `SELECT question_id, user_answer, is_correct
         FROM answers WHERE submission_id = ? ORDER BY id`
      )
      .all(submissionId) as Array<{
      question_id: number;
      user_answer: string;
      is_correct: number;
    }>;
  } finally {
    userDb.close();
  }

  return answers.map((a) => {
    const q = getQuestionById(a.question_id);
    return {
      question_id: a.question_id,
      user_answer: a.user_answer,
      is_correct: Boolean(a.is_correct),
      stem: q?.stem,
      correct_answer: q?.answer,
      module: q?.module,
      category: q?.category,
    };
  });
}

function textResult(payload: unknown) {
  return {
    content: [{ type: "text" as const, text: JSON.stringify(payload, null, 2) }],
    details: {},
  };
}

export const getQuestionTool = defineTool({
  name: "get_question",
  label: "Get Question",
  description:
    "从公考脑库只读查询一道题的题干、选项、正确答案和原始解析。解析错题前必须先调用本工具。",
  promptSnippet: "get_question(question_id) 读取真实题目内容",
  promptGuidelines: [
    "解析错题前必须先调用 get_question，禁止编造题干/选项/答案。",
  ],
  parameters: Type.Object({
    question_id: Type.Number({ description: "questions.id" }),
  }),
  async execute(_toolCallId, params) {
    const q = getQuestionById(Number(params.question_id));
    if (!q) {
      return textResult({ error: `question_id=${params.question_id} 不存在` });
    }
    return textResult(q);
  },
});

export const getSubmissionAnswersTool = defineTool({
  name: "get_submission_answers",
  label: "Get Submission Answers",
  description:
    "从 user.db 查询某次交卷的全部作答：question_id、用户答案、是否正确，并附带题干摘要。",
  promptSnippet: "get_submission_answers(submission_id) 读取本次交卷作答",
  parameters: Type.Object({
    submission_id: Type.Number({ description: "submissions.id" }),
  }),
  async execute(_toolCallId, params) {
    const rows = getSubmissionAnswersById(Number(params.submission_id));
    return textResult({
      submission_id: params.submission_id,
      total: rows.length,
      wrong_count: rows.filter((r) => !r.is_correct).length,
      answers: rows,
    });
  },
});

export type ModuleStat = {
  module: string;
  total: number;
  correct: number;
  accuracy: number;
  wrong: number;
};

export type UserStats = {
  total_answered: number;
  total_correct: number;
  accuracy: number;
  wrong_count: number;
  submissions: number;
  exam_submissions: number;
  practice_submissions: number;
  avg_exam_duration_sec: number | null;
  modules: ModuleStat[];
  weakest_modules: string[];
  recent_exams: Array<{
    id: number;
    paper: string | null;
    region: string | null;
    year: string | null;
    score: number;
    duration_sec: number | null;
    timed_limit_sec: number | null;
  }>;
  learner_profile: {
    display_name: string;
    target_exam: string;
    focus_regions: string;
    notes: string;
  } | null;
};

export function getLearnerProfile() {
  const userDb = openReadonly(USER_DB);
  try {
    const row = userDb
      .prepare(
        `SELECT display_name, target_exam, focus_regions, notes FROM learner_profile WHERE id=1`
      )
      .get() as
      | {
          display_name: string;
          target_exam: string;
          focus_regions: string;
          notes: string;
        }
      | undefined;
    return row || null;
  } finally {
    userDb.close();
  }
}

export function getRecentChat(limit = 12) {
  const userDb = openReadonly(USER_DB);
  try {
    const rows = userDb
      .prepare(
        `SELECT role, content, created_at FROM chat_messages ORDER BY id DESC LIMIT ?`
      )
      .all(limit) as Array<{ role: string; content: string; created_at: string }>;
    return rows.reverse();
  } catch {
    return [];
  } finally {
    userDb.close();
  }
}

export function getUserStats(): UserStats {
  const userDb = openReadonly(USER_DB);
  let answerRows: Array<{ question_id: number; is_correct: number }> = [];
  let submissions = 0;
  let examSubmissions = 0;
  let practiceSubmissions = 0;
  let avgExamDuration: number | null = null;
  let recentExams: UserStats["recent_exams"] = [];
  let profile: UserStats["learner_profile"] = null;
  try {
    answerRows = userDb
      .prepare(`SELECT question_id, is_correct FROM answers`)
      .all() as Array<{ question_id: number; is_correct: number }>;
    submissions = Number(
      (
        userDb.prepare(`SELECT COUNT(*) AS n FROM submissions`).get() as {
          n: number;
        }
      ).n || 0
    );
    try {
      examSubmissions = Number(
        (
          userDb
            .prepare(
              `SELECT COUNT(*) AS n FROM submissions WHERE mode='exam'`
            )
            .get() as { n: number }
        ).n || 0
      );
      practiceSubmissions = Number(
        (
          userDb
            .prepare(
              `SELECT COUNT(*) AS n FROM submissions WHERE mode!='exam' OR mode IS NULL`
            )
            .get() as { n: number }
        ).n || 0
      );
      const avgRow = userDb
        .prepare(
          `SELECT AVG(duration_sec) AS avg_sec FROM submissions WHERE mode='exam' AND duration_sec IS NOT NULL`
        )
        .get() as { avg_sec: number | null };
      avgExamDuration =
        avgRow?.avg_sec == null ? null : Number(avgRow.avg_sec.toFixed(1));
      recentExams = userDb
        .prepare(
          `SELECT id, paper, region, year, score, duration_sec, timed_limit_sec
           FROM submissions WHERE mode='exam'
           ORDER BY id DESC LIMIT 5`
        )
        .all() as UserStats["recent_exams"];
      const p = userDb
        .prepare(
          `SELECT display_name, target_exam, focus_regions, notes FROM learner_profile WHERE id=1`
        )
        .get() as UserStats["learner_profile"] | undefined;
      profile = p || null;
    } catch {
      // 旧库尚未迁移时降级
    }
  } finally {
    userDb.close();
  }

  const totalAnswered = answerRows.length;
  const totalCorrect = answerRows.filter((a) => a.is_correct).length;
  const wrongCount = totalAnswered - totalCorrect;

  const byModule = new Map<string, { total: number; correct: number }>();
  for (const a of answerRows) {
    const q = getQuestionById(a.question_id);
    const module = q?.module || "未知模块";
    const cur = byModule.get(module) || { total: 0, correct: 0 };
    cur.total += 1;
    if (a.is_correct) cur.correct += 1;
    byModule.set(module, cur);
  }

  const modules: ModuleStat[] = [...byModule.entries()]
    .map(([module, v]) => ({
      module,
      total: v.total,
      correct: v.correct,
      wrong: v.total - v.correct,
      accuracy: v.total ? Number(((100 * v.correct) / v.total).toFixed(2)) : 0,
    }))
    .sort((a, b) => a.accuracy - b.accuracy || b.total - a.total);

  const weakest = modules
    .filter((m) => m.total >= 1)
    .slice(0, 3)
    .map((m) => m.module);

  return {
    total_answered: totalAnswered,
    total_correct: totalCorrect,
    accuracy: totalAnswered
      ? Number(((100 * totalCorrect) / totalAnswered).toFixed(2))
      : 0,
    wrong_count: wrongCount,
    submissions,
    exam_submissions: examSubmissions,
    practice_submissions: practiceSubmissions,
    avg_exam_duration_sec: avgExamDuration,
    modules,
    weakest_modules: weakest,
    recent_exams: recentExams,
    learner_profile: profile,
  };
}

export const getUserStatsTool = defineTool({
  name: "get_user_stats",
  label: "Get User Stats",
  description:
    "从 user.db 汇总总做题数、正确率、各模块正确率、错题数量。生成学习规划前必须先调用。",
  promptSnippet: "get_user_stats() 读取个人做题统计",
  promptGuidelines: [
    "生成学习规划时必须先调用 get_user_stats，计划中要引用真实正确率/弱项模块。",
  ],
  parameters: Type.Object({}),
  async execute() {
    return textResult(getUserStats());
  },
});

export const searchKnowledgeTool = defineTool({
  name: "search_knowledge",
  label: "Search Knowledge",
  description:
    "检索考公方法论知识库（Markdown 资料）。解析方法论、生成学习规划时优先调用。",
  promptSnippet: "search_knowledge(query) 检索备考方法论",
  promptGuidelines: [
    "生成规划或讲解方法论时，优先 search_knowledge 再组织回答。",
  ],
  parameters: Type.Object({
    query: Type.String({ description: "检索词，如：资料分析正确率偏低 怎么练" }),
    limit: Type.Optional(
      Type.Number({ description: "返回条数，默认 5", minimum: 1, maximum: 10 })
    ),
  }),
  async execute(_toolCallId, params) {
    const result = await searchKnowledge(
      String(params.query || ""),
      Number(params.limit || 5)
    );
    return textResult(result);
  },
});

export const getLearnerMemoryTool = defineTool({
  name: "get_learner_memory",
  label: "Get Learner Memory",
  description:
    "读取学习者持久化档案、最近对话摘要，用于给出针对性建议。",
  promptSnippet: "get_learner_memory() 读取档案与近期对话",
  parameters: Type.Object({
    chat_limit: Type.Optional(
      Type.Number({ description: "最近对话条数，默认 8", minimum: 1, maximum: 30 })
    ),
  }),
  async execute(_toolCallId, params) {
    return textResult({
      profile: getLearnerProfile(),
      recent_chat: getRecentChat(Number(params.chat_limit || 8)),
    });
  },
});

export const customTools = [
  getQuestionTool,
  getSubmissionAnswersTool,
  getUserStatsTool,
  searchKnowledgeTool,
  getLearnerMemoryTool,
];
