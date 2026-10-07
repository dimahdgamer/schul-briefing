import { api } from "../api.js";
import { logo, passwordField, wirePasswordToggles } from "../ui.js";

function greeting() {
  const hour = new Date().getHours();
  if (hour < 11) return "Guten Morgen.";
  if (hour < 18) return "Hallo.";
  return "Guten Abend.";
}

export async function render(root, params, ctx) {
  root.innerHTML = `
    <main class="login">
      <div class="login-card reveal">
        ${logo(48)}
        <h1 class="display">${greeting()}</h1>
        <form novalidate>
          <label class="visually-hidden" for="pw">Passwort</label>
          ${passwordField({ id: "pw", placeholder: "Passwort oder Zugangscode" })}
          <p class="form-error" role="alert"></p>
          <button class="btn primary" type="submit">Anmelden</button>
        </form>
      </div>
    </main>`;

  wirePasswordToggles(root);
  const form = root.querySelector("form");
  if (window.matchMedia("(pointer: fine)").matches) form.password.focus();
  const error = root.querySelector(".form-error");
  const button = form.querySelector("button");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const password = form.password.value;
    if (!password) {
      error.textContent = "Bitte das Passwort eingeben.";
      return;
    }
    button.disabled = true;
    error.textContent = "";
    try {
      await api("/login", { method: "POST", body: { password } });
      location.replace("/#/heute");
      location.reload();
    } catch (err) {
      error.textContent = err.message || "Anmeldung fehlgeschlagen.";
      button.disabled = false;
      form.password.select();
    }
  });
}
