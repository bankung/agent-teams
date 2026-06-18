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
      // eslint-plugin-react-hooks@7). Downgraded from "error" to "warn" per
      // #2487 (Option A) so the Next 16 upgrade lands green. Genuine
      // set-state-in-effect cleanup is tracked in follow-up task #2489.
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/refs": "warn",
      "react-hooks/immutability": "warn",
      "react-hooks/preserve-manual-memoization": "warn",
    },
  },
]);
