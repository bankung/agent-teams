// Feature flags — read from NEXT_PUBLIC_* env vars so values are inlined
// at Next.js build time and available in both Server Components and Client
// Components without a separate API call.

// Operator-opt-in personal-use feature; default off for pilot installs.
export const FINANCE_PANELS_ENABLED =
  process.env.NEXT_PUBLIC_FINANCE_PANELS_ENABLED === "true";
