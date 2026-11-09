import { request } from "./client.js";

/**
 * @typedef {{name: string, role: string, created: string, pwd_default: number}} UserItem
 */

/**
 * 账号列表（仅 admin）。
 * @returns {Promise<{items: UserItem[], roles: string[], me: string, defaultPassword: string}>}
 */
export async function listUsers() {
  const data = await request("/api/users", { timeout: 15000 });
  return {
    items: (data && data.items) || [],
    roles: (data && data.roles) || ["admin", "uploader", "viewer"],
    me: (data && data.me) || "",
    defaultPassword: (data && data.default_password) || "12345678"
  };
}

/**
 * 建号 / 改角色 / 重置密码（同一接口：带 password 即设置密码，reset_password 即重置为初始密码）。
 * @param {{name: string, password?: string, reset_password?: boolean, role?: string}} payload
 * @returns {Promise<{action: string, user: UserItem}>}
 */
export function saveUser(payload) {
  return request("/api/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeout: 20000
  });
}

/**
 * 批量建号：已存在的账号会被跳过，初始密码统一。
 * @param {{text?: string, names?: string[], role?: string, password?: string}} payload
 * @returns {Promise<{created: string[], skipped: string[], invalid: {name: string, reason: string}[],
 *                    role: string, password: string}>}
 */
export function batchCreateUsers(payload) {
  return request("/api/users/batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeout: 60000
  });
}

/** @param {string} name */
export function deleteUser(name) {
  return request("/api/users/" + encodeURIComponent(name), {
    method: "DELETE",
    timeout: 15000
  });
}

/**
 * 用户自助修改自己的密码（需要原密码）。
 * @param {string} oldPassword
 * @param {string} newPassword
 */
export function changePassword(oldPassword, newPassword) {
  return request("/api/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    timeout: 20000,
    skipUnauthorized: true
  });
}
