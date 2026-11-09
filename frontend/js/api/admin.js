import { request, requestBlob } from "./client.js";

export const DEFAULT_MAX_UPLOAD = 512 * 1024 * 1024;

/**
 * 获取服务端配置；接口不可用时回退到默认上限，保证页面可用。
 * @returns {Promise<{max_upload: number, version: string, open_read: boolean}>}
 */
export async function fetchConfig() {
  try {
    const data = await request("/api/config", { withAuth: false, timeout: 8000 });
    return {
      max_upload: Number(data && data.max_upload) || DEFAULT_MAX_UPLOAD,
      version: (data && data.version) || "",
      open_read: !!(data && data.open_read)
    };
  } catch (error) {
    return { max_upload: DEFAULT_MAX_UPLOAD, version: "", open_read: false };
  }
}

/** 仅管理员可用：导出 CSV 清单。 */
export function exportCsv() {
  return requestBlob("/api/export.csv", { timeout: 60000 });
}
