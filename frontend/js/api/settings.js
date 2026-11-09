import { request } from "./client.js";

/**
 * 「客户端接入」文案：任意登录账号可读，仅管理员可写。
 * @returns {Promise<{sections: Record<string,string>, defaults: Record<string,string>,
 *   labels: Record<string,string>, order: string[], custom: boolean,
 *   custom_sections: string[], updated_at: string, updated_by: string,
 *   max_section_chars: number}>}
 */
export function fetchClientConfig() {
  return request("/api/client-config", { timeout: 8000 });
}

/**
 * 只提交需要改的段落；传空字符串等于该段恢复默认。
 * @param {Record<string,string>} sections
 */
export function saveClientConfig(sections) {
  return request("/api/client-config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sections })
  });
}

/** 全部恢复内置默认（删除自定义内容） */
export function resetClientConfig() {
  return request("/api/client-config", { method: "DELETE" });
}
