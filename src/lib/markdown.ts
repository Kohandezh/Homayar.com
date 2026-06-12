import { marked } from "marked";

/**
 * Renders first-party markdown (blog posts, legal pages) to HTML on the
 * server. Content is authored in-repo, so no sanitization pass is needed.
 * Style with the .prose-dark class from globals.css.
 */
export function renderMarkdown(md: string): string {
  return marked.parse(md, { async: false, gfm: true, breaks: false });
}
