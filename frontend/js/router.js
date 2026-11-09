import * as loginPage from "./pages/login.js";
import * as listPage from "./pages/package-list.js";
import * as uploadPage from "./pages/upload.js";
import * as usersPage from "./pages/users.js";
import * as userCreatePage from "./pages/user-create.js";
import * as auditPage from "./pages/audit.js";
import { authStore, ensureAuth } from "./store/auth.js";

let root = null;
let currentPage = null;
let started = false;
let pendingMessage = "";
let routeSeq = 0;

function normalizePath(raw) {
  let path = raw || "/";
  if (!path.startsWith("/")) path = "/" + path;
  if (path === "/index.html") path = "/";
  if (path.length > 1 && path.endsWith("/")) path = path.slice(0, -1);
  return path;
}

export function getPath() {
  const raw = (location.hash || "").replace(/^#/, "");
  return normalizePath(raw || "/");
}

function swapPage(page, arg) {
  const replace = () => {
    if (currentPage && typeof currentPage.unmount === "function") {
      currentPage.unmount();
    }
    currentPage = page;
    root.innerHTML = "";
    try {
      const result = page.mount(root, arg);
      if (result && typeof result.catch === "function") {
        result.catch(error => console.error("[route] 页面加载失败", error));
      }
    } catch (error) {
      console.error("[route] 页面渲染异常", error);
      root.innerHTML = '<div class="empty">页面加载失败，请刷新重试</div>';
    }
  };

  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (document.startViewTransition && !reduce) {
    document.startViewTransition(replace);
  } else {
    replace();
  }
}

/** @param {string} path @param {{replace?: boolean}} [options] */
export function navigate(path, options = {}) {
  const target = "#" + normalizePath(path);
  if (location.hash === target) {
    handleRoute();
    return;
  }
  if (options.replace) {
    try { history.replaceState(null, "", target); } catch (e) { location.hash = target; return; }
    handleRoute();
    return;
  }
  location.hash = target;
}

/**
 * 跳转到登录页并显示提示。
 * @param {string} [message]
 */
export function showLogin(message = "") {
  pendingMessage = message;
  navigate("/login", { replace: true });
}

async function handleRoute() {
  if (!root) return;
  const seq = ++routeSeq;
  const path = getPath();

  if (path === "/login") {
    if (authStore.getState().authOk) {
      navigate("/", { replace: true });
      return;
    }
    if (authStore.getState().credentials) {
      const restored = await ensureAuth();
      if (seq !== routeSeq) return;
      if (restored) {
        navigate("/", { replace: true });
        return;
      }
      pendingMessage = "登录状态已失效，请重新登录";
    }
    const message = pendingMessage;
    pendingMessage = "";
    swapPage(loginPage, message);
    return;
  }

  const isUpload = path === "/upload";
  const isUsers = path === "/users";
  const isUserCreate = path === "/users/new";
  const isAudit = path === "/audit";
  if (path !== "/" && !isUpload && !isUsers && !isUserCreate && !isAudit) {
    navigate("/", { replace: true });
    return;
  }

  const hadCredentials = !!authStore.getState().credentials;
  const ok = await ensureAuth();
  if (seq !== routeSeq) return;

  if (!ok) {
    pendingMessage = hadCredentials ? "登录状态已失效，请重新登录" : "请先登录后再访问";
    navigate("/login", { replace: true });
    return;
  }

  // 用户管理 / 审计日志仅管理员可见：在这里拦掉，避免先渲染页面再跳转
  if ((isUsers || isUserCreate || isAudit) && authStore.getState().role !== "admin") {
    pendingMessage = "";
    navigate("/", { replace: true });
    return;
  }

  if (isUserCreate) swapPage(userCreatePage);
  else if (isUsers) swapPage(usersPage);
  else if (isAudit) swapPage(auditPage);
  else if (isUpload) swapPage(uploadPage);
  else swapPage(listPage);
}

export function start() {
  if (started) return;
  started = true;
  root = document.getElementById("view-root");

  window.addEventListener("hashchange", handleRoute);
  window.addEventListener("pagehide", () => {
    if (currentPage === listPage && typeof listPage.saveState === "function") {
      listPage.saveState();
    }
  });

  if (!location.hash) {
    try { history.replaceState(null, "", "#/"); } catch (e) { location.hash = "#/"; }
  }
  handleRoute();
}
