import { ensureKnowledgeIndexed, searchKnowledge } from "./knowledge.ts";

async function main() {
  const info = await ensureKnowledgeIndexed((msg) => console.log(msg));
  console.log("[OK] indexed", info);

  const probe = await searchKnowledge("资料分析正确率偏低 怎么练", 3);
  console.log(JSON.stringify(probe, null, 2));
  if (!probe.ok || probe.hits.length === 0) {
    throw new Error("search_knowledge 未返回相关片段");
  }
  console.log("[PASS] knowledge search");
}

main().catch((err) => {
  console.error("[FAIL]", err);
  process.exit(1);
});
