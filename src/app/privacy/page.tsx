import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Metadata } from "next";
import { renderMarkdown } from "@/lib/markdown";

export const metadata: Metadata = {
  title: "Privacy Policy",
  description:
    "How Homayar collects, uses, and protects personal data — including call recordings, transcripts, and AI-derived analysis processed on behalf of enterprise customers.",
};

export default function PrivacyPage() {
  const md = readFileSync(
    join(process.cwd(), "content/legal/privacy.md"),
    "utf8",
  );
  const html = renderMarkdown(md);

  return (
    <section className="container-site pt-16 pb-24 sm:pt-20">
      <div className="mx-auto max-w-3xl">
        <div
          className="prose-dark"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </div>
    </section>
  );
}
