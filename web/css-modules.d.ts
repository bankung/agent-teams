// Type declarations for CSS files imported as side-effects.
// Used by dynamic imports (e.g. import("driver.js/dist/driver.css")) so
// TypeScript doesn't error on the module resolution.
declare module "*.css" {
  const content: Record<string, string>;
  export default content;
}
