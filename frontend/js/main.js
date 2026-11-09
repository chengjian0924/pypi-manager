import { fetchConfig } from "./api/admin.js";
import { setUnauthorizedHandler } from "./api/client.js";
import { clearAuth } from "./store/auth.js";
import { setConfig } from "./store/ui.js";
import { renderHeader } from "./components/header.js";
import { start, showLogin } from "./router.js";

function buildShell() {
  const app = document.getElementById("app");
  app.innerHTML = `
  <div class="wrap">
    <div id="headerMount"></div>
    <div id="view-root"></div>
  </div>
  <div class="toast" id="toast" role="status" aria-live="polite"></div>
  <div class="modal" id="modal">
    <div class="sheet" id="sheet" role="dialog" aria-modal="true"></div>
  </div>`;

  const modal = document.getElementById("modal");
  modal.addEventListener("click", event => {
    if (event.target === modal) modal.classList.remove("on");
  });

  renderHeader(document.getElementById("headerMount"));
}

async function boot() {
  const config = await fetchConfig();
  setConfig(config);
  buildShell();

  setUnauthorizedHandler(() => {
    clearAuth();
    if (location.hash === "#/login") return;
    showLogin("登录状态已失效，请重新登录");
  });

  start();
}

boot();
