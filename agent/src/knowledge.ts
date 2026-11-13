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
    })();
  }
  return enginePromise;
}

export async function ensureKnowledgeIndexed(
  onProgress?: (msg: string) => void
): Promise<{ kbName: string; chunkCount: number; reused: boolean }> {
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
    const engine = await getKnowledgeEngine();
    await ensureKnowledgeIndexed();
    const resp = await engine.search(query, {
      mode: "hybrid",
      profile: "docs",
      limit,
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
