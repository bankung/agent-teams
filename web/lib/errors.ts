import { HttpError } from "./api";

// extractErrorMessage — normalises any caught value into a human-readable
// string. Handles HttpError.message, Error.message, and an optional fallback
// for anything else. Replaces the 30+ verbatim
// `err instanceof Error ? err.message : "<fallback>"` sites across web/.
export function extractErrorMessage(err: unknown, fallback = "An error occurred"): string {
  if (err instanceof HttpError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}
