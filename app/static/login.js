document.getElementById("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const errorEl = document.getElementById("login-error");
  errorEl.hidden = true;

  try {
    const { access_token } = await Api.login(
      document.getElementById("username").value,
      document.getElementById("password").value,
    );
    localStorage.setItem("access_token", access_token);
    window.location.href = "/app/tasks.html";
  } catch (err) {
    showError(errorEl, err.message);
  }
});
