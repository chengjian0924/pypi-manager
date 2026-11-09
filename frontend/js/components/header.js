import { navigate } from "../router.js";
import { applyNewCredentials, authStore, clearAuth } from "../store/auth.js";
import { encodeCredentials } from "../api/auth.js";
import { changePassword } from "../api/users.js";
import { roleLabel } from "../utils/format.js";
import { initThemeToggle } from "./theme-toggle.js";
import { toast } from "./toast.js";

const BRAND_ICON = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 8.5 12 3 3 8.5v7L12 21l9-5.5v-7Z"/><path d="M3 8.5 12 14l9-5.5"/><path d="M12 14v7"/></svg>`;
const BACK_ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5"/><path d="m12 19-7-7 7-7"/></svg>`;
const UPLOAD_ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/></svg>`;
const KEY_ICON_16 = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="15" r="4"/><path d="m10.8 12.2 8.4-8.4M15 6l3 3M17 4l3 3"/></svg>`;
const PLUS_ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>`;
const CLOSE_ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>`;

/** 打开「修改密码」弹窗：所有已登录账号都能改自己的密码 */
function openPasswordDialog() {
  const state = authStore.getState();
  if (!state.authOk) return;
  const sheet = document.getElementById("sheet");
  const modal = document.getElementById("modal");
  if (!sheet || !modal) return;

  sheet.innerHTML = `
    <div class="sheet-head">
      <div>
        <h3>修改密码</h3>
        <p class="s">账号 ${state.user}${roleLabel(state.role) ? " · " + roleLabel(state.role) : ""}</p>
      </div>
      <button class="icon-btn" type="button" data-action="pwd-close" aria-label="关闭">${CLOSE_ICON}</button>
    </div>
    <div class="sheet-body">
      <div class="pwd-form">
        <div class="field">
          <label for="pwdOld">当前密码</label>
          <input type="password" id="pwdOld" autocomplete="current-password" placeholder="请输入当前密码…">
        </div>
        <div class="field">
          <label for="pwdNew">新密码</label>
          <input type="password" id="pwdNew" autocomplete="new-password" placeholder="至少 8 位…">
        </div>
        <div class="field">
          <label for="pwdNew2">确认新密码</label>
          <input type="password" id="pwdNew2" autocomplete="new-password" placeholder="再输入一次新密码…">
        </div>
    </div>
    <div class="sheet-foot">
      <button class="btn" type="button" data-action="pwd-close">取消</button>
      <button class="btn btn-primary" type="button" data-action="pwd-submit">保存新密码</button>
    </div>`;
  modal.classList.add("on");

  const close = () => modal.classList.remove("on");
  const oldInput = sheet.querySelector("#pwdOld");
  const newInput = sheet.querySelector("#pwdNew");
  const againInput = sheet.querySelector("#pwdNew2");
  const submitBtn = sheet.querySelector('[data-action="pwd-submit"]');

  sheet.querySelectorAll('[data-action="pwd-close"]').forEach(btn => {
    btn.addEventListener("click", close);
  });

  const submit = async () => {
    const oldPassword = oldInput ? oldInput.value : "";
    const newPassword = newInput ? newInput.value : "";
    const again = againInput ? againInput.value : "";
    if (!oldPassword) { toast("请输入当前密码", true); if (oldInput) oldInput.focus(); return; }
    if (newPassword.length < 8) { toast("新密码至少 8 位", true); if (newInput) newInput.focus(); return; }
    if (newPassword !== again) { toast("两次输入的新密码不一致", true); if (againInput) againInput.focus(); return; }
    if (newPassword === oldPassword) { toast("新密码不能与当前密码相同", true); if (newInput) newInput.focus(); return; }

    if (submitBtn) submitBtn.disabled = true;
    try {
      await changePassword(oldPassword, newPassword);
      applyNewCredentials(encodeCredentials(state.user, newPassword));
      close();
      toast("密码已更新，下次请用新密码登录");
    } catch (error) {
      if (error.status === 401) {
        close();
        clearAuth();
        toast("登录状态已失效，请重新登录", true);
        navigate("/login", { replace: true });
      } else {
        toast(error.message, true);
      }
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  };

  if (submitBtn) submitBtn.addEventListener("click", submit);
  [oldInput, newInput, againInput].forEach(input => {
    if (!input) return;
    input.addEventListener("keydown", event => {
      if (event.key === "Enter") { event.preventDefault(); submit(); }
    });
  });
  if (oldInput) oldInput.focus();
}

/**
 * 渲染顶部导航并绑定登录态相关交互。
 * @param {HTMLElement} container
 */
export function renderHeader(container) {
  container.innerHTML = `
  <header class="topbar">
    <div class="brand">
      <div class="logo" aria-hidden="true">${BRAND_ICON}</div>
      <div>
        <h1>Wheelhouse</h1>
        <p class="sub">内网 Python 包仓库</p>
      </div>
    </div>
    <div class="actions">
      <button class="btn btn-primary btn-upload" id="backNav" type="button">
        ${BACK_ICON}
        <span>返回主界面</span>
      </button>
      <button class="btn" id="backToList" type="button">返回账号列表</button>
      <button class="btn btn-primary btn-upload" id="uploadNav" type="button">
        ${UPLOAD_ICON}
        <span>上传分发包</span>
      </button>
      <button class="btn btn-primary" id="createUserNav" type="button">
        ${PLUS_ICON}
        <span>注册用户</span>
      </button>
      <button class="btn list-only" id="usersNav" type="button" style="display:none">用户管理</button>
      <button class="btn" id="auditNav" type="button" style="display:none">审计日志</button>
      <button class="btn list-only" id="exportNav" type="button" style="display:none">导出 CSV</button>
      <button class="btn list-only" id="settingsNav" type="button" style="display:none">客户端配置</button>
      <button class="btn" id="pwdNav" type="button">
        ${KEY_ICON_16}
        <span>修改密码</span>
      </button>
      <span class="user-chip" id="userChip" title="当前登录账号">
        <i class="dot"></i><span id="whoText">未登录</span>
      </span>
      <button class="btn" id="logoutBtn" type="button" style="display:none">退出</button>
      <button class="icon-btn" id="themeBtn" type="button" title="主题：跟随系统" aria-label="切换主题">
        <svg class="ico ico-auto" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
        <svg class="ico ico-light" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>
        <svg class="ico ico-dark" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79Z"/></svg>
      </button>
    </div>
  </header>`;

  const backBtn = container.querySelector("#backNav");
  const backToList = container.querySelector("#backToList");
  const uploadBtn = container.querySelector("#uploadNav");
  const createUserNav = container.querySelector("#createUserNav");
  const pwdNav = container.querySelector("#pwdNav");
  const usersNav = container.querySelector("#usersNav");
  const auditNav = container.querySelector("#auditNav");
  const exportNav = container.querySelector("#exportNav");
  const settingsNav = container.querySelector("#settingsNav");
  const logoutBtn = container.querySelector("#logoutBtn");
  const userChip = container.querySelector("#userChip");
  const whoText = container.querySelector("#whoText");
  const themeBtn = container.querySelector("#themeBtn");

  backBtn.addEventListener("click", () => navigate("/"));
  backToList.addEventListener("click", () => navigate("/users"));
  uploadBtn.addEventListener("click", () => navigate("/upload"));
  createUserNav.addEventListener("click", () => navigate("/users/new"));
  pwdNav.addEventListener("click", () => openPasswordDialog());
  usersNav.addEventListener("click", () => navigate("/users"));
  auditNav.addEventListener("click", () => navigate("/audit"));
  logoutBtn.addEventListener("click", () => {
    clearAuth();
    toast("已退出登录");
    navigate("/login", { replace: true });
  });

  initThemeToggle(themeBtn);

  const syncAuth = () => {
    const state = authStore.getState();
    const label = roleLabel(state.role);
    const root = document.documentElement;
    root.classList.toggle("is-auth", state.authOk);
    root.classList.toggle("is-admin", state.authOk && state.role === "admin");

    whoText.textContent = state.authOk
      ? ((state.user || "已登录") + (label ? " · " + label : ""))
      : "未登录";
    userChip.classList.toggle("on", state.authOk);
    userChip.classList.toggle("warn", state.authOk && state.pwdDefault);
    userChip.title = !state.authOk ? "未登录"
      : (state.pwdDefault ? "正在使用初始密码，请点击「修改密码」" : "当前登录账号");
    logoutBtn.style.display = state.authOk ? "inline-block" : "none";
    uploadBtn.style.display = (!state.authOk || state.role === "viewer") ? "none" : "";
    exportNav.style.display = (state.authOk && state.role === "admin") ? "" : "none";
    usersNav.style.display = (state.authOk && state.role === "admin") ? "" : "none";
    auditNav.style.display = (state.authOk && state.role === "admin") ? "" : "none";
    settingsNav.style.display = state.authOk ? "" : "none";
  };

  syncAuth();
  authStore.subscribe(syncAuth);
}
