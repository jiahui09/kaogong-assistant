import http from "node:http";
import {
  chatCoach,
  explainOne,
  explainWrongAnswers,
  makeStudyPlan,
} from "./agent.ts";
import { ensureKnowledgeIndexed, searchKnowledge } from "./knowledge.ts";
import { getUserStats } from "./tools.ts";

const HOST = process.env.KAOGONG_AGENT_HOST || "127.0.0.1";
const PORT = Number(process.env.KAOGONG_AGENT_PORT || 8787);

type Json = Record<string, unknown>;

function sendJson(
  res: http.ServerResponse,
  status: number,
  body: Json | unknown
) {
  const data = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(data),
  });
  res.end(data);
}

async function readJson(req: http.IncomingMessage): Promise<Json> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  const raw = Buffer.concat(chunks).toString("utf8").trim();
  if (!raw) return {};
  return JSON.parse(raw) as Json;
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url || "/", `http://${HOST}:${PORT}`);
    if (req.method === "GET" && url.pathname === "/health") {
      sendJson(res, 200, { ok: true, service: "kaogong-agent" });
      return;
    }

    if (req.method === "POST" && url.pathname === "/explain-wrong") {
      const body = await readJson(req);
      const submissionId = Number(body.submission_id);
      if (!Number.isFinite(submissionId) || submissionId <= 0) {
        sendJson(res, 400, { error: "submission_id 必须是正整数" });
        return;
      }
      const explanations = await explainWrongAnswers(submissionId);
      sendJson(res, 200, { explanations });
      return;
    }

    if (req.method === "POST" && url.pathname === "/explain-one") {
      // 一次性内存会话按需解析，不污染其他请求的历史。
      const body = await readJson(req);
      const questionId = Number(body.question_id);
      const userAnswer = String(body.user_answer ?? "");
      if (!Number.isFinite(questionId) || questionId <= 0) {
        sendJson(res, 400, { error: "question_id 必须是正整数" });
        return;
      }
      const text = await explainOne(questionId, userAnswer);
      sendJson(res, 200, { text });
      return;
    }

    if (req.method === "POST" && url.pathname === "/plan") {
      const body = await readJson(req);
      const days = Number(body.days ?? 7);
      if (!Number.isFinite(days) || days <= 0) {
        sendJson(res, 400, { error: "days 必须是正数" });
        return;
      }
      const plan = await makeStudyPlan(days);
      sendJson(res, 200, { plan });
      return;
    }

    if (req.method === "POST" && url.pathname === "/chat") {
      const body = await readJson(req);
      const message = String(body.message ?? "").trim();
      if (!message) {
        sendJson(res, 400, { error: "message 不能为空" });
        return;
      }
      const reply = await chatCoach(message);
      sendJson(res, 200, { reply });
      return;
    }

    // 调试/验收辅助接口（知识库不可用时不影响做题主流程）
    if (req.method === "GET" && url.pathname === "/stats") {
      sendJson(res, 200, getUserStats());
      return;
    }

    if (req.method === "POST" && url.pathname === "/search-knowledge") {
      const body = await readJson(req);
      const query = String(body.query || "");
      const result = await searchKnowledge(query, Number(body.limit || 5));
      sendJson(res, result.ok ? 200 : 500, result);
      return;
    }

    if (req.method === "POST" && url.pathname === "/index-knowledge") {
      const info = await ensureKnowledgeIndexed((msg) =>
        console.log("[knowledge]", msg)
      );
      sendJson(res, 200, info);
      return;
    }

    sendJson(res, 404, { error: "not found" });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error("[agent-server]", message);
    sendJson(res, 500, { error: message });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`[kaogong-agent] listening on http://${HOST}:${PORT}`);
});
