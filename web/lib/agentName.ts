// Agent-name regex — FE mirror of api/src/schemas/agent_metadata.AGENT_NAME_PATTERN
// (Kanban #1016 / #2481). Lower-case alphanumeric segments joined by single
// hyphens. Used by AgentFormModal for fast client-side feedback; the SERVER
// (Pydantic AgentWrite + the file validator) remains the authority.
//
// Kept as a standalone module (not buried in a component) so both the form and
// its tests import the EXACT same source — a drift here would desync the
// client gate from the BE contract.
export const AGENT_NAME_PATTERN = "^[a-z0-9]+(-[a-z0-9]+)*$";

// Fresh RegExp per reference is avoided; this is stateless (no /g flag) so a
// shared instance is safe to reuse across .test() calls.
export const AGENT_NAME_RE = new RegExp(AGENT_NAME_PATTERN);
