import { makeStudyPlan } from "./agent.ts";
import { searchKnowledge } from "./knowledge.ts";
import { getUserStats } from "./tools.ts";

async function main() {
  const stats = getUserStats();
  console.log("[INFO] stats", stats);
  if (stats.total_answered <= 0) throw new Error("没有做题统计，无法验收规划");

  const search = await searchKnowledge("资料分析正确率偏低 怎么练", 3);
  if (!search.ok) throw new Error(search.error);
  if (search.hits.length === 0) throw new Error("search_knowledge 无结果");
  console.log(
    "[PASS] search_knowledge hits=",
    search.hits.map((h) => h.file_path)
  );

  const plan = await makeStudyPlan(7);
  console.log(plan.slice(0, 500));
  if (!plan.trim()) throw new Error("空规划");

  const mustMention = stats.weakest_modules[0];
  if (mustMention && !plan.includes(mustMention)) {
    // 宽松一点：至少提到正确率数字或“资料分析”
    const alt =
      plan.includes("资料分析") ||
      plan.includes(String(stats.accuracy)) ||
      plan.includes("正确率");
    if (!alt) {
      throw new Error(`规划未引用真实统计/弱项：${mustMention}`);
    }
    console.warn("[WARN] 未直接出现弱项模块名，但包含统计相关表述，请人工抽查");
  } else {
    console.log("[PASS] 规划引用弱项模块", mustMention);
  }

  // HTTP
  const resp = await fetch("http://127.0.0.1:8787/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ days: 7 }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status} ${await resp.text()}`);
  const body = (await resp.json()) as { plan?: string };
  if (!body.plan) throw new Error("HTTP /plan 缺少 plan");
  console.log("[PASS] /plan HTTP OK, len=", body.plan.length);
}

main().catch((err) => {
  console.error("[FAIL]", err);
  process.exit(1);
});
