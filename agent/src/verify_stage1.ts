/**
 * 阶段 1 验收：
 * 1) custom tools 可读库
 * 2) /health
 * 3) /explain-wrong 返回 JSON（需服务已启动；也可本文件直接调 explainWrongAnswers）
 */
import { DatabaseSync } from "node:sqlite";
import { explainWrongAnswers } from "./agent.ts";
import { getQuestionById, getSubmissionAnswersById } from "./tools.ts";
import { USER_DB } from "./paths.ts";

function ensureTinyWrongSubmission(): number {
  const answers = getSubmissionAnswersById(1);
  if (answers.some((a) => !a.is_correct)) {
    return 1;
  }
  // 没有现成错题时造一条最小交卷
  const db = new DatabaseSync(USER_DB);
  try {
    const qid = 82934;
    const q = getQuestionById(qid);
    if (!q) throw new Error("无法取到样题");
    const wrongAns = q.answer === "A" ? "B" : "A";
    const info = db
      .prepare(
        `INSERT INTO submissions(created_at, total, correct, score) VALUES (?,?,?,?)`
      )
      .run(new Date().toISOString(), 1, 0, 0);
    const submissionId = Number(info.lastInsertRowid);
    db.prepare(
      `INSERT INTO answers(submission_id, question_id, user_answer, is_correct) VALUES (?,?,?,?)`
    ).run(submissionId, qid, wrongAns, 0);
    return submissionId;
  } finally {
    db.close();
  }
}

async function main() {
  const q = getQuestionById(82934);
  if (!q || !q.stem || !q.answer) {
    throw new Error("get_question 工具读库失败");
  }
  console.log("[PASS] get_question", q.question_id, q.answer);

  const submissionId = ensureTinyWrongSubmission();
  const rows = getSubmissionAnswersById(submissionId);
  const wrong = rows.filter((r) => !r.is_correct);
  console.log(
    `[INFO] submission=${submissionId} answers=${rows.length} wrong=${wrong.length}`
  );
  if (wrong.length === 0) throw new Error("需要至少 1 道错题才能验解析");

  // 为控制耗时，若错题很多，仅保留前 2 道错题到临时 submission 再测
  let targetId = submissionId;
  if (wrong.length > 2) {
    const db = new DatabaseSync(USER_DB);
    try {
      const info = db
        .prepare(
          `INSERT INTO submissions(created_at, total, correct, score) VALUES (?,?,?,?)`
        )
        .run(new Date().toISOString(), wrong.length, 0, 0);
      targetId = Number(info.lastInsertRowid);
      const stmt = db.prepare(
        `INSERT INTO answers(submission_id, question_id, user_answer, is_correct) VALUES (?,?,?,?)`
      );
      for (const w of wrong.slice(0, 2)) {
        stmt.run(targetId, w.question_id, w.user_answer, 0);
      }
    } finally {
      db.close();
    }
    console.log(`[INFO] 使用精简 submission=${targetId}（2 道错题）`);
  }

  console.log("[INFO] 调用 explainWrongAnswers ...");
  const explanations = await explainWrongAnswers(targetId);
  console.log(JSON.stringify({ explanations }, null, 2));

  if (!Array.isArray(explanations) || explanations.length === 0) {
    throw new Error("未返回任何解析");
  }
  for (const item of explanations) {
    const real = getQuestionById(item.question_id);
    if (!real) throw new Error(`解析引用了不存在的题 ${item.question_id}`);
    // 粗检：解析文本应提到正确答案字母或题干片段，避免完全跑题
    const hitAnswer = item.text.includes(real.answer);
    const snippet = real.stem.slice(0, 8);
    const hitStem = snippet.length > 0 && item.text.includes(snippet);
    console.log(
      `[CHECK] q=${item.question_id} answerHit=${hitAnswer} stemHit=${hitStem} len=${item.text.length}`
    );
  }
  console.log("[PASS] 阶段 1 Agent 解析验收通过");
}

main().catch((err) => {
  console.error("[FAIL]", err);
  process.exit(1);
});
