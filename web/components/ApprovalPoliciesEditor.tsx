"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import {
  listAllTasks,
  updateProject,
  type ProjectRead,
  type ProjectUpdateBody,
  type TaskRead,
} from "@/lib/api";
import {
  FIELD_DEFINITIONS,
  blankDraft,
  buildPoliciesDocument,
  conditionError,
  draftErrors,
  draftFromRule,
  newCondition,
  newPolicyId,
  normalizePolicies,
  previewRule,
  ruleFromDraft,
  type ApprovalPoliciesDocument,
  type ApprovalPolicyAction,
  type ApprovalPolicyDraft,
  type ApprovalPolicyRule,
  type ConditionField,
  type ConditionLogic,
  type ConditionOperator,
  type PolicyPreviewStats,
} from "@/lib/approvalPolicies";
import { TaskStatus } from "@/lib/constants";
import { extractErrorMessage } from "@/lib/errors";
import { Icon } from "./Icon";

type Props = {
  project: ProjectRead;
};

const ACTIONS: Array<{ value: ApprovalPolicyAction; label: string }> = [
  { value: "auto_approve", label: "Auto-approve" },
  { value: "auto_deny", label: "Auto-reject" },
  { value: "require_attention", label: "Route to user" },
];

const TRIGGERS = [
  { value: "hitl_prompt", label: "HITL prompt" },
  { value: "tool_call", label: "Tool call" },
] as const;

function formatDate(value: string | null | undefined): string {
  if (!value) return "None";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "None";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function actionLabel(action: ApprovalPolicyRule["action"]): string {
  if (action === "auto_deny") return "Auto-reject";
  if (action === "require_attention" || action === "requires_attention") {
    return "Route to user";
  }
  return "Auto-approve";
}

function getRuleId(rule: ApprovalPolicyRule): string {
  return typeof rule.id === "string" ? rule.id : `${rule.name ?? "rule"}`;
}

function trimRuleForDuplicate(rule: ApprovalPolicyRule): ApprovalPolicyRule {
  return {
    ...rule,
    id: newPolicyId(),
    name: `${rule.name ?? "Policy"} copy`,
    created_at: undefined,
    updated_at: undefined,
  };
}

export function ApprovalPoliciesEditor({ project }: Props) {
  const router = useRouter();
  const [doc, setDoc] = useState<ApprovalPoliciesDocument>(() =>
    normalizePolicies(project.approval_policies),
  );
  const [draft, setDraft] = useState<ApprovalPolicyDraft>(() => blankDraft());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [statsLoading, setStatsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState<string | null>(null);
  const [previewStats, setPreviewStats] = useState<PolicyPreviewStats | null>(null);
  const [statsByRule, setStatsByRule] = useState<Record<string, PolicyPreviewStats>>({});

  useEffect(() => {
    setDoc(normalizePolicies(project.approval_policies)); // eslint-disable-line react-hooks/set-state-in-effect -- SSE-refresh sync: policy doc must be replaced when the server pushes an updated approval_policies object; no derive-during-render path exists across 6 independent state slices
    setDraft(blankDraft());
    setEditingId(null);
    setPreviewStats(null);
    setError(null);
    setSavedNote(null);
  }, [project.approval_policies]);

  const rules = useMemo(() => doc.rules ?? [], [doc.rules]);
  const version = doc.version ?? 0;
  const errors = draftErrors(draft);
  const canSave = !saving && errors.length === 0;

  useEffect(() => {
    let cancelled = false;
    async function refreshStats() {
      if (rules.length === 0) {
        setStatsByRule({});
        return;
      }
      setStatsLoading(true);
      try {
        const tasks = await listAllTasks(project.id, {
          process_status: TaskStatus.DONE,
        });
        if (cancelled) return;
        const next: Record<string, PolicyPreviewStats> = {};
        for (const rule of rules) {
          next[getRuleId(rule)] = previewRule(rule, tasks);
        }
        setStatsByRule(next);
      } catch {
        if (!cancelled) setStatsByRule({});
      } finally {
        if (!cancelled) setStatsLoading(false);
      }
    }
    refreshStats();
    return () => {
      cancelled = true;
    };
  }, [project.id, rules]);

  async function saveRules(nextRules: ApprovalPolicyRule[], note: string) {
    setSaving(true);
    setError(null);
    setSavedNote(null);
    const nowIso = new Date().toISOString();
    const nextDoc = buildPoliciesDocument(doc, nextRules, nowIso);
    try {
      const body: ProjectUpdateBody = { approval_policies: nextDoc };
      await updateProject(project.id, body);
      setDoc(nextDoc);
      setSavedNote(note);
      router.refresh();
    } catch (err: unknown) {
      setError(extractErrorMessage(err, "Save failed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveDraft(e: React.FormEvent) {
    e.preventDefault();
    if (!canSave) return;
    const nextRule = ruleFromDraft(draft, version + 1, new Date().toISOString());
    const nextRules =
      editingId === null
        ? [...rules, nextRule]
        : rules.map((rule) => (getRuleId(rule) === editingId ? nextRule : rule));
    await saveRules(nextRules, editingId === null ? "Policy saved." : "Policy updated.");
    setEditingId(null);
  }

  async function handlePreview() {
    if (errors.length > 0) return;
    setPreviewing(true);
    setError(null);
    try {
      const tasks: TaskRead[] = await listAllTasks(project.id, {
        process_status: TaskStatus.DONE,
      });
      const rule = ruleFromDraft(draft, version + 1, new Date().toISOString());
      setPreviewStats(previewRule(rule, tasks));
    } catch (err: unknown) {
      setError(extractErrorMessage(err, "Preview failed"));
    } finally {
      setPreviewing(false);
    }
  }

  function editRule(rule: ApprovalPolicyRule) {
    setDraft(draftFromRule(rule));
    setEditingId(getRuleId(rule));
    setPreviewStats(null);
    setError(null);
    setSavedNote(null);
  }

  async function toggleRule(rule: ApprovalPolicyRule) {
    const id = getRuleId(rule);
    const nextRules = rules.map((r) =>
      getRuleId(r) === id ? { ...r, enabled: r.enabled === false } : r,
    );
    await saveRules(nextRules, "Policy toggle saved.");
  }

  async function deleteRule(rule: ApprovalPolicyRule) {
    const id = getRuleId(rule);
    await saveRules(
      rules.filter((r) => getRuleId(r) !== id),
      "Policy deleted.",
    );
    if (editingId === id) {
      setEditingId(null);
      setDraft(blankDraft());
    }
  }

  async function duplicateRule(rule: ApprovalPolicyRule) {
    const copy = trimRuleForDuplicate(rule);
    await saveRules([...rules, copy], "Policy duplicated.");
  }

  function updateCondition(
    id: string,
    patch: Partial<{
      field: ConditionField;
      op: ConditionOperator;
      value: string;
    }>,
  ) {
    setDraft((current) => ({
      ...current,
      conditions: current.conditions.map((condition) => {
        if (condition.id !== id) return condition;
        const nextField = patch.field ?? condition.field;
        const def = FIELD_DEFINITIONS.find((f) => f.value === nextField);
        const nextOp =
          patch.op ??
          (def?.ops.includes(condition.op) ? condition.op : def?.ops[0]) ??
          condition.op;
        return { ...condition, ...patch, field: nextField, op: nextOp };
      }),
    }));
    setPreviewStats(null);
  }

  return (
    <section
      aria-labelledby="approval-policies-heading"
      className="flex flex-col gap-4"
    >
      <header className="flex flex-col gap-1">
        <h2
          id="approval-policies-heading"
          className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
        >
          Approval policies
        </h2>
        <p className="text-[12px] leading-5 text-zinc-500 dark:text-zinc-400">
          Version {version}. Policies are stored on the project and evaluated before
          an approval pause reaches the operator.
        </p>
      </header>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(420px,0.9fr)]">
        <div className="rounded-md border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
          <div className="flex items-center justify-between border-b border-zinc-200 px-3 py-2 dark:border-zinc-800">
            <div className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Policy list
            </div>
            <button
              type="button"
              onClick={() => {
                setDraft(blankDraft());
                setEditingId(null);
                setPreviewStats(null);
              }}
              className="inline-flex min-h-[36px] items-center gap-1 rounded border border-zinc-300 px-2 py-1 text-xs font-medium text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
              data-approval-policy-new
            >
              <Icon name="plus" size={14} />
              New
            </button>
          </div>

          <div className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {rules.length === 0 ? (
              <p className="px-3 py-5 text-sm text-zinc-500 dark:text-zinc-400">
                No policies yet.
              </p>
            ) : (
              rules.map((rule) => {
                const id = getRuleId(rule);
                const stats = statsByRule[id];
                return (
                  <div
                    key={id}
                    className="grid gap-3 px-3 py-3 md:grid-cols-[minmax(0,1fr)_auto]"
                    data-approval-policy-row={id}
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <label className="inline-flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-300">
                          <input
                            type="checkbox"
                            checked={rule.enabled !== false}
                            onChange={() => toggleRule(rule)}
                            disabled={saving}
                            data-approval-policy-enabled={id}
                            className="h-4 w-4 rounded border-zinc-300"
                          />
                          Enabled
                        </label>
                        <span className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                          {rule.name ?? "(unnamed policy)"}
                        </span>
                        <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                          {actionLabel(rule.action)}
                        </span>
                      </div>
                      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-zinc-500 dark:text-zinc-400">
                        <span>
                          Hit count 7d:{" "}
                          <span className="font-mono text-zinc-700 dark:text-zinc-200">
                            {statsLoading ? "..." : stats?.hitCount ?? 0}
                          </span>
                        </span>
                        <span>
                          Last-fired-at:{" "}
                          <span className="font-mono text-zinc-700 dark:text-zinc-200">
                            {formatDate(stats?.lastMatchedAt)}
                          </span>
                        </span>
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => editRule(rule)}
                        className="min-h-[36px] rounded border border-zinc-300 px-2 py-1 text-xs font-medium text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => duplicateRule(rule)}
                        disabled={saving}
                        className="min-h-[36px] rounded border border-zinc-300 px-2 py-1 text-xs font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
                      >
                        Duplicate
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteRule(rule)}
                        disabled={saving}
                        className="min-h-[36px] rounded border border-red-200 px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-50 dark:border-red-900/60 dark:text-red-300 dark:hover:bg-red-950/30"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        <form
          onSubmit={handleSaveDraft}
          className="flex flex-col gap-4 rounded-md border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900"
          data-approval-policy-editor
        >
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              {editingId === null ? "New policy" : "Edit policy"}
            </h3>
            <label className="inline-flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-300">
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(e) => {
                  setDraft((current) => ({ ...current, enabled: e.target.checked }));
                  setPreviewStats(null);
                }}
                className="h-4 w-4 rounded border-zinc-300"
              />
              Enabled
            </label>
          </div>

          <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
            Label
            <input
              value={draft.name}
              onChange={(e) => {
                setDraft((current) => ({ ...current, name: e.target.value }));
                setPreviewStats(null);
              }}
              className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
              data-approval-policy-name
            />
          </label>

          <fieldset className="flex flex-col gap-2">
            <legend className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Trigger
            </legend>
            <select
              value={draft.trigger}
              onChange={(e) => {
                setDraft((current) => ({
                  ...current,
                  trigger: e.target.value as ApprovalPolicyDraft["trigger"],
                }));
                setPreviewStats(null);
              }}
              className="rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
              data-approval-policy-trigger
            >
              {TRIGGERS.map((trigger) => (
                <option key={trigger.value} value={trigger.value}>
                  {trigger.label}
                </option>
              ))}
            </select>
          </fieldset>

          <fieldset className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <legend className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Condition
              </legend>
              <select
                value={draft.logic}
                onChange={(e) => {
                  setDraft((current) => ({
                    ...current,
                    logic: e.target.value as ConditionLogic,
                  }));
                  setPreviewStats(null);
                }}
                className="rounded border border-zinc-300 bg-white px-2 py-1 text-xs text-zinc-900 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                data-approval-policy-logic
              >
                <option value="all">AND</option>
                <option value="any">OR</option>
              </select>
            </div>

            <datalist id="approval-policy-fields">
              {FIELD_DEFINITIONS.map((field) => (
                <option key={field.value} value={field.value}>
                  {field.label}
                </option>
              ))}
            </datalist>

            <div className="flex flex-col gap-2">
              {draft.conditions.map((condition) => {
                const def = FIELD_DEFINITIONS.find((f) => f.value === condition.field);
                const ops = def?.ops ?? ["contains"];
                const rowError = conditionError(condition);
                return (
                  <div
                    key={condition.id}
                    className="grid gap-2 md:grid-cols-[minmax(0,1fr)_120px_minmax(0,1fr)_auto]"
                    data-approval-policy-condition
                  >
                    <label className="text-[11px] font-medium text-zinc-600 dark:text-zinc-300">
                      Field
                      <input
                        list="approval-policy-fields"
                        value={condition.field}
                        onChange={(e) =>
                          updateCondition(condition.id, {
                            field: e.target.value as ConditionField,
                          })
                        }
                        className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-xs text-zinc-900 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                      />
                    </label>
                    <label className="text-[11px] font-medium text-zinc-600 dark:text-zinc-300">
                      Op
                      <select
                        value={condition.op}
                        onChange={(e) =>
                          updateCondition(condition.id, {
                            op: e.target.value as ConditionOperator,
                          })
                        }
                        className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-xs text-zinc-900 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                      >
                        {ops.map((op) => (
                          <option key={op} value={op}>
                            {op}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="text-[11px] font-medium text-zinc-600 dark:text-zinc-300">
                      Value
                      <input
                        value={condition.value}
                        onChange={(e) =>
                          updateCondition(condition.id, { value: e.target.value })
                        }
                        placeholder={def?.placeholder}
                        aria-invalid={rowError !== null}
                        className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                      />
                    </label>
                    <button
                      type="button"
                      onClick={() => {
                        setDraft((current) => ({
                          ...current,
                          conditions:
                            current.conditions.length > 1
                              ? current.conditions.filter((c) => c.id !== condition.id)
                              : current.conditions,
                        }));
                        setPreviewStats(null);
                      }}
                      disabled={draft.conditions.length <= 1}
                      className="self-end rounded border border-zinc-300 px-2 py-1.5 text-xs text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
                    >
                      Remove
                    </button>
                    {rowError && (
                      <p className="md:col-span-4 text-[11px] text-red-700 dark:text-red-300">
                        {rowError}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>

            <button
              type="button"
              onClick={() => {
                setDraft((current) => ({
                  ...current,
                  conditions: [...current.conditions, newCondition()],
                }));
                setPreviewStats(null);
              }}
              className="inline-flex min-h-[36px] w-fit items-center gap-1 rounded border border-zinc-300 px-2 py-1 text-xs font-medium text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
              data-approval-policy-add-condition
            >
              <Icon name="plus" size={14} />
              Add condition
            </button>
          </fieldset>

          <fieldset className="flex flex-col gap-2">
            <legend className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Action
            </legend>
            <div className="grid gap-2 sm:grid-cols-3">
              {ACTIONS.map((action) => (
                <label
                  key={action.value}
                  className="flex min-h-[44px] items-center gap-2 rounded border border-zinc-200 px-2 py-2 text-xs text-zinc-700 dark:border-zinc-800 dark:text-zinc-200"
                >
                  <input
                    type="radio"
                    name="approval-policy-action"
                    checked={draft.action === action.value}
                    onChange={() => {
                      setDraft((current) => ({ ...current, action: action.value }));
                      setPreviewStats(null);
                    }}
                  />
                  {action.label}
                </label>
              ))}
            </div>
            {draft.action === "auto_approve" && (
              <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                Default answer
                <input
                  value={draft.defaultAnswer}
                  onChange={(e) =>
                    setDraft((current) => ({
                      ...current,
                      defaultAnswer: e.target.value,
                    }))
                  }
                  className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                />
              </label>
            )}
            {draft.action === "require_attention" && (
              <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                Route label
                <input
                  value={draft.label}
                  onChange={(e) =>
                    setDraft((current) => ({ ...current, label: e.target.value }))
                  }
                  className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                />
              </label>
            )}
          </fieldset>

          {errors.length > 0 && (
            <ul className="rounded border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-700 dark:border-red-900/60 dark:bg-red-950/20 dark:text-red-300">
              {errors.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}

          {error !== null && (
            <p role="alert" className="text-[12px] text-red-700 dark:text-red-300">
              {error}
            </p>
          )}
          {savedNote !== null && (
            <p role="status" className="text-[12px] text-green-700 dark:text-green-300">
              {savedNote}
            </p>
          )}

          {previewStats !== null && (
            <div className="rounded border border-zinc-200 bg-zinc-50 p-3 dark:border-zinc-800 dark:bg-zinc-950">
              <div className="flex flex-wrap items-center gap-3 text-xs text-zinc-600 dark:text-zinc-300">
                <span>
                  Would match{" "}
                  <span className="font-mono font-semibold text-zinc-900 dark:text-zinc-100">
                    {previewStats.hitCount}
                  </span>{" "}
                  closed tasks
                </span>
                <span>
                  Last-fired-at{" "}
                  <span className="font-mono text-zinc-900 dark:text-zinc-100">
                    {formatDate(previewStats.lastMatchedAt)}
                  </span>
                </span>
              </div>
              {previewStats.samples.length > 0 && (
                <ul className="mt-2 flex flex-col gap-1 text-[12px] text-zinc-600 dark:text-zinc-300">
                  {previewStats.samples.map((sample) => (
                    <li key={sample.id} className="truncate">
                      #{sample.id} {sample.title}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <div className="flex flex-wrap justify-end gap-2">
            <button
              type="button"
              onClick={handlePreview}
              disabled={previewing || errors.length > 0}
              className="min-h-[44px] rounded border border-zinc-300 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 sm:min-h-0 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
              data-approval-policy-preview
            >
              {previewing ? "Previewing..." : "Preview"}
            </button>
            <button
              type="submit"
              disabled={!canSave}
              className="min-h-[44px] rounded border border-emerald-600 bg-emerald-600 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-white hover:bg-emerald-700 disabled:opacity-50 sm:min-h-0 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
              data-approval-policy-save
            >
              {saving ? "Saving..." : "Save policy"}
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}
