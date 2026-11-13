// 需安装 Playwright；PLAYWRIGHT_MODULE 可指定已有安装目录。
// 由 benchmark_responsiveness.py 启动隔离服务后调用，不连接真实学习库。
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    let frames = 0, bytes = 0;
    page.on('websocket', ws => ws.on('framereceived', f => {
      frames++;
      bytes += Buffer.byteLength(f.payload);
    }));
    await page.goto(process.argv[2]);
    await page.locator('.exam-header-clock').waitFor();
    await page.getByRole('heading', { name: '学习规划与对话辅导', exact: true, includeHidden: true }).waitFor({ state: 'attached' });
    await page.waitForTimeout(600);
    await page.evaluate(() => {
      window.clockReplacements = 0;
      const clock = document.querySelector('.exam-header-clock');
      new MutationObserver(records => {
        for (const record of records) {
          if ([...record.removedNodes].some(node => node === clock || node.contains?.(clock))) {
            window.clockReplacements++;
          }
        }
      }).observe(document.body, { childList: true, subtree: true });
    });
    const idleFrames = frames;
    await page.waitForTimeout(3500);
    const result = { idle: {
      frames: frames - idleFrames,
      clockReplacements: await page.evaluate(() => window.clockReplacements),
    }};
    if (result.idle.frames || result.idle.clockReplacements) throw Error('空闲计时触发服务端刷新或替换计时器：' + JSON.stringify(result.idle));
    const next = async () => {
      const header = page.getByRole('heading').filter({ hasText: '错题详情' });
      const previous = await header.innerText();
      const start = Date.now();
      await page.getByRole('button', { name: '下一题 →', exact: true }).click();
      await page.waitForFunction(previous => [...document.querySelectorAll('h3')].some(
        e => e.textContent.includes('错题详情') && e.textContent.trim() !== previous.trim()), previous);
      return Date.now() - start;
    };
    await page.getByRole('tab', { name: '错题本', exact: true }).click();
    let startFrames = frames, startBytes = bytes;
    const switchTimes = [];
    for (let i = 0; i < 7; i++) switchTimes.push(await next());
    result.switch = { ms: switchTimes, frames: frames - startFrames, bytes: bytes - startBytes };
    startFrames = frames; startBytes = bytes;
    const starTimes = [];
    for (let i = 0; i < 4; i++) {
      const button = page.getByRole('tabpanel').filter({ has: page.locator('.st-key-wb_grid') })
        .getByRole('button', { name: /^(标为重点|取消重点)$/ });
      const old = await button.innerText(), start = Date.now();
      await button.click();
      await page.waitForFunction(old => [...document.querySelectorAll('button')].some(
        e => e.offsetWidth && e.textContent.trim() === (old === '标为重点' ? '取消重点' : '标为重点')), old);
      starTimes.push(Date.now() - start);
    }
    result.star = { ms: starTimes, frames: frames - startFrames, bytes: bytes - startBytes };
    await page.getByRole('tab', { name: '做题', exact: true }).click();
    const question = page.locator('[role="radiogroup"][aria-label^="选项##"]').first();
    const selected = await question.locator('input').evaluateAll(xs => xs.findIndex(x => x.checked));
    await question.locator('label').nth(selected === 1 ? 2 : 1).click();
    await page.waitForTimeout(600);
    const actual = await page.locator('[role="radiogroup"][aria-label^="选项##"]').evaluateAll(
      groups => groups.filter(g => { const x = g.querySelector('input:checked'); return x && x.value !== '0'; }).length);
    const progress = await page.locator('[data-clock-progress]').innerText();
    if (Number(progress.split('/')[0]) !== actual) throw Error('进度与作答状态不一致');
    const timer = await page.locator('[data-clock-elapsed]').innerText();
    await page.waitForTimeout(1300);
    if (timer === await page.locator('[data-clock-elapsed]').innerText()) throw Error('计时未递增');
    result.progressChecked = true;
    await page.getByRole('tab', { name: '学习规划 / 对话', exact: true }).click();
    const start = Date.now();
    await page.getByRole('button', { name: '生成阶段性学习规划', exact: true }).click();
    await page.getByText(/正在生成 .* 天规划/).waitFor();
    result.planStartMs = Date.now() - start;
    await page.getByRole('tab', { name: '错题本', exact: true }).click();
    result.switchWhilePlanMs = await next();
    await page.getByRole('tab', { name: '学习规划 / 对话', exact: true }).click();
    await page.getByText('模拟异步规划完成', { exact: true }).waitFor({ timeout: 15000 });
    result.asyncPlanCompleted = true;
    console.log(JSON.stringify(result, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
