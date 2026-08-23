import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ArtifactViewer from "@/components/ArtifactViewer";
import type { Artifact } from "@/lib/types";

const htmlArtifact: Artifact = {
  id: "a1",
  session_id: "s1",
  type: "html",
  title: "Growth review one-pager",
  content: "<!doctype html><html><body><h1>Growth review</h1></body></html>",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  metadata: { sanitization: { had_script: true, removed_urls: ["https://evil.test/x.png"] } },
};

describe("ArtifactViewer", () => {
  it("renders HTML inside a fully locked-down sandboxed iframe", () => {
    render(<ArtifactViewer artifact={htmlArtifact} />);
    const frame = screen.getByTitle(/Artifact preview/i) as HTMLIFrameElement;

    // sandbox="" grants NO capabilities: no scripts, no same-origin, no forms.
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.getAttribute("srcdoc")).toContain("<h1>Growth review</h1>");
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(frame.getAttribute("src")).toBeNull();
  });

  it("tells the viewer what the sanitizer removed", () => {
    render(<ArtifactViewer artifact={htmlArtifact} />);
    expect(screen.getByText(/script tags removed/i)).toBeInTheDocument();
    expect(screen.getByText(/1 remote URL\(s\) blocked/i)).toBeInTheDocument();
  });

  it("renders markdown artifacts as markup, not as an iframe", () => {
    render(
      <ArtifactViewer
        artifact={{
          ...htmlArtifact,
          type: "markdown",
          content: "# Retention playbook\n\n- Cohorts beat aggregates",
        }}
      />,
    );
    expect(screen.getByRole("heading", { name: "Retention playbook" })).toBeInTheDocument();
    expect(screen.queryByTitle(/Artifact preview/i)).not.toBeInTheDocument();
  });

  it("does not execute raw HTML embedded in a markdown artifact", () => {
    render(
      <ArtifactViewer
        artifact={{
          ...htmlArtifact,
          type: "markdown",
          content: "Safe text <script>window.__pwned = true</script>",
        }}
      />,
    );
    expect((window as any).__pwned).toBeUndefined();
    expect(document.querySelector("script")).toBeNull();
  });

  it("shows an empty state when there is no artifact", () => {
    render(<ArtifactViewer artifact={null} />);
    expect(screen.getByText(/No artifact yet/i)).toBeInTheDocument();
  });
});
