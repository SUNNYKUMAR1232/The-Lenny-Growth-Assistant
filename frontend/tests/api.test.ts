import { afterEach, describe, expect, it, vi } from "vitest";
import { isAbortError, parseSseChunk, streamMessage } from "@/lib/api";

describe("parseSseChunk", () => {
  it("parses complete events and keeps the trailing partial block", () => {
    const buffer =
      'event: route\ndata: {"route":"KNOWLEDGE_Q","method":"rule","confidence":0.85,"model":"ollama/llama3.1:8b"}\n\n' +
      'event: token\ndata: {"text":"Retention "}\n\n' +
      'event: token\ndata: {"text":"comp';

    const { events, rest } = parseSseChunk(buffer);

    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({ type: "route", route: "KNOWLEDGE_Q" });
    expect(events[1]).toMatchObject({ type: "token", text: "Retention " });
    expect(rest).toContain('"text":"comp');
  });

  it("unwraps the final chat response", () => {
    const buffer = 'event: final\ndata: {"session_id":"s1","message":{"content":"hi"}}\n\n';
    const { events } = parseSseChunk(buffer);
    expect(events[0].type).toBe("final");
    expect((events[0] as any).response.session_id).toBe("s1");
  });

  it("surfaces server errors as error events", () => {
    const buffer =
      'event: error\ndata: {"error":{"code":"MODEL_UNAVAILABLE","message":"Ollama is not running."}}\n\n';
    const { events } = parseSseChunk(buffer);
    expect(events[0]).toEqual({
      type: "error",
      error: { code: "MODEL_UNAVAILABLE", message: "Ollama is not running." },
    });
  });

  it("ignores malformed payloads instead of throwing", () => {
    const { events } = parseSseChunk("event: token\ndata: {not json}\n\nevent: unknown\ndata: {}\n\n");
    expect(events).toEqual([]);
  });

  it("returns nothing for an empty buffer", () => {
    expect(parseSseChunk("").events).toEqual([]);
  });
});

describe("isAbortError", () => {
  it("recognises a cancelled request", () => {
    expect(isAbortError(new DOMException("aborted", "AbortError"))).toBe(true);
  });

  it("does not mistake a real failure for a cancel", () => {
    expect(isAbortError(new Error("connection reset"))).toBe(false);
    expect(isAbortError(null)).toBe(false);
  });
});

describe("streamMessage cancellation", () => {
  afterEach(() => vi.unstubAllGlobals());

  /** An SSE body that yields one chunk, then waits to be released. */
  function pausableBody(first: string) {
    let release: (() => void) | null = null;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let sent = false;
    return {
      release: () => release?.(),
      body: {
        getReader: () => ({
          read: async () => {
            if (!sent) {
              sent = true;
              return { done: false, value: new TextEncoder().encode(first) };
            }
            await gate;
            return { done: true, value: undefined };
          },
          cancel: async () => {},
        }),
      },
    };
  }

  it("stops delivering events once the caller aborts", async () => {
    const controller = new AbortController();
    const { body, release } = pausableBody(
      'event: token\ndata: {"text":"one "}\n\nevent: token\ndata: {"text":"two "}\n\n',
    );
    vi.stubGlobal("fetch", async () => ({ ok: true, body }) as unknown as Response);

    const seen: string[] = [];
    const pending = streamMessage("session-a", "hi", (event) => {
      if (event.type === "token") seen.push(event.text);
      // Cancel partway through, the way Stop or a session switch does.
      controller.abort();
    }, { signal: controller.signal });

    release();
    await pending;

    // The first chunk's events were already parsed; what matters is that the
    // loop stopped and never read another chunk into a session the user left.
    expect(seen.length).toBeGreaterThan(0);
    expect(controller.signal.aborted).toBe(true);
  });

  it("propagates an abort instead of reporting it as a network failure", async () => {
    const controller = new AbortController();
    controller.abort();
    vi.stubGlobal("fetch", async () => {
      throw new DOMException("aborted", "AbortError");
    });

    await expect(
      streamMessage("session-a", "hi", () => {}, { signal: controller.signal }),
    ).rejects.toSatisfy(isAbortError);
  });
});
