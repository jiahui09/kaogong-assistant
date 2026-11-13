import assert from "node:assert/strict";
import { performance } from "node:perf_hooks";
import { pathToFileURL } from "node:url";
import { getUserStats, getSubmissionAnswersById, type DatabasePaths } from "./tools.ts";

// baseline tools.ts 由 Python 驱动从指定 Git 提交导出到临时目录。
const [baselineFile, userFile, questionsFile] = process.argv.slice(2);
if (!baselineFile || !userFile || !questionsFile) {
  throw new Error("请运行 scripts/benchmark_performance.py");
}
const baseline = await import(pathToFileURL(baselineFile).href);
const paths: DatabasePaths = { user: userFile, questions: questionsFile };
const iterations = Number(process.env.BENCH_ITERATIONS || 9);
if (!Number.isInteger(iterations) || iterations < 3) throw new Error("BENCH_ITERATIONS 必须 >= 3");
function measure(fn: () => unknown) {
  const times: number[] = [];
  const firstStart = performance.now();
  const result = fn();
  const firstMs = performance.now() - firstStart;
  for (let i = 0; i < iterations; i++) {
    const start = performance.now();
    fn();
    times.push(performance.now() - start);
  }
  times.sort((a, b) => a - b);
  return { result, timing: {
    first_ms: firstMs, median_ms: times[Math.floor(times.length / 2)],
    p95_ms: times[Math.ceil(times.length * 0.95) - 1], samples: times.length,
  } };
}
const output: Record<string, unknown> = {};
for (const [name, before, after] of [
  ["stats", () => baseline.getUserStats(), () => getUserStats(paths)],
  ["submission_1", () => baseline.getSubmissionAnswersById(1), () => getSubmissionAnswersById(1, paths)],
] as const) {
  const a = measure(before);
  const b = measure(after);
  assert.deepEqual(JSON.parse(JSON.stringify(b.result)), JSON.parse(JSON.stringify(a.result)), `${name} 语义发生变化`);
  output[name] = { before: a.timing, after: b.timing, speedup: a.timing.median_ms / b.timing.median_ms, equivalent: true };
}
console.log(JSON.stringify(output));
