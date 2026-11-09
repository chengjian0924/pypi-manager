import { navigate } from "../router.js";
import { login } from "../store/auth.js";
import { setView } from "../store/ui.js";
import { toast } from "../components/toast.js";

const LOGO_ICON = `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M21 8.5 12 3 3 8.5v7L12 21l9-5.5v-7Z"/><path d="M3 8.5 12 14l9-5.5"/><path d="M12 14v7"/></svg>`;

/**
 * @param {HTMLElement} container
 * @param {string} [message]
 */
export function mount(container, message = "") {
  setView("login");
  container.innerHTML = `
  <main class="view login-view" id="viewLogin">
    <div class="login-card">
      <div class="login-logo" aria-hidden="true">${LOGO_ICON}</div>
      <h2>登录 Wheelhouse</h2>
      <form class="login-form" id="loginForm" novalidate>
        <label class="login-field">
          <span>账号</span>
          <input type="text" id="loginUser" placeholder="请输入账号" autocomplete="username" aria-label="账号">
        </label>
        <label class="login-field">
          <span>密码</span>
          <input type="password" id="loginPass" placeholder="请输入密码" autocomplete="current-password" aria-label="密码">
        </label>
        <p class="login-err" id="loginErr" role="alert" hidden></p>
        <button class="btn btn-primary login-submit" id="loginBtn" type="submit">登录</button>
      </form>
    </div>
  </main>`;

  const form = container.querySelector("#loginForm");
  const userInput = container.querySelector("#loginUser");
  const passInput = container.querySelector("#loginPass");
  const errorNode = container.querySelector("#loginErr");
  const button = container.querySelector("#loginBtn");

  if (message) {
    errorNode.textContent = message;
    errorNode.hidden = false;
  }

  const fail = text => {
    errorNode.textContent = text;
    errorNode.hidden = false;
    button.disabled = false;
    button.textContent = "登录";
    return false;
  };

  form.addEventListener("submit", async event => {
    event.preventDefault();
    const username = userInput.value.trim();
    const password = passInput.value;

    if (!username && !password) { userInput.focus(); return fail("请输入账号和密码"); }
    if (!username) { userInput.focus(); return fail("请输入账号"); }
    if (!password) { passInput.focus(); return fail("请输入密码"); }

    button.disabled = true;
    button.textContent = "登录中…";
    errorNode.hidden = true;

    try {
      await login(username, password);
      passInput.value = "";
      toast("登录成功");
      navigate("/", { replace: true });
    } catch (error) {
      if (error.status === 401) return fail("账号或密码错误，请检查后重新输入");
      if (error.status) return fail("登录失败：服务器返回 HTTP " + error.status);
      return fail("无法连接服务器，请检查网络或确认服务已启动");
    }
    return false;
  });

  if (!userInput.value) {
    setTimeout(() => {
      try { userInput.focus(); } catch (e) { /* ignore */ }
    }, 60);
  }
}
