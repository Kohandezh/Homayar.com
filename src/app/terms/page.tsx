import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Metadata } from "next";
import { renderMarkdown } from "@/lib/markdown";

export const metadata: Metadata = {
  title: "Terms of Service",
  description:
    "The terms governing access to and use of the Homayar platform — subscriptions, acceptable use, customer data ownership, and recording consent responsibilities.",
};

export default function TermsPage() {
  const md = readFileSync(join(process.cwd(), "content/legal/terms.md"), "utf8");
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
