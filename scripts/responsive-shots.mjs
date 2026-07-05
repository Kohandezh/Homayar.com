import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE || "http://localhost:3009";
const OUT = "/tmp/claude/resp";
mkdirSync(OUT, { recursive: true });

// Device classes the user named: phone, large phone, tablet/iPad portrait & landscape.
const VIEWPORTS = [
  { name: "mobile-360", width: 360, height: 800 },
  { name: "mobile-390", width: 390, height: 844 },
  { name: "tablet-768", width: 768, height: 1024 },
  { name: "ipad-820", width: 820, height: 1180 },
  { name: "ipad-land-1024", width: 1024, height: 768 },
];

const PAGES = [
  { name: "home", path: "/" },
  { name: "pricing", path: "/pricing" },
  { name: "catalog", path: "/catalog" },
  { name: "platform", path: "/platform" },
  { name: "contact", path: "/contact" },
];

const browser = await chromium.launch();
const issues = [];

for (const vp of VIEWPORTS) {
  const ctx = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    deviceScaleFactor: 2,
    isMobile: vp.width < 820,
    hasTouch: vp.width < 1024,
  });
  const page = await ctx.newPage();
  for (const p of PAGES) {
    await page.goto(BASE + p.path, { waitUntil: "networkidle" }).catch(() => {});
    await page.waitForTimeout(400);
    // Detect horizontal overflow (the #1 mobile responsiveness bug).
    const overflow = await page.evaluate(() => {
      const de = document.documentElement;
      const scrollW = de.scrollWidth;
      const clientW = de.clientWidth;
      const offenders = [];
      if (scrollW > clientW + 1) {
        for (const el of document.querySelectorAll("*")) {
          const r = el.getBoundingClientRect();
          if (r.right > clientW + 1 && r.width > 4) {
            offenders.push(
              `${el.tagName.toLowerCase()}.${(el.className && el.className.toString ? el.className.toString() : "").slice(0, 60)} right=${Math.round(r.right)}`,
            );
          }
          if (offenders.length > 6) break;
        }
      }
      return { scrollW, clientW, offenders };
    });
    if (overflow.scrollW > overflow.clientW + 1) {
      issues.push(
        `[OVERFLOW] ${p.name} @ ${vp.name}: scrollW=${overflow.scrollW} clientW=${overflow.clientW}\n    ${overflow.offenders.join("\n    ")}`,
      );
    }
    await page.screenshot({
      path: `${OUT}/${p.name}__${vp.name}.png`,
      fullPage: false,
    });
  }
  await ctx.close();
}

await browser.close();

if (issues.length) {
  console.log("=== RESPONSIVE ISSUES ===");
  console.log(issues.join("\n"));
} else {
  console.log("No horizontal-overflow issues detected across viewports.");
}
console.log(`\nScreenshots written to ${OUT}`);
