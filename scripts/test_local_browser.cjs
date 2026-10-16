const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const base = 'http://dashboard:8000';
const routes = ['/', '/vulnerability-index/', '/dashboard/', '/vi/', '/authors/',
  '/outlets/', '/countries/', '/articles/', '/generate-report/', '/chat/'];
const expectedModes = {
  aws_translate: process.env.VI_LOCAL_AWS_LIVE === '1' ? 'live' : 'controlled',
  groq: process.env.VI_LOCAL_GROQ_LIVE === '1' ? 'live' : 'controlled',
};

async function verifyProviders(context, stage, requireGroq = false) {
  const response = await context.request.get(base + '/__local_test__/providers');
  assert.equal(response.status(), 200, 'Local provider instrumentation missing');
  const providers = await response.json();
  for (const [name, expectedMode] of Object.entries(expectedModes)) {
    assert.equal(providers[name].mode, expectedMode, `${name}: incorrect provider mode`);
    assert.equal(providers[name].failed, 0, `${stage}: ${name} failed; the app may have returned fallback content. Check credentials/permissions, region/model, quota and service logs.`);
    if (name === 'aws_translate' || requireGroq) assert.ok(providers[name].succeeded > 0, `${stage}: ${name} was never called`);
  }
  return providers;
}

async function run() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  page.setDefaultNavigationTimeout(120000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  // The isolated stack cannot reach CDNs. Use the installed Plotly bundle,
  // not a fake chart, and exclude third-party styling from functional checks.
  await context.route('**/*', async route => {
    const url = route.request().url();
    if (url.startsWith(base + '/')) return route.continue();
    if (url.startsWith('https://cdn.plot.ly/')) {
      const script = await context.request.get(base + '/__local_test__/plotly.js');
      return route.fulfill({ response: script });
    }
    return route.fulfill({ status: 200, contentType: url.includes('.css') ? 'text/css' : 'application/javascript', body: '' });
  });
  try {
    for (const [language, home, country, reportTitle] of [
      ['en', 'Home', 'Ethiopia', 'Generate CVI Report'],
      ['fr', 'Accueil', 'Éthiopie', 'Générer un rapport CVI'],
    ]) {
      await page.goto(base + '/');
      await verifyProviders(context, 'homepage translation');
      if (expectedModes.aws_translate === 'live') {
        assert.ok(!(await page.locator('body').innerText()).includes('Bonjour, voici les nouvelles économiques.'),
          'The French fixture title was not translated; do not accept original-text fallback in live mode');
      }
      await Promise.all([page.waitForNavigation(), page.selectOption('select[name=language]', language)]);
      for (const route of routes) {
        const response = await page.goto(base + route);
        assert.equal(response.status(), 200, `${language} ${route}`);
        assert.equal(response.headers()['content-language'], language);
        if (route !== '/articles/') assert.equal(await page.locator('html').getAttribute('lang'), language);
      }
      await page.goto(base + '/');
      assert.ok(await page.locator('nav.navbar').innerText().then(text => text.includes(home)));
      assert.equal(await page.locator('select[name=calc_target_country] option[value=Ethiopia]').innerText(), country);
      await page.selectOption('select[name=calc_target_country]', 'Ethiopia');
      await page.selectOption('select[name=calc_foreign_actor]', 'China');
      await page.selectOption('select[name=calc_strategic_intent]', 'Economic');
      await Promise.all([page.waitForNavigation(), page.locator('#cvi-filter-form button[type=submit]').click()]);
      for (const [name, value] of [['calc_target_country', 'Ethiopia'], ['calc_foreign_actor', 'China'], ['calc_strategic_intent', 'Economic']]) {
        assert.equal(new URL(page.url()).searchParams.get(name), value);
        assert.equal(await page.locator(`select[name=${name}]`).inputValue(), value);
      }
      await page.waitForFunction(() => [...document.querySelectorAll('.js-plotly-plot')].some(el => el.data?.length));
      assert.ok(await page.locator('a[href^="https://example.org/local-test-"]').count());
      const nextLanguage = language === 'en' ? 'fr' : 'en';
      await Promise.all([page.waitForNavigation(), page.selectOption('select[name=language]', nextLanguage)]);
      assert.equal(new URL(page.url()).searchParams.get('calc_strategic_intent'), 'Economic');
      await Promise.all([page.waitForNavigation(), page.selectOption('select[name=language]', language)]);
      await page.goto(base + '/articles/?q=cooperation');
      assert.ok(await page.locator('h5').first().innerText());
      assert.ok(await page.locator('a[href^="https://example.org/local-test-"]').count());
      await page.goto(base + '/chat/');
      const rejected = await context.request.post(base + '/chat/', { data: {message: 'hello'} });
      assert.equal(rejected.status(), 403);
      await page.fill('#user-input', 'analyze Ethiopia China narratives');
      const chatReply = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname.includes('chat'));
      await page.locator('button[onclick="handleSend()"]').click();
      const accepted = await chatReply;
      assert.equal(accepted.status(), 200);
      assert.equal((await accepted.json()).success, true);
      await page.waitForFunction(() => document.querySelectorAll('.bot-msg').length > 1);
      await verifyProviders(context, 'AI chat', true);
      await page.goto(base + '/generate-report/');
      assert.equal((await page.locator('h1.display-5').innerText()).trim(), reportTitle);
      const beforePdf = await verifyProviders(context, 'before PDF', true);
      const pdf = await context.request.get(base + '/generate-report/?country=Ethiopia&actors=China', { timeout: 140000 });
      assert.equal(pdf.status(), 200);
      assert.equal(pdf.headers()['content-type'], 'application/pdf');
      assert.ok((await pdf.body()).subarray(0, 4).equals(Buffer.from('%PDF')));
      const afterPdf = await verifyProviders(context, 'PDF AI summaries and analysis', true);
      assert.equal(afterPdf.groq.succeeded - beforePdf.groq.succeeded, 5, 'PDF must complete four article summaries and one analysis, not fallback text');
      console.log(`[run-local-dashboard] PASS ${language}: 10 routes, language POST, preserved filters, real Plotly chart, articles, chat/CSRF, PDF (${(await pdf.body()).length} bytes).`);
      if (language === 'en' && expectedModes.groq === 'live') {
        // Separate each language's six real AI calls into minute-sized quota
        // windows; never hide provider errors by retrying or accepting fallbacks.
        for (let remaining = 75; remaining > 0; remaining -= 25) {
          console.log(`[run-local-dashboard] Groq quota cooldown before French checks: ${remaining}s remaining.`);
          await new Promise(resolve => setTimeout(resolve, 25000));
        }
        console.log('[run-local-dashboard] Groq quota cooldown finished; starting French checks.');
      }
    }
    assert.deepEqual(errors, [], `Browser JavaScript errors: ${errors.join('; ')}`);
    const providers = await verifyProviders(context, 'final verification', true);
    for (const [name, state] of Object.entries(providers)) {
      console.log(`[run-local-dashboard] PROVIDER PASS: ${name} mode=${state.mode} succeeded=${state.succeeded} failed=${state.failed}`);
    }
    console.log('[run-local-dashboard] PASS: no browser JavaScript errors. CDN styling is excluded.');
  } finally {
    await browser.close();
  }
}

run().catch(error => { console.error(`[run-local-dashboard] BROWSER FAILED: ${error.stack}`); process.exitCode = 1; });
