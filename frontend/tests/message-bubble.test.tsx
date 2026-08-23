import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import MessageBubble from "@/components/MessageBubble";
import type { Message } from "@/lib/types";

const base: Message = {
  id: "m1",
  session_id: "s1",
  role: "assistant",
  content: "Retention is the compounding engine of growth [S1].",
  created_at: new Date().toISOString(),
  metadata: {
    route: "KNOWLEDGE_Q",
    model_label: "ollama/llama3.1:8b",
    grounding: {
      enabled: true,
      checked_claims: 2,
      supported_claims: 2,
      supported_ratio: 1,
      revised: false,
      action: "accepted",
    },
    evidence: [
      {
        source_id: "S1",
        chunk_id: "c1",
        title: "Why retention is the only growth metric that matters",
        guest: "Casey Winters",
        source_url: "https://www.youtube.com/watch?v=abc&t=90s",
        chunk_index: 4,
        text: "Retention is the compounding engine of growth.",
        score: 0.81,
        vector_score: 0.7,
        keyword_score: 0.9,
        retrieval: "hybrid",
      },
    ],
  },
};

describe("MessageBubble", () => {
  it("shows the route, model and grounding verdict", () => {
    render(<MessageBubble message={base} />);
    expect(screen.getByText("Grounded answer")).toBeInTheDocument();
    expect(screen.getByText("ollama/llama3.1:8b")).toBeInTheDocument();
    expect(screen.getByText(/grounded 2\/2/i)).toBeInTheDocument();
  });

  it("flags weakly grounded answers", () => {
    render(
      <MessageBubble
        message={{
          ...base,
          metadata: {
            ...base.metadata,
            grounding: { ...base.metadata.grounding!, supported_claims: 1, action: "annotated" },
          },
        }}
      />,
    );
    expect(screen.getByText(/weakly grounded/i)).toBeInTheDocument();
  });

  it("reveals citations with a deep link into the episode", async () => {
    const user = userEvent.setup();
    render(<MessageBubble message={base} />);

    await user.click(screen.getByRole("button", { name: /1 source from 1 episode/i }));

    expect(screen.getByText("Why retention is the only growth metric that matters")).toBeInTheDocument();
    expect(screen.getByText(/Casey Winters/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /listen/i });
    expect(link).toHaveAttribute("href", "https://www.youtube.com/watch?v=abc&t=90s");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("labels memory as personalization rather than evidence", () => {
    render(
      <MessageBubble
        message={{
          ...base,
          metadata: {
            ...base.metadata,
            memories_used: [{ id: "mem1", key: "role", type: "semantic" }],
          },
        }}
      />,
    );
    expect(screen.getByText(/personalization only, not evidence/i)).toBeInTheDocument();
  });

  it("renders user messages without pipeline chrome", () => {
    render(<MessageBubble message={{ ...base, role: "user", metadata: {} }} />);
    expect(screen.queryByText("Grounded answer")).not.toBeInTheDocument();
  });
});
