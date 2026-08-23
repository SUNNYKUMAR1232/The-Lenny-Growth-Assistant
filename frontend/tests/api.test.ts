import { describe, expect, it } from "vitest";
import { parseSseChunk } from "@/lib/api";

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
