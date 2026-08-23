"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Markdown rendering for chat messages and Markdown artifacts.
 *
 * `rehype-raw` is deliberately NOT installed. Raw HTML inside model output is
 * rendered as text, never as markup — the HTML path goes through the
 * sanitizer and the sandboxed iframe instead. Links open in a new tab with
 * `noopener noreferrer` so an artifact link can never reach `window.opener`.
 */
export default function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-chat">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
