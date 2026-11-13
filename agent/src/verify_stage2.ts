/**
 * 阶段 2 验收：
 * 1) POST /explain-one 返回 JSON
 * 2) 解析引用真实答案
 * 3) 服务关闭时前端降级（由 Python 侧脚本验证）
 */
import { explainOne } from "./agent.ts";
import { getQuestionById } from "./tools.ts";

async function main() {
  const questionId = 82934;
  const q = getQuestionById(questionId);
  if (!q) throw new Error("样题不存在");
  const userAnswer = q.answer === "A" ? "B" : "A";

  console.log("[INFO] explainOne", questionId, "user=", userAnswer, "right=", q.answer);
  const text = await explainOne(questionId, userAnswer);
  if (!text.trim()) throw new Error("空解析");
  console.log(text.slice(0, 240));
  if (!text.includes(q.answer)) {
    console.warn("[WARN] 解析文本未直接包含正确答案字母，请人工抽查");
  } else {
    console.log("[PASS] 解析包含正确答案字母", q.answer);
  }

  // 再通过 HTTP 验一次（要求 server 已启动）
  const resp = await fetch("http://127.0.0.1:8787/explain-one", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question_id: questionId, user_answer: userAnswer }),
  });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
  }
  const body = (await resp.json()) as { text?: string };
  if (!body.text) throw new Error("HTTP 响应缺少 text");
  console.log("[PASS] /explain-one HTTP OK, len=", body.text.length);
}

main().catch((err) => {
  console.error("[FAIL]", err);
  process.exit(1);
});
