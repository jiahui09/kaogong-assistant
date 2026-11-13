import path from "node:path";
import { KnowledgeEngine } from "pi-knowledge/dist/src/engine.js";
import { ROOT } from "./paths.ts";

export const KNOWLEDGE_DIR = path.join(ROOT, "data", "knowledge-index");
export const KNOWLEDGE_SOURCE_DIR = path.join(ROOT, "knowledge");
export const KB_NAME = "kaogong-methods";

let enginePromise: Promise<KnowledgeEngine> | null = null;

export async function getKnowledgeEngine(): Promise<KnowledgeEngine> {
  if (!enginePromise) {
    enginePromise = (async () => {
      const engine = new KnowledgeEngine();
      await engine.initialize(KNOWLEDGE_DIR);
      return engine;
    })().catch((err) => {
      // 初始化失败不永久缓存 rejected Promise，后续请求可重试。
      enginePromise = null;
      throw err;
    });
  }
  return enginePromise;
}

type IndexInfo = { kbName: string; chunkCount: number; reused: boolean };
let indexPromise: Promise<IndexInfo> | null = null;

export function ensureKnowledgeIndexed(
  onProgress?: (msg: string) => void
): Promise<IndexInfo> {
  // 冷启动并发请求共享一次索引任务；结束后清理，失败允许重试。
  if (!indexPromise) {
    indexPromise = indexKnowledge(onProgress).finally(() => {
      indexPromise = null;
    });
  }
  return indexPromise;
}

async function indexKnowledge(onProgress?: (msg: string) => void): Promise<IndexInfo> {
  const engine = await getKnowledgeEngine();
  const existing = engine.list().find((kb) => kb.name === KB_NAME);
  if (existing && (existing.chunk_count || 0) > 0) {
    onProgress?.(`复用已有知识库 ${KB_NAME}（chunks=${existing.chunk_count}）`);
    return {
      kbName: KB_NAME,
      chunkCount: existing.chunk_count || 0,
      reused: true,
    };
  }
  if (existing) {
    engine.remove(KB_NAME);
  }
  onProgress?.(`开始索引 ${KNOWLEDGE_SOURCE_DIR} ...`);
  const { kb, chunkCount } = await engine.add(
    KNOWLEDGE_SOURCE_DIR,
    KB_NAME,
    onProgress
  );
  return { kbName: kb.name, chunkCount, reused: false };
}

export type KnowledgeHit = {
  score: number;
  file_path: string;
  snippet: string;
  content: string;
  kb_name: string;
};

export async function searchKnowledge(
  query: string,
  limit = 5
): Promise<{ ok: true; hits: KnowledgeHit[] } | { ok: false; error: string }> {
  try {
    const text = query.trim();
    if (!text) return { ok: true, hits: [] };
    const safeLimit = Number.isFinite(limit) ? Math.max(1, Math.min(10, Math.floor(limit))) : 5;
    const engine = await getKnowledgeEngine();
    await ensureKnowledgeIndexed();
    const resp = await engine.search(text, {
      mode: "hybrid",
      profile: "docs",
      limit: safeLimit,
      kb_id: KB_NAME,
    });
    const hits: KnowledgeHit[] = (resp.results || []).map((r) => ({
      score: r.score,
      file_path: r.file_path,
      snippet: r.snippet,
      content: r.content,
      kb_name: r.kb_name,
    }));
    return { ok: true, hits };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { ok: false, error: message };
  }
}
