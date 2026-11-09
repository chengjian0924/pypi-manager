import { request } from "./client.js";

/** 服务端单次最多返回的条数（与 admin.py 的 limit 上限一致） */
export const AUDIT_MAX_LIMIT = 2000;

/**
 * 读取审计日志（仅管理员）。服务端会连同轮转文件一起从尾部读取，结果按时间倒序。
 * @param {{limit?: number}} [options]
 * @returns {Promise<{items: import('../types/models.js').AuditEntry[]}>}
 */
export async function fetchAudit(options = {}) {
  const raw = Number(options.limit);
  const limit = Math.max(1, Math.min(Number.isFinite(raw) ? Math.floor(raw) : 1000, AUDIT_MAX_LIMIT));
  const data = await request("/api/audit?limit=" + limit, { timeout: 20000 });
  return { items: data && Array.isArray(data.items) ? data.items : [] };
}
