const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("autoclipper_token");
}

export function setToken(token: string) {
  localStorage.setItem("autoclipper_token", token);
}

export function clearToken() {
  localStorage.removeItem("autoclipper_token");
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = getToken();
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (!(options.body instanceof FormData) && options.body) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_URL}${path}`, { ...options, headers });

  if (!response.ok) {
    let message = response.statusText;
    try {
      const data = await response.json();
      if (typeof data.detail === "string") {
        // The common case: our own HTTPException(detail="...").
        message = data.detail;
      } else if (Array.isArray(data.detail)) {
        // FastAPI's own 422 validation-error shape is a list of {msg, loc, ...} objects, not a
        // string — stringifying it directly renders as "[object Object]" in the UI.
        message = data.detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join("; ") || message;
      } else if (typeof data.message === "string") {
        message = data.message;
      }
    } catch {
      // ignore body parse errors
    }
    throw new ApiError(response.status, message);
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path, { method: "GET" }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  postForm: <T>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export function apiBaseUrl(): string {
  return API_URL;
}

export function authToken(): string | null {
  return getToken();
}

/** Fetches a short-lived presigned S3/MinIO URL for a clip. The clip detail endpoint is
 * JWT-protected, so this must go through the authenticated `api` client rather than being
 * used directly as a <video src> or link href (neither can attach an Authorization header). */
export async function getClipDownloadUrl(clipId: string): Promise<string> {
  const res = await api.get<{ url: string }>(`/clips/${clipId}/download`);
  return res.url;
}

/** Same as getClipDownloadUrl but for the ORIGINAL source video — used by the clip editor's
 * live preview so trim/crop can be scrubbed against the full source. */
export async function getVideoDownloadUrl(videoId: string): Promise<string> {
  const res = await api.get<{ url: string }>(`/videos/${videoId}/download`);
  return res.url;
}
