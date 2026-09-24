// Shared API client for the browser UI.
const Api = {
  token: () => localStorage.getItem("access_token"),

  headers() {
    const token = this.token();
    return token
      ? { "Content-Type": "application/json", Authorization: `Bearer ${token}` }
      : { "Content-Type": "application/json" };
  },

  async request(method, path, body) {
    const response = await fetch(path, {
      method,
      headers: this.headers(),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (response.status === 204) return null;

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      // The API's error contract is {code, detail} — surface detail, keep
      // code available so callers can branch without string matching.
      const error = new Error(payload.detail || `Request failed (${response.status})`);
      error.code = payload.code;
      error.status = response.status;
      throw error;
    }
    return payload;
  },

  login: (username, password) => Api.request("POST", "/auth/login", { username, password }),
  logout: () => Api.request("POST", "/auth/logout"),
  me: () => Api.request("GET", "/me"),
  listTasks: (params = "") => Api.request("GET", `/tasks${params}`),
  createTask: (title, description) => Api.request("POST", "/tasks", { title, description }),
  updateTask: (id, patch) => Api.request("PATCH", `/tasks/${id}`, patch),
  deleteTask: (id) => Api.request("DELETE", `/tasks/${id}`),
};

function clearSession() {
  localStorage.removeItem("access_token");
}

function requireSession() {
  if (!Api.token()) {
    window.location.href = "/app/index.html";
    return false;
  }
  return true;
}

function showError(el, message) {
  el.textContent = message;
  el.hidden = false;
}
