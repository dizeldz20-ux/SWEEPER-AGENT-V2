// Thin fetch wrapper for the same-origin agent API.
//
// The SPA is served by the Flask agent, so every call is same-origin: no CORS,
// and the browser's HTTP Basic session (if the agent is auth-gated) is attached
// automatically via credentials:'same-origin'. There is NO token in the bundle.
//
// Two body encodings are used by the backend:
//   - JSON  (application/json)               -> /api/* writes, /chat, connectors
//   - form  (x-www-form-urlencoded)          -> /v6/* POSTs (read request.form)

export const API_BASE = ''; // same-origin

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown, message?: string) {
    super(message || `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

export interface RequestOptions {
  method?: string;
  json?: unknown;
  form?: Record<string, string | number>;
  signal?: AbortSignal;
}

export async function request<T = unknown>(
  path: string,
  opts: RequestOptions = {},
): Promise<T> {
  const {method = 'GET', json, form, signal} = opts;
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;

  if (json !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(json);
  } else if (form !== undefined) {
    headers['Content-Type'] = 'application/x-www-form-urlencoded';
    body = new URLSearchParams(
      Object.entries(form).map(([k, v]) => [k, String(v)]),
    ).toString();
  }

  const resp = await fetch(API_BASE + path, {
    method,
    headers,
    body,
    credentials: 'same-origin',
    signal,
  });

  const text = await resp.text();
  let parsed: unknown = undefined;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }

  if (!resp.ok) {
    throw new ApiError(resp.status, parsed, `${method} ${path} -> ${resp.status}`);
  }
  return parsed as T;
}

// Build an absolute (same-origin) URL for <a href>/window.open downloads.
export function url(path: string): string {
  return API_BASE + path;
}
