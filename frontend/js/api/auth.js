import { request } from "./client.js";
import { getCredentials, setCredentials } from "../utils/storage.js";

/** @param {string} username @param {string} password */
export function encodeCredentials(username, password) {
  return btoa(unescape(encodeURIComponent(username + ":" + password)));
}

/**
 * @param {string} credentials
 * @returns {Promise<{user: string, role: string, pwd_default: boolean}>}
 */
export async function whoami(credentials) {
  const data = await request("/api/whoami", {
    credentials,
    skipUnauthorized: true,
    timeout: 15000
  });
  return {
    user: (data && data.user) || "",
    role: (data && data.role) || "",
    // 仍在使用初始密码时顶栏会高亮提醒，这里必须透传，否则登录/刷新后提示就丢了
    pwd_default: !!(data && data.pwd_default)
  };
}

/**
 * @param {string} username
 * @param {string} password
 * @returns {Promise<{user: string, role: string, pwd_default: boolean, credentials: string}>}
 */
export async function login(username, password) {
  const credentials = encodeCredentials(username, password);
  const info = await whoami(credentials);
  setCredentials(credentials);
  return Object.assign({}, info, { credentials });
}

/**
 * @param {string} [credentials]
 * @returns {Promise<{user: string, role: string, pwd_default: boolean, credentials: string}|null>}
 */
export async function validateSession(credentials = getCredentials()) {
  if (!credentials) return null;
  try {
    const info = await whoami(credentials);
    return Object.assign({}, info, { credentials });
  } catch (error) {
    return null;
  }
}
