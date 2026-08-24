import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import ModelSettings from "@/components/ModelSettings";
import type { ModelInfo } from "@/lib/types";

const options = {
  configurable: true,
  providers: [
    {
      id: "ollama" as const,
      label: "Ollama (local)",
      needs_api_key: false,
      needs_base_url: true,
      default_base_url: "http://localhost:11434",
      models: ["llama3.1:8b"],
      help: "Runs on your machine.",
      backends: [],
    },
    {
      id: "anthropic" as const,
      label: "Anthropic Claude",
      needs_api_key: true,
      needs_base_url: false,
      default_base_url: null,
      models: ["claude-sonnet-4-5"],
      help: "Paste an API key.",
      backends: [],
    },
    {
      id: "openai" as const,
      label: "OpenAI / OpenAI-compatible",
      needs_api_key: true,
      needs_base_url: true,
      default_base_url: "https://api.openai.com/v1",
      models: ["gpt-4o"],
      help: "Any compatible gateway.",
      backends: [],
    },
    {
      id: "pi" as const,
      label: "Pi Coding Agent",
      needs_api_key: true,
      needs_base_url: false,
      default_base_url: null,
      models: ["claude-sonnet-4-5", "llama3.1:8b"],
      help: "Runs through the Pi CLI with every tool disabled.",
      backends: ["anthropic", "openai", "ollama"],
    },
  ],
};

const localModel: ModelInfo = {
  provider: "ollama",
  model: "llama3.1:8b",
  label: "ollama/llama3.1:8b",
  cloud_provider: null,
  embedding_provider: "ollama",
  embedding_model: "nomic-embed-text",
  available: true,
  detail: "ok",
  source: "environment",
  configurable: true,
};

const cloudModel: ModelInfo = {
  ...localModel,
  provider: "cloud",
  model: "claude-sonnet-4-5",
  label: "anthropic/claude-sonnet-4-5",
  cloud_provider: "anthropic",
  source: "runtime",
};

const mocks = vi.hoisted(() => ({
  modelOptions: vi.fn(),
  setModelConfig: vi.fn(),
  testModel: vi.fn(),
  resetModelConfig: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: mocks,
  ApiError: class ApiError extends Error {
    code = "X";
  },
}));

describe("ModelSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.modelOptions.mockResolvedValue(options);
  });

  it("offers a cloud provider with an API key field and a base URL", async () => {
    const user = userEvent.setup();
    render(
      <ModelSettings open model={localModel} onClose={() => {}} onSaved={() => {}} />,
    );

    await screen.findByLabelText("Provider");
    // Local provider needs no key.
    expect(screen.queryByLabelText("API key")).toBeNull();

    await user.selectOptions(screen.getByLabelText("Provider"), "openai");
    expect(screen.getByLabelText("API key")).toBeInTheDocument();
    expect(screen.getByLabelText("Base URL")).toHaveValue("https://api.openai.com/v1");
  });

  it("submits the full configuration and reports the active model", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    mocks.setModelConfig.mockResolvedValue({
      config: { source: "runtime", api_key_set: true, api_key_hint: "…abcd" },
      model: cloudModel,
    });

    render(<ModelSettings open model={localModel} onClose={() => {}} onSaved={onSaved} />);
    await screen.findByLabelText("Provider");

    await user.selectOptions(screen.getByLabelText("Provider"), "anthropic");
    await user.type(screen.getByLabelText("API key"), "sk-ant-secret-abcd");
    await user.click(screen.getByRole("button", { name: /save and use/i }));

    await waitFor(() => expect(mocks.setModelConfig).toHaveBeenCalledTimes(1));
    expect(mocks.setModelConfig).toHaveBeenCalledWith({
      provider: "anthropic",
      model: "claude-sonnet-4-5",
      base_url: undefined,
      api_key: "sk-ant-secret-abcd",
    });
    expect(onSaved).toHaveBeenCalledWith(cloudModel);
    expect(await screen.findByText(/now using anthropic\/claude-sonnet-4-5/i)).toBeInTheDocument();
  });

  it("clears the typed key when the vendor changes", async () => {
    // A key belongs to one vendor: carrying it across would send an Anthropic
    // credential to OpenAI's endpoint.
    const user = userEvent.setup();
    render(<ModelSettings open model={localModel} onClose={() => {}} onSaved={() => {}} />);
    await screen.findByLabelText("Provider");

    await user.selectOptions(screen.getByLabelText("Provider"), "anthropic");
    await user.type(screen.getByLabelText("API key"), "sk-ant-1234");
    expect(screen.getByLabelText("API key")).toHaveValue("sk-ant-1234");

    await user.selectOptions(screen.getByLabelText("Provider"), "openai");
    expect(screen.getByLabelText("API key")).toHaveValue("");
  });

  it("never renders the key back after saving — only a masked hint", async () => {
    const user = userEvent.setup();
    mocks.setModelConfig.mockResolvedValue({
      config: { source: "runtime", api_key_set: true, api_key_hint: "…9f2a" },
      model: cloudModel,
    });

    render(<ModelSettings open model={localModel} onClose={() => {}} onSaved={() => {}} />);
    await screen.findByLabelText("Provider");
    await user.selectOptions(screen.getByLabelText("Provider"), "anthropic");
    await user.type(screen.getByLabelText("API key"), "sk-ant-secret-9f2a");
    await user.click(screen.getByRole("button", { name: /save and use/i }));

    await waitFor(() => expect(screen.getByLabelText("API key")).toHaveValue(""));
    expect(screen.getByLabelText("API key")).toHaveAttribute(
      "placeholder",
      expect.stringContaining("…9f2a"),
    );
    expect(document.body.innerHTML).not.toContain("sk-ant-secret-9f2a");
  });

  it("surfaces a failed connection test without changing the model", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    mocks.testModel.mockResolvedValue({
      ok: false,
      detail: "The cloud provider rejected the API key.",
      label: "anthropic/claude-sonnet-4-5",
    });

    render(<ModelSettings open model={localModel} onClose={() => {}} onSaved={onSaved} />);
    await screen.findByLabelText("Provider");
    await user.selectOptions(screen.getByLabelText("Provider"), "anthropic");
    await user.click(screen.getByRole("button", { name: /test connection/i }));

    expect(await screen.findByText(/rejected the api key/i)).toBeInTheDocument();
    expect(onSaved).not.toHaveBeenCalled();
    expect(mocks.setModelConfig).not.toHaveBeenCalled();
  });

  it("reverts to the environment configuration on reset", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    mocks.resetModelConfig.mockResolvedValue({
      config: { source: "environment", api_key_set: false },
      model: localModel,
    });

    render(<ModelSettings open model={cloudModel} onClose={() => {}} onSaved={onSaved} />);
    await screen.findByLabelText("Provider");
    await user.click(screen.getByRole("button", { name: /reset to \.env/i }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(localModel));
    expect(await screen.findByText(/reverted to \.env/i)).toBeInTheDocument();
  });
});

describe("ModelSettings — Pi Coding Agent", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.modelOptions.mockResolvedValue(options);
  });

  it("exposes the agent backend selector only for Pi", async () => {
    const user = userEvent.setup();
    render(<ModelSettings open model={localModel} onClose={() => {}} onSaved={() => {}} />);
    await screen.findByLabelText("Provider");

    expect(screen.queryByLabelText("Agent backend")).toBeNull();

    await user.selectOptions(screen.getByLabelText("Provider"), "pi");
    expect(screen.getByLabelText("Agent backend")).toBeInTheDocument();
  });

  it("sends the chosen backend alongside the model", async () => {
    const user = userEvent.setup();
    const piModel: ModelInfo = {
      ...localModel,
      provider: "pi",
      model: "claude-sonnet-4-5",
      label: "pi/anthropic/claude-sonnet-4-5",
      cloud_provider: "anthropic",
      source: "runtime",
    };
    mocks.setModelConfig.mockResolvedValue({
      config: { source: "runtime", api_key_set: true, api_key_hint: "…1234" },
      model: piModel,
    });

    render(<ModelSettings open model={localModel} onClose={() => {}} onSaved={() => {}} />);
    await screen.findByLabelText("Provider");
    await user.selectOptions(screen.getByLabelText("Provider"), "pi");
    await user.type(screen.getByLabelText("API key"), "sk-ant-1234");
    await user.click(screen.getByRole("button", { name: /save and use/i }));

    await waitFor(() => expect(mocks.setModelConfig).toHaveBeenCalledTimes(1));
    expect(mocks.setModelConfig).toHaveBeenCalledWith(
      expect.objectContaining({ provider: "pi", agent_backend: "anthropic" }),
    );
  });

  it("hides the key field when Pi drives a local backend", async () => {
    // Pi + Ollama is the fully local path: no credential should be asked for.
    const user = userEvent.setup();
    render(<ModelSettings open model={localModel} onClose={() => {}} onSaved={() => {}} />);
    await screen.findByLabelText("Provider");

    await user.selectOptions(screen.getByLabelText("Provider"), "pi");
    expect(screen.getByLabelText("API key")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Agent backend"), "ollama");
    expect(screen.queryByLabelText("API key")).toBeNull();
  });

  it("clears a typed key when the Pi backend changes vendor", async () => {
    const user = userEvent.setup();
    render(<ModelSettings open model={localModel} onClose={() => {}} onSaved={() => {}} />);
    await screen.findByLabelText("Provider");

    await user.selectOptions(screen.getByLabelText("Provider"), "pi");
    await user.type(screen.getByLabelText("API key"), "sk-ant-9999");
    await user.selectOptions(screen.getByLabelText("Agent backend"), "openai");

    expect(screen.getByLabelText("API key")).toHaveValue("");
  });
});
