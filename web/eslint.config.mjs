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
      // set-state-in-effect promoted to "error" (ms49 Phase E, #2494): the ~50
      // sites from the Next 16 upgrade were driven to 0 active warnings by phases
      // A-D — idiomatic sites get usePersistentState / useAsyncData hooks, modals
      // get key-remount, and genuine smells were fixed (clamps during render, Board
      // URL-sync derived, filter-pagination resetted without an effect). The ~7
      // remaining sites carry scoped justified eslint-disable-line comments.
      // Rule is now "error" to guard against future regressions.
      "react-hooks/set-state-in-effect": "error",
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
