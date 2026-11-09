import { request } from "./client.js";

/**
 * 获取包列表，由前端做名称 / 类型 / 日期 / 版本等多维筛选。
 *
 * 默认只取在用包（deleted=0）；只有用户打开「含已删除」或点回收站「查看」
 * 时才请求 deleted=all，连带把回收站历史行取回来。历史行只增不减，
 * 随覆盖上传和删除不断累积，按需加载可避免每次刷新都传输这部分数据。
 * @param {boolean} includeDeleted
 */
export function fetchPackages(includeDeleted = false) {
  const mode = includeDeleted ? "all" : "0";
  return request("/api/packages?deleted=" + mode, { timeout: 30000 });
}

/** @param {number} id @param {string} note */
export function updateNote(id, note) {
  return request("/api/files/" + encodeURIComponent(String(id)), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ note })
  });
}

/** @param {number} id */
export function deleteFile(id) {
  return request("/api/files/" + encodeURIComponent(String(id)), { method: "DELETE" });
}

/** @param {number} id */
export function restoreFile(id) {
  return request("/api/files/" + encodeURIComponent(String(id)), { method: "POST" });
}

/** 管理员：彻底删除回收站里的单条记录（不可恢复） @param {number} id */
export function purgeFile(id) {
  return request("/api/files/" + encodeURIComponent(String(id)) + "/purge", { method: "DELETE" });
}

/** 管理员：清空回收站 */
export function emptyTrash() {
  return request("/api/trash/empty", { method: "POST" });
}
