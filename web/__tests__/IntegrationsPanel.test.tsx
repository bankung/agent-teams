// Component tests for IntegrationsPanel — Kanban #2375 (R5 /settings consolidation).
//
// IntegrationsPanel is the read-only integration status list extracted from the
// former PlatformSettingsModal so it can render as a page panel on /settings.
// These tests cover the panel-form behaviour (no modal chrome): fetch on mount,
// status badge + category grouping render, and the per-row (?) setup expander —
// the coverage previously implied by the modal, now retargeted to the panel.
//
// Strategy: mock @/lib/api getIntegrations. Determinism: async-fetch assertions
// use findBy*/waitFor (never sync querySelector on post-fetch state).

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  configure,
} from "@testing-library/react";
import type { IntegrationsResponse } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

const mockGetIntegrations = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getIntegrations: (...args: Parameters<typeof actual.getIntegrations>) =>
      mockGetIntegrations(...args),
  };
});

// Imported AFTER mocks register.
import { IntegrationsPanel } from "@/components/IntegrationsPanel";

function response(over: Partial<IntegrationsResponse> = {}): IntegrationsResponse {
  return {
    platform_security: { vault_key_configured: true },
    integrations: [
      {
        id: "telegram",
        label: "Telegram",
        category: "Notifications",
        configured: true,
        env_vars: [{ name: "TELEGRAM_BOT_TOKEN", required: true, present: true }],
        setup: { steps: ["Create a bot via @BotFather"], links: [] },
      },
      {
        id: "openai",
        label: "OpenAI",
        category: "LLM",
        configured: false,
        env_vars: [{ name: "OPENAI_API_KEY", required: true, present: false }],
        setup: { steps: ["Generate an API key"], links: [] },
      },
    ],
    ...over,
  };
}

describe("IntegrationsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("fetches on mount and renders grouped rows with status badges", async () => {
    mockGetIntegrations.mockResolvedValue(response());
    render(<IntegrationsPanel />);

    // Fetch fires on mount (no trigger button — it's a page panel now).
    await waitFor(() => expect(mockGetIntegrations).toHaveBeenCalledTimes(1));

    // Both integration rows render under their categories.
    expect(await screen.findByText("Telegram")).toBeTruthy();
    expect(screen.getByText("OpenAI")).toBeTruthy();
    expect(screen.getByText("Notifications")).toBeTruthy();
    expect(screen.getByText("LLM")).toBeTruthy();

    // Status badges preserve the data-integration-status attr contract.
    const configured = document.querySelector(
      '[data-integration-status="configured"]',
    );
    const notConfigured = document.querySelector(
      '[data-integration-status="not-configured"]',
    );
    expect(configured).toBeTruthy();
    expect(notConfigured).toBeTruthy();

    // Platform security card renders with the vault-key presence badge.
    expect(
      document.querySelector('[data-vault-key-status="configured"]'),
    ).toBeTruthy();
  });

  it("toggles the per-row setup expander via the (?) button", async () => {
    mockGetIntegrations.mockResolvedValue(response());
    render(<IntegrationsPanel />);

    const helpBtn = await screen.findByRole("button", {
      name: /show setup guidance for Telegram/i,
    });
    // Collapsed by default.
    expect(
      document.querySelector('[data-integration-setup="telegram"]'),
    ).toBeNull();

    fireEvent.click(helpBtn);
    await waitFor(() =>
      expect(
        document.querySelector('[data-integration-setup="telegram"]'),
      ).toBeTruthy(),
    );
    expect(screen.getByText("Create a bot via @BotFather")).toBeTruthy();
  });

  it("shows an error + retry on fetch failure", async () => {
    mockGetIntegrations.mockRejectedValueOnce(new Error("boom"));
    render(<IntegrationsPanel />);

    expect(await screen.findByRole("alert")).toBeTruthy();
    const retry = screen.getByRole("button", { name: /retry/i });

    mockGetIntegrations.mockResolvedValueOnce(response());
    fireEvent.click(retry);
    expect(await screen.findByText("Telegram")).toBeTruthy();
  });
});
