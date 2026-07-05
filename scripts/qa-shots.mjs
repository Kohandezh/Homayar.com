import { chromium } from '@playwright/test';
const pages = [
  ['home', '/'], ['catalog', '/catalog'], ['product', '/catalog/automated-qa-scorecards'],
  ['pricing', '/pricing'], ['platform', '/platform'], ['ai-agents', '/ai-agents'],
  ['cart', '/cart'], ['contact', '/contact'], ['case-studies', '/case-studies'], ['blog', '/blog'],
];
const browser = await chromium.launch();
for (const [vp, w, h] of [['desktop', 1440, 900], ['mobile', 390, 844]]) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => m.type() === 'error' && errors.push(m.text()));
  for (const [name, path] of pages) {
    await page.goto(`http://localhost:3000${path}`, { waitUntil: 'networkidle' });
    await page.screenshot({ path: `/tmp/homayar-qa/${name}-${vp}.png`, fullPage: name === 'home' && vp === 'desktop' });
  }
  if (errors.length) console.log(`[${vp}] console errors:`, errors.slice(0, 5));
  await ctx.close();
}
await browser.close();
console.log('done');
