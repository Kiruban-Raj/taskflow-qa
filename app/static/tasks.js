const errorEl = document.getElementById("task-error");
const listEl = document.getElementById("task-list");
const emptyEl = document.getElementById("empty-state");

const NEXT_STATUS = { todo: "in_progress", in_progress: "done", done: "in_progress" };
const NEXT_LABEL = { todo: "Start", in_progress: "Complete", done: "Reopen" };

function currentQuery() {
  const params = new URLSearchParams();
  const search = document.getElementById("search").value.trim();
  const status = document.getElementById("status-filter").value;
  if (search) params.set("q", search);
  if (status) params.set("status_filter", status);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

function renderTask(task) {
  const li = document.createElement("li");
  li.dataset.testid = "task-item";
  li.dataset.taskId = task.id;

  const title = document.createElement("span");
  title.className = "title";
  title.dataset.testid = "task-title";
  title.textContent = task.title;

  const status = document.createElement("span");
  status.className = "status";
  status.dataset.testid = "task-status";
  status.dataset.status = task.status;
  status.textContent = task.status.replace("_", " ");

  const spacer = document.createElement("span");
  spacer.className = "spacer";

  const advance = document.createElement("button");
  advance.className = "secondary";
  advance.dataset.testid = "task-advance";
  advance.textContent = NEXT_LABEL[task.status];
  advance.addEventListener("click", () => mutate(() =>
    Api.updateTask(task.id, { status: NEXT_STATUS[task.status] })));

  const remove = document.createElement("button");
  remove.className = "danger";
  remove.dataset.testid = "task-delete";
  remove.textContent = "Delete";
  remove.addEventListener("click", () => mutate(() => Api.deleteTask(task.id)));

  li.append(title, status, spacer, advance, remove);
  return li;
}

async function refresh() {
  const tasks = await Api.listTasks(currentQuery());
  listEl.innerHTML = "";
  emptyEl.hidden = tasks.length > 0;
  tasks.forEach((task) => listEl.appendChild(renderTask(task)));
}

async function mutate(action) {
  errorEl.hidden = true;
  try {
    await action();
    await refresh();
  } catch (err) {
    showError(errorEl, err.message);
  }
}

document.getElementById("create-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const titleEl = document.getElementById("new-title");
  const descEl = document.getElementById("new-description");
  const title = titleEl.value;
  const description = descEl.value;
  mutate(async () => {
    await Api.createTask(title, description);
    titleEl.value = "";
    descEl.value = "";
  });
});

document.getElementById("search").addEventListener("input", () => refresh());
document.getElementById("status-filter").addEventListener("change", () => refresh());

document.getElementById("logout-button").addEventListener("click", async () => {
  try {
    await Api.logout();
  } finally {
    // Drop the local token even if the revoke call failed, so the user is
    // never stranded in a half-signed-in state.
    clearSession();
    window.location.href = "/app/index.html";
  }
});

if (requireSession()) {
  Api.me()
    .then((user) => { document.getElementById("current-user").textContent = user.username; })
    .catch(() => { clearSession(); window.location.href = "/app/index.html"; });
  refresh();
}
