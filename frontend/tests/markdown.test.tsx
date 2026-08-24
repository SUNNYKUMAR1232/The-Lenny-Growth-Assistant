import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import Markdown from "@/components/Markdown";

/**
 * Model output is Markdown. If any surface renders it as plain text, users see
 * literal `**asterisks**` and `## hashes` — which is exactly what happened to
 * the streaming bubble before it was switched to this component.
 */
describe("Markdown", () => {
  it("renders emphasis as markup, not as literal asterisks", () => {
    const { container } = render(<Markdown>{"Retention is **the** metric."}</Markdown>);
    expect(container.querySelector("strong")).toHaveTextContent("the");
    expect(container.textContent).not.toContain("**");
  });

  it("renders headings, lists and tables", () => {
    const source = [
      "## Activation",
      "",
      "- first",
      "- second",
      "",
      "| metric | value |",
      "| --- | --- |",
      "| D7 | 40% |",
    ].join("\n");
    const { container } = render(<Markdown>{source}</Markdown>);

    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Activation");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("table")).toBeTruthy(); // remark-gfm
    expect(container.textContent).not.toContain("|");
  });

  it("renders partial Markdown mid-stream without dropping text", () => {
    // Streaming delivers half-finished syntax; it must still show the words.
    const { container } = render(<Markdown>{"**Onboarding is the on"}</Markdown>);
    expect(container.textContent).toContain("Onboarding is the on");
  });

  it("does not execute raw HTML in model output", () => {
    const { container } = render(
      <Markdown>{'<img src=x onerror="alert(1)"> plain text'}</Markdown>,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("plain text");
  });

  it("opens links safely", () => {
    render(<Markdown>{"[episode](https://youtube.com/watch?v=x)"}</Markdown>);
    const link = screen.getByRole("link", { name: "episode" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });
});
