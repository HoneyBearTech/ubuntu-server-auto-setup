// Talks to the server's JSON API. Every post carries the session's CSRF token in the X-CSRF-Token header
// (the server refuses a post without it), and every call names the page it was made from (X-USAS-Page), so
// a sign-in that expired can bring the user back to it.

let csrfToken = "";

export function setCsrf(token: string): void {
  csrfToken = token;
}

export function csrf(): string {
  return csrfToken;
}

export interface Reply<T = Record<string, unknown>> {
  status: number;
  ok: boolean;
  data: T;
}

// Where the browser goes when the server says a sign-in is needed: a full page load, so the next page starts
// with the new session's token. Tests replace it.
export const location_ = {
  assign(url: string): void {
    window.location.assign(url);
  },
};

async function call<T>(method: string, path: string, body?: FormData): Promise<Reply<T>> {
  const headers: Record<string, string> = { Accept: "application/json", "X-USAS-Page": window.location.pathname };
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken;
  let response: Response;
  try {
    response = await fetch(path, { method, headers, body, credentials: "same-origin", cache: "no-store" });
  } catch {
    return { status: 0, ok: false, data: { error: "The web UI didn't answer. Is the container running?" } as T };
  }
  const text = await response.text().catch(() => "");
  let data: T;
  try {
    data = JSON.parse(text) as T;
  } catch {
    data = { error: text.trim() || `The server answered ${response.status}.` } as T;
  }
  const redirect = (data as { redirect?: string }).redirect;
  if (response.status === 401 && redirect) {
    location_.assign(redirect);
  }
  const token = (data as { csrf?: string }).csrf;
  if (typeof token === "string" && token) setCsrf(token);
  return { status: response.status, ok: response.ok, data };
}

export function get<T>(path: string): Promise<Reply<T>> {
  return call<T>("GET", path);
}

export function post<T = Record<string, unknown>>(path: string, body: FormData | Record<string, string>): Promise<Reply<T>> {
  let form: FormData;
  if (body instanceof FormData) {
    form = body;
  } else {
    form = new FormData();
    for (const [key, value] of Object.entries(body)) form.append(key, value);
  }
  return call<T>("POST", path, form);
}

// The form's fields plus the button that submitted it (its name and value, such as action=test), as a
// browser would send them.
export function formData(form: HTMLFormElement, submitter?: HTMLElement | null): FormData {
  const data = new FormData(form);
  if (submitter instanceof HTMLButtonElement && submitter.name) {
    data.set(submitter.name, submitter.value);
  }
  return data;
}
