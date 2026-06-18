// Kanban #2481 — AgentFormModal (create + edit) component tests.
//
// Strategy: render the modal open (open=true), mock @/lib/api's createAgent /
// updateAgent, drive the form with userEvent, and assert: the disabled-submit
// validity gate, the create + edit happy paths (incl. the EXACT AgentWrite
// payload + the operator token passed as the 2nd/3rd arg), the operator-token
// header path, and the 403 / 422-diagnostics rendering. The restart caveat is
// a static-presence assertion.
//
// Determinism (#1310): every post-await assertion goes through findBy*/waitFor
// (never a sync querySelector after a click); asyncUtilTimeout is raised so
// waitFor survives full-suite CPU load. Queries are scoped to the render
// container where practical; the modal renders into a portal-less ModalShell so
// container-scoped queries reach it.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor, within, fireEvent, configure } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpError, type AgentDetail } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

// ---------- mocks ----------

const mockRefresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: mockRefresh }),
}));

const mockCreateAgent = vi.fn();
const mockUpdateAgent = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    createAgent: (...a: Parameters<typeof actual.createAgent>) =>
      mockCreateAgent(...a),
    updateAgent: (...a: Parameters<typeof actual.updateAgent>) =>
      mockUpdateAgent(...a),
  };
});

import { AgentFormModal } from "@/components/AgentFormModal";

// ---------- fixtures ----------

function summary() {
  return {
    name: "dev-helper",
    description: "A helper.",
    model: "sonnet" as const,
    tools_summary: "All tools",
    tool_count: null,
    hook_count: 0,
    source_file: "dev-helper.md",
    domain: "dev" as const,
    valid: true,
    validation_errors: [],
  };
}

function detail(over: Partial<AgentDetail> = {}): AgentDetail {
  return {
    ...summary(),
    name: "dev-helper",
    model: "opus",
    raw_frontmatter: "name: dev-helper\nmodel: opus",
    full_description: "Full description for the helper agent.",
    spawns: [],
    tools: "All tools",
    body: "",
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------- validation gate ----------

describe("AgentFormModal — validation", () => {
  it("disables submit until name (regex) + description are valid", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <AgentFormModal mode="create" open onClose={() => {}} />,
    );
    const scope = within(container);

    const submitBtn = container.querySelector(
      "[data-agent-form-submit]",
    ) as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(true);

    // Invalid name (capital + underscore) → name error + still disabled.
    const nameInput = container.querySelector(
      "[data-agent-form-name]",
    ) as HTMLInputElement;
    await user.type(nameInput, "Bad_Name");
    expect(
      await scope.findByText(/Lower-case alphanumeric segments/i),
    ).toBeInTheDocument();
    expect(submitBtn.disabled).toBe(true);

    // Fix name but no description → still disabled.
    await user.clear(nameInput);
    await user.type(nameInput, "good-name");
    await waitFor(() => expect(submitBtn.disabled).toBe(true));

    // Add description → enabled.
    const desc = container.querySelector(
      "[data-agent-form-description]",
    ) as HTMLTextAreaElement;
    await user.type(desc, "A valid description.");
    await waitFor(() => expect(submitBtn.disabled).toBe(false));
  });

  it("renders the restart caveat prominently", () => {
    const { container } = render(
      <AgentFormModal mode="create" open onClose={() => {}} />,
    );
    const note = container.querySelector("[data-agent-form-restart-note]");
    expect(note).not.toBeNull();
    expect(note?.textContent).toMatch(/not invokable until Claude Code restarts/i);
  });

  it("blocks submit when the hooks JSON does not parse", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <AgentFormModal mode="create" open onClose={() => {}} />,
    );
    await user.type(
      container.querySelector("[data-agent-form-name]") as HTMLInputElement,
      "good-name",
    );
    await user.type(
      container.querySelector(
        "[data-agent-form-description]",
      ) as HTMLTextAreaElement,
      "desc",
    );
    // userEvent.type treats { and } as special key syntax → escape the literal
    // brace as {{ so we type a real "{ not json".
    await user.type(
      container.querySelector("[data-agent-form-hooks]") as HTMLTextAreaElement,
      "{{ not json",
    );
    expect(
      await within(container).findByText(/Hooks JSON is invalid/i),
    ).toBeInTheDocument();
    const submitBtn = container.querySelector(
      "[data-agent-form-submit]",
    ) as HTMLButtonElement;
    await waitFor(() => expect(submitBtn.disabled).toBe(true));
  });
});

// ---------- create happy path ----------

describe("AgentFormModal — create", () => {
  it("POSTs the minimal AgentWrite and forwards the operator token", async () => {
    const user = userEvent.setup();
    mockCreateAgent.mockResolvedValueOnce(summary());
    const onClose = vi.fn();
    const { container } = render(
      <AgentFormModal mode="create" open onClose={onClose} />,
    );

    await user.type(
      container.querySelector("[data-agent-form-name]") as HTMLInputElement,
      "dev-helper",
    );
    await user.type(
      container.querySelector(
        "[data-agent-form-description]",
      ) as HTMLTextAreaElement,
      "A helper agent.",
    );
    await user.selectOptions(
      container.querySelector("[data-agent-form-model]") as HTMLSelectElement,
      "sonnet",
    );
    await user.type(
      container.querySelector("[data-agent-form-body]") as HTMLTextAreaElement,
      "You are a helper.",
    );
    await user.type(
      container.querySelector(
        "[data-agent-form-operator-token]",
      ) as HTMLInputElement,
      "secret-key",
    );

    await user.click(
      container.querySelector("[data-agent-form-submit]") as HTMLButtonElement,
    );

    await waitFor(() => expect(mockCreateAgent).toHaveBeenCalledTimes(1));
    const [payload, token] = mockCreateAgent.mock.calls[0];
    expect(payload).toEqual({
      name: "dev-helper",
      description: "A helper agent.",
      model: "sonnet",
      body: "You are a helper.",
    });
    // No tools/hooks/scope keys when left at inherit/empty (minimal payload).
    expect("tools" in payload).toBe(false);
    expect("hooks" in payload).toBe(false);
    expect("scope" in payload).toBe(false);
    expect(token).toBe("secret-key");

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(mockRefresh).toHaveBeenCalled();
  });

  it("sends tools as an explicit list when list mode + rows are filled", async () => {
    const user = userEvent.setup();
    mockCreateAgent.mockResolvedValueOnce(summary());
    const { container } = render(
      <AgentFormModal mode="create" open onClose={() => {}} />,
    );
    await user.type(
      container.querySelector("[data-agent-form-name]") as HTMLInputElement,
      "dev-helper",
    );
    await user.type(
      container.querySelector(
        "[data-agent-form-description]",
      ) as HTMLTextAreaElement,
      "desc",
    );
    // Switch tools to explicit list and add two rows.
    await user.click(
      container.querySelector(
        '[data-agent-form-tools-mode="list"]',
      ) as HTMLInputElement,
    );
    await user.click(
      container.querySelector("[data-agent-form-tool-add]") as HTMLButtonElement,
    );
    await user.type(
      container.querySelector("[data-agent-form-tool-row='0']") as HTMLInputElement,
      "Read",
    );
    await user.click(
      container.querySelector("[data-agent-form-tool-add]") as HTMLButtonElement,
    );
    await user.type(
      container.querySelector("[data-agent-form-tool-row='1']") as HTMLInputElement,
      "Grep",
    );
    await user.click(
      container.querySelector("[data-agent-form-submit]") as HTMLButtonElement,
    );
    await waitFor(() => expect(mockCreateAgent).toHaveBeenCalledTimes(1));
    expect(mockCreateAgent.mock.calls[0][0].tools).toEqual(["Read", "Grep"]);
  });
});

// ---------- edit happy path ----------

describe("AgentFormModal — edit", () => {
  it("pre-fills, disables the name field, and PUTs with the path name", async () => {
    const user = userEvent.setup();
    mockUpdateAgent.mockResolvedValueOnce(summary());
    const onClose = vi.fn();
    const { container } = render(
      <AgentFormModal
        mode="edit"
        agent={detail()}
        open
        onClose={onClose}
      />,
    );

    const nameInput = container.querySelector(
      "[data-agent-form-name]",
    ) as HTMLInputElement;
    // Name pre-filled from the detail + disabled (identity / filename).
    expect(nameInput.value).toBe("dev-helper");
    expect(nameInput.disabled).toBe(true);

    // Description pre-filled from full_description.
    const desc = container.querySelector(
      "[data-agent-form-description]",
    ) as HTMLTextAreaElement;
    expect(desc.value).toBe("Full description for the helper agent.");

    const tokenInput = container.querySelector(
      "[data-agent-form-operator-token]",
    ) as HTMLInputElement;
    // fireEvent.change sets the value + fires React's onChange directly (no
    // focus dance), which is deterministic regardless of any pending RAF focus.
    fireEvent.change(tokenInput, { target: { value: "edit-key" } });
    await waitFor(() => expect(tokenInput.value).toBe("edit-key"));
    await user.click(
      container.querySelector("[data-agent-form-submit]") as HTMLButtonElement,
    );

    await waitFor(() => expect(mockUpdateAgent).toHaveBeenCalledTimes(1));
    const [pathName, payload, token] = mockUpdateAgent.mock.calls[0];
    expect(pathName).toBe("dev-helper");
    expect(payload.name).toBe("dev-helper");
    expect(token).toBe("edit-key");
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("pre-fills tools list + body when agent.tools is string[] + agent.body is set", async () => {
    const { container } = render(
      <AgentFormModal
        mode="edit"
        agent={detail({ tools: ["Read", "Grep"], body: "hello" })}
        open
        onClose={() => {}}
      />,
    );
    // List mode radio selected.
    await waitFor(() => {
      const listRadio = container.querySelector(
        '[data-agent-form-tools-mode="list"]',
      ) as HTMLInputElement;
      expect(listRadio.checked).toBe(true);
    });
    // Two pre-filled tool rows.
    await waitFor(() => {
      const row0 = container.querySelector(
        "[data-agent-form-tool-row='0']",
      ) as HTMLInputElement;
      const row1 = container.querySelector(
        "[data-agent-form-tool-row='1']",
      ) as HTMLInputElement;
      expect(row0.value).toBe("Read");
      expect(row1.value).toBe("Grep");
    });
    // Body pre-filled.
    await waitFor(() => {
      const bodyArea = container.querySelector(
        "[data-agent-form-body]",
      ) as HTMLTextAreaElement;
      expect(bodyArea.value).toBe("hello");
    });
  });

  it("shows inherit mode when agent.tools is null", async () => {
    const { container } = render(
      <AgentFormModal
        mode="edit"
        agent={detail({ tools: null })}
        open
        onClose={() => {}}
      />,
    );
    await waitFor(() => {
      const inheritRadio = container.querySelector(
        '[data-agent-form-tools-mode="inherit"]',
      ) as HTMLInputElement;
      expect(inheritRadio.checked).toBe(true);
    });
  });

  it("shows All-tools mode when agent.tools is 'All tools'", async () => {
    const { container } = render(
      <AgentFormModal
        mode="edit"
        agent={detail({ tools: "All tools" })}
        open
        onClose={() => {}}
      />,
    );
    await waitFor(() => {
      const allRadio = container.querySelector(
        '[data-agent-form-tools-mode="all"]',
      ) as HTMLInputElement;
      expect(allRadio.checked).toBe(true);
    });
  });
});

// ---------- error rendering ----------

describe("AgentFormModal — error rendering", () => {
  it("renders the operator prompt on a 403", async () => {
    const user = userEvent.setup();
    mockCreateAgent.mockRejectedValueOnce(
      new HttpError(403, "operator_proof_required: …", "403 Forbidden"),
    );
    const { container } = render(
      <AgentFormModal mode="create" open onClose={() => {}} />,
    );
    await user.type(
      container.querySelector("[data-agent-form-name]") as HTMLInputElement,
      "dev-helper",
    );
    await user.type(
      container.querySelector(
        "[data-agent-form-description]",
      ) as HTMLTextAreaElement,
      "desc",
    );
    await user.click(
      container.querySelector("[data-agent-form-submit]") as HTMLButtonElement,
    );
    const err = await within(container).findByText(
      /paste your OPERATOR_ACTION_KEY/i,
    );
    expect(err).toBeInTheDocument();
    expect(
      container.querySelector('[data-agent-form-error-kind="operator"]'),
    ).not.toBeNull();
  });

  it("renders the validator diagnostics list on a 422 object detail", async () => {
    const user = userEvent.setup();
    mockCreateAgent.mockRejectedValueOnce(
      new HttpError(
        422,
        {
          message: "agent frontmatter is invalid; nothing was written",
          diagnostics: [
            {
              file: "dev-helper.md",
              line: 3,
              field: "model",
              message: "unknown model 'opux'",
              severity: "error",
            },
            {
              file: "dev-helper.md",
              line: 5,
              field: "custom",
              message: "unknown frontmatter key",
              severity: "warning",
            },
          ],
        },
        "422 Unprocessable Entity",
      ),
    );
    const { container } = render(
      <AgentFormModal mode="create" open onClose={() => {}} />,
    );
    await user.type(
      container.querySelector("[data-agent-form-name]") as HTMLInputElement,
      "dev-helper",
    );
    await user.type(
      container.querySelector(
        "[data-agent-form-description]",
      ) as HTMLTextAreaElement,
      "desc",
    );
    await user.click(
      container.querySelector("[data-agent-form-submit]") as HTMLButtonElement,
    );

    await within(container).findByText(/agent frontmatter is invalid/i);
    const diags = container.querySelectorAll("[data-agent-form-diagnostic]");
    expect(diags.length).toBe(2);
    expect(
      container.querySelector('[data-agent-form-diagnostic][data-severity="error"]'),
    ).not.toBeNull();
    expect(container.textContent).toContain("unknown model 'opux'");
  });

  it("renders the conflict message on a 409", async () => {
    const user = userEvent.setup();
    mockCreateAgent.mockRejectedValueOnce(
      new HttpError(409, "agent 'x' already exists", "409 Conflict"),
    );
    const { container } = render(
      <AgentFormModal mode="create" open onClose={() => {}} />,
    );
    await user.type(
      container.querySelector("[data-agent-form-name]") as HTMLInputElement,
      "dev-helper",
    );
    await user.type(
      container.querySelector(
        "[data-agent-form-description]",
      ) as HTMLTextAreaElement,
      "desc",
    );
    await user.click(
      container.querySelector("[data-agent-form-submit]") as HTMLButtonElement,
    );
    expect(
      await within(container).findByText(/already exists/i),
    ).toBeInTheDocument();
  });
});
