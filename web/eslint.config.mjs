// ESLint flat config — migrated from .eslintrc.json for ESLint 9 / Next 16
// (#2487). Replicates the legacy `{ extends: ["next/core-web-vitals"] }` plus
// the `react-hooks/exhaustive-deps: "warn"` override (downgraded from the
// Next default "error" so the documented AcEditor.tsx warning does not block
// CI). `eslint-config-next/core-web-vitals` exports a flat-config array in v16,
// so it is spread directly via `extends`.
import { defineConfig } from "eslint/config";
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";

export default defineConfig([
  {
    extends: [...nextCoreWebVitals],
    rules: {
      "react-hooks/exhaustive-deps": "warn",
      // React-Compiler lint family introduced by eslint-config-next@16 (via
      // eslint-plugin-react-hooks@7); none existed in the Next-14 baseline.
      //
      // set-state-in-effect stays "warn" DELIBERATELY (#2489): its ~41 sites are
      // idiomatic effects that sync with external systems — SSR-hydrate-from-
      // localStorage, async data-fetch, prop-reset-on-open — which React's docs
      // bless. A scoped remediation plan for these is tracked separately under
      // #2489; do not blanket-promote without that pass.
      "react-hooks/set-state-in-effect": "warn",
      // The remaining Compiler rules are "error": their few sites are genuinely
      // actionable and have been fixed (#2489) — refs written during render
      // moved into effects (AgentFormModal, ModalShell); a use-before-declare
      // pushToast reordered in Board.
      "react-hooks/refs": "error",
      "react-hooks/immutability": "error",
      "react-hooks/preserve-manual-memoization": "error",
    },
  },
]);
