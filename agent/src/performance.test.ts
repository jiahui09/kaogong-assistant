import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { test, mock } from "node:test";
import { KnowledgeEngine } from "pi-knowledge/dist/src/engine.js";
import { getUserStats, getSubmissionAnswersById } from "./tools.ts";
import { ensureKnowledgeIndexed, getKnowledgeEngine, searchKnowledge, KB_NAME } from "./knowledge.ts";

function fixture() {
  const directory = mkdtempSync(path.join(os.tmpdir(), "kaogong-perf-test-"));
  const paths = { user: path.join(directory, "user.db"), questions: path.join(directory, "questions.db") };
  const questions = new DatabaseSync(paths.questions);
  questions.exec(`CREATE TABLE questions (id INTEGER PRIMARY KEY, module TEXT, category TEXT, stem TEXT, answer TEXT);
    INSERT INTO questions VALUES (1,'资料分析','增长率','<p>真实题干</p>','b'),(2,'判断推理',NULL,'题目二','A'),(3,'',NULL,NULL,NULL);`);
  questions.close();
  const user = new DatabaseSync(paths.user);
  user.exec(`CREATE TABLE answers (id INTEGER PRIMARY KEY, submission_id INTEGER, question_id INTEGER, user_answer TEXT, is_correct INTEGER);
    CREATE INDEX ix_answers_submission ON answers(submission_id);
    CREATE TABLE submissions (id INTEGER PRIMARY KEY, mode TEXT, paper TEXT, region TEXT, year TEXT, score REAL, duration_sec INTEGER, timed_limit_sec INTEGER);
    CREATE TABLE learner_profile (id INTEGER PRIMARY KEY, display_name TEXT, target_exam TEXT, focus_regions TEXT, notes TEXT);
    INSERT INTO submissions VALUES (1,'exam','测试卷','河南','2026',50,101,7200),(2,NULL,NULL,NULL,NULL,0,NULL,NULL);
    INSERT INTO learner_profile VALUES (1,'测试考生','国考','河南','');
    INSERT INTO answers VALUES (1,1,1,'A',0),(2,1,2,'A',1),(3,1,1,'B',1),(4,1,999,'A',0),(5,1,3,'A',0);`);
  user.close();
  return { paths, cleanup: () => rmSync(directory, { recursive: true, force: true }) };
}

test("SQL 聚合保留重复作答、缺失题目、模块顺序与实时统计", () => {
  const f = fixture();
  try {
    const stats = getUserStats(f.paths);
    assert.equal(stats.total_answered, 5);
    assert.equal(stats.total_correct, 2);
    assert.equal(stats.accuracy, 40);
    assert.equal(stats.wrong_count, 3);
    assert.deepEqual(stats.modules, [
      { module: "未知模块", total: 2, correct: 0, wrong: 2, accuracy: 0 },
      { module: "资料分析", total: 2, correct: 1, wrong: 1, accuracy: 50 },
      { module: "判断推理", total: 1, correct: 1, wrong: 0, accuracy: 100 },
    ]);
    assert.deepEqual(stats.weakest_modules, ["未知模块", "资料分析", "判断推理"]);
    assert.equal(stats.submissions, 2);
    assert.equal(stats.exam_submissions, 1);
    assert.equal(stats.practice_submissions, 1);
    assert.equal(stats.avg_exam_duration_sec, 101);
    assert.equal(stats.recent_exams[0].paper, "测试卷");
    assert.equal(stats.learner_profile?.display_name, "测试考生");
    // 外部进程写入后可见，无缓存失效问题。
    const writer = new DatabaseSync(f.paths.user);
    writer.exec("INSERT INTO answers VALUES(6,1,2,'B',0)");
    writer.close();
    assert.equal(getUserStats(f.paths).total_answered, 6);
    const q = new DatabaseSync(f.paths.questions, { readOnly: true });
    assert.equal(q.prepare("SELECT COUNT(*) n FROM questions").get()?.n, 3);
    q.close();
  } finally { f.cleanup(); }
});

test("批量查询按作答顺序返回且不丢失缺题记录", () => {
  const f = fixture();
  try {
    const answers = getSubmissionAnswersById(1, f.paths);
    assert.deepEqual(answers.map(a => a.question_id), [1, 2, 1, 999, 3]);
    assert.equal(answers[0].stem, "真实题干");
    assert.equal(answers[0].correct_answer, "B");
    assert.equal(answers[1].is_correct, true);
    assert.equal(answers[1].category, "");
    assert.equal(answers[3].stem, undefined);
    assert.equal(answers[4].stem, "");
    assert.deepEqual(getSubmissionAnswersById(999, f.paths), []);
  } finally { f.cleanup(); }
});

test("旧库降级与空作答统计", () => {
  const f = fixture();
  try {
    const db = new DatabaseSync(f.paths.user);
    db.exec("DELETE FROM answers; DROP TABLE submissions; CREATE TABLE submissions (id INTEGER PRIMARY KEY); INSERT INTO submissions VALUES(1)");
    db.close();
    const stats = getUserStats(f.paths);
    assert.equal(stats.total_answered, 0);
    assert.equal(stats.accuracy, 0);
    assert.equal(stats.submissions, 1);
    assert.equal(stats.exam_submissions, 0);
    assert.deepEqual(stats.modules, []);
    assert.equal(stats.learner_profile, null);
  } finally { f.cleanup(); }
});

test("索引合并、失败重试与检索边界（mock，无模型加载或真实索引写入）", async () => {
  let initCount = 0;
  let addCount = 0;
  let existing = false;
  let failAdd = false;
  let lastQuery = "";
  let lastLimit = 0;
  mock.method(KnowledgeEngine.prototype, "initialize", async () => {
    if (++initCount === 1) throw new Error("temporary init failure");
  });
  mock.method(KnowledgeEngine.prototype, "list", () => existing ? [{ name: KB_NAME, chunk_count: 4 }] : []);
  mock.method(KnowledgeEngine.prototype, "add", async () => {
    addCount++;
    await new Promise(resolve => setTimeout(resolve, 10));
    if (failAdd) throw new Error("temporary index failure");
    existing = true;
    return { kb: { name: KB_NAME }, chunkCount: 4 };
  });
  mock.method(KnowledgeEngine.prototype, "search", async (query: string, options: {limit: number}) => {
    lastQuery = query;
    lastLimit = options.limit;
    return { results: [] };
  });
  try {
    assert.deepEqual(await searchKnowledge("  "), { ok: true, hits: [] });
    assert.equal(initCount, 0);
    await assert.rejects(getKnowledgeEngine(), /temporary init failure/);
    const all = await Promise.all(Array.from({ length: 20 }, () => ensureKnowledgeIndexed()));
    assert.equal(initCount, 2);
    assert.equal(addCount, 1);
    assert.ok(all.every(info => info.chunkCount === 4));
    assert.equal((await ensureKnowledgeIndexed()).reused, true);
    existing = false;
    failAdd = true;
    await assert.rejects(ensureKnowledgeIndexed(), /temporary index failure/);
    failAdd = false;
    await ensureKnowledgeIndexed();
    assert.equal(addCount, 3);
    await searchKnowledge("  资料分析  ", Infinity);
    assert.equal(lastQuery, "资料分析");
    assert.equal(lastLimit, 5);
    for (const [input, expected] of [[-10, 1], [10000, 10], [2.8, 2]]) {
      await searchKnowledge("query", input);
      assert.equal(lastLimit, expected);
    }
  } finally { mock.restoreAll(); }
});
