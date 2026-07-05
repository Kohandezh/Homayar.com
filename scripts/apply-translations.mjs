// One-off: read the translation workflow output and inject the de/ar/fr/es
// dictionaries into src/lib/content.ts, decoding stray HTML entities.
import fs from "node:fs";

const outPath = process.argv[2];
const contentPath = process.argv[3];

const raw = fs.readFileSync(outPath, "utf8");
const codeIdx = raw.indexOf('"code"');
if (codeIdx < 0) throw new Error("result array not found in output");
const start = raw.lastIndexOf("[", codeIdx);
const arr = JSON.parse(raw.slice(start, raw.lastIndexOf("]") + 1));

const decode = (s) =>
  typeof s === "string"
    ? s
        .replace(/&amp;/g, "&")
        .replace(/&#39;/g, "'")
        .replace(/&apos;/g, "'")
        .replace(/&quot;/g, '"')
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&nbsp;/g, " ")
    : s;
const deep = (o) =>
  Array.isArray(o)
    ? o.map(deep)
    : o && typeof o === "object"
      ? Object.fromEntries(Object.entries(o).map(([k, v]) => [k, deep(v)]))
      : decode(o);

const byCode = {};
for (const r of arr) byCode[r.code] = deep(r.dict);

let consts = "";
for (const code of ["de", "ar", "fr", "es"]) {
  if (!byCode[code]) throw new Error("missing translation: " + code);
  consts += `const ${code}: Dict = ${JSON.stringify(byCode[code], null, 2)};\n\n`;
}

let c = fs.readFileSync(contentPath, "utf8");
const startMarker = "// de/ar/fr/es are produced";
const endMarker = "export const CONTENT";
const s = c.indexOf(startMarker);
const e = c.indexOf(endMarker);
if (s < 0 || e < 0) throw new Error("markers not found in content.ts");
c = c.slice(0, s) + consts + c.slice(e);
fs.writeFileSync(contentPath, c);

console.log(
  "content.ts updated:",
  arr.map((r) => ({ code: r.code, complete: r.complete, issues: (r.issues || []).length })),
);
