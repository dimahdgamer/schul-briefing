export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

let onUnauthorized = () => {};

export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler;
}

export async function api(path, { method = "GET", body } = {}) {
  let response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      credentials: "same-origin",
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Keine Verbindung. Angezeigt wird der zuletzt geladene Stand, falls vorhanden.");
  }
  const isJson = (response.headers.get("content-type") || "").includes("application/json");
  const data = isJson ? await response.json().catch(() => null) : null;
  if (response.status === 401 && path !== "/login") {
    onUnauthorized();
    throw new ApiError(401, "Nicht angemeldet");
  }
  if (!response.ok) {
    throw new ApiError(response.status, (data && data.detail) || `Fehler ${response.status}`);
  }
  return data;
}
