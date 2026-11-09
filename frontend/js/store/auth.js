import { createStore } from "./index.js";
import * as authApi from "../api/auth.js";
import { getCredentials, removeCredentials, setCredentials } from "../utils/storage.js";

const initialState = {
  credentials: getCredentials(),
  user: "",
  role: "",
  pwdDefault: false,
  authOk: false
};

export const authStore = createStore(initialState);

/** @type {null | Promise<boolean>} */
let pendingEnsure = null;

export function getAuth() {
  return authStore.getState();
}

export function isAdmin() {
  return getAuth().role === "admin";
}

/** @param {{uploaded_by?: string}} file */
export function canManage(file) {
  const state = getAuth();
  if (state.role === "admin") return true;
  return state.role === "uploader" && file.uploaded_by === state.user;
}

/** 只读角色不显示上传入口。 */
export function canUpload() {
  return getAuth().role !== "viewer";
}

/** @param {{user?: string, role?: string, credentials?: string, pwd_default?: boolean}} info */
export function setLoggedIn(info) {
  authStore.setState({
    credentials: info.credentials || getCredentials(),
    user: info.user || "",
    role: info.role || "",
    pwdDefault: !!info.pwd_default,
    authOk: true
  });
}

/**
 * 自助改密成功后同步本地凭据：不更新的话下一个请求仍会用旧密码，直接 401。
 * @param {string} credentials 新的 Basic 凭据
 */
export function applyNewCredentials(credentials) {
  setCredentials(credentials);
  authStore.setState({ credentials, pwdDefault: false });
}

export function clearAuth() {
  removeCredentials();
  authStore.setState({ credentials: "", user: "", role: "", pwdDefault: false, authOk: false });
}

/** @param {string} username @param {string} password */
export async function login(username, password) {
  const info = await authApi.login(username, password);
  setLoggedIn(Object.assign({}, info, { user: info.user || username }));
  return info;
}

/** 校验 sessionStorage 中的凭据并恢复角色信息。 */
export async function ensureAuth() {
  const state = getAuth();
  if (state.authOk) return true;
  if (!state.credentials) return false;
  if (!pendingEnsure) {
    pendingEnsure = authApi.validateSession(state.credentials)
      .then(info => {
        if (info) {
          setLoggedIn(info);
          return true;
        }
        clearAuth();
        return false;
      })
      .catch(() => false)
      .finally(() => { pendingEnsure = null; });
  }
  return pendingEnsure;
}
