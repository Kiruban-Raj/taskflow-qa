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

  // Who are we serving? Resolved by the server from the address this page
  // was loaded from, so the page never has to know or guess the customer.
  tenant: () => Api.request("GET", "/tenant"),

  login: (username, password) => Api.request("POST", "/auth/login", { username, password }),
  logout: () => Api.request("POST", "/auth/logout"),
  me: () => Api.request("GET", "/me"),
  listTasks: (params = "") => Api.request("GET", `/tasks${params}`),
  createTask: (title, description) => Api.request("POST", "/tasks", { title, description }),
  updateTask: (id, patch) => Api.request("PATCH", `/tasks/${id}`, patch),
  deleteTask: (id) => Api.request("DELETE", `/tasks/${id}`),
  bulkDeleteDone: () => Api.request("POST", "/tasks/bulk-delete"),
};

// Apply this customer's branding to whichever page is open.
//
// Branding is fetched rather than hardcoded so that onboarding a customer
// stays a config change. Three copies of the HTML with different logos is
// the thing this avoids.
async function applyBranding() {
  try {
    const tenant = await Api.tenant();
    const name = document.getElementById("product-name");
    if (name) name.textContent = tenant.product_name;
    document.title = tenant.product_name;
    return tenant;
  } catch {
    // Branding is decoration. If it fails, the page must still work -
    // never let a cosmetic call block signing in.
    return null;
  }
}

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
