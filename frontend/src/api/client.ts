/**
 * The one place the frontend turns an HTTP response into data or an error.
 *
 * Failed requests used to be handled ad hoc at each call site: some threw a
 * fixed string and discarded the server's explanation, and others read
 * `data.detail` straight into `new Error(...)`. That rendered FastAPI's 422
 * validation errors (a list of objects) as "[object Object]", and it threw a
 * JSON parse error instead of the intended message whenever a proxy answered
 * with HTML or an empty body.
 */

interface ValidationIssue {
  loc?: unknown;
  msg?: unknown;
}

/**
 * The human-readable part of an error body, or null if there isn't one.
 *
 * FastAPI sends `detail` as a string for an HTTPException and as a list of
 * `{loc, msg}` for request-validation failures.
 */
export function errorDetail(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;

  if (typeof detail === "string") return detail.trim() || null;

  if (Array.isArray(detail)) {
    const messages = detail
      .map((item: unknown) => {
        if (typeof item === "string") return item;
        const issue = item as ValidationIssue | null;
        if (!issue || typeof issue.msg !== "string") return null;
        // "body" is where every POST field lives; naming it adds nothing.
        const path = Array.isArray(issue.loc) ? issue.loc.filter((p) => p !== "body").join(".") : "";
        return path ? `${path}: ${issue.msg}` : issue.msg;
      })
      .filter((m): m is string => !!m);
    return messages.length > 0 ? messages.join("; ") : null;
  }

  return null;
}

/** A message for a failed response, falling back to `fallback` plus the status code. */
export async function readErrorMessage(res: Response, fallback: string): Promise<string> {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // Not JSON (an HTML error page from a proxy, an empty 502): use the fallback.
  }
  return errorDetail(body) ?? `${fallback} (HTTP ${res.status})`;
}

/** `fetch` + status check + JSON decode. Throws an `Error` carrying the server's reason. */
export async function apiFetch<T>(url: string, fallback: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(await readErrorMessage(res, fallback));
  return (await res.json()) as T;
}

/** `apiFetch` for a JSON POST. */
export function apiPost<T>(url: string, body: unknown, fallback: string): Promise<T> {
  return apiFetch<T>(url, fallback, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
