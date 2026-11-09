import { ApiError } from "./client.js";
import { getCredentials } from "../utils/storage.js";
import { fmtBytes } from "../utils/format.js";

/**
 * 通过 XHR 上传单个文件，提供进度回调与中断能力。
 * @param {File} file
 * @param {{onProgress?: (pct: number) => void}} [options]
 * @returns {{promise: Promise<object>, abort: () => void}}
 */
export function uploadFile(file, options = {}) {
  const { onProgress } = options;
  const xhr = new XMLHttpRequest();
  let canceled = false;

  const promise = new Promise((resolve, reject) => {
    xhr.open("PUT", "/api/upload?filename=" + encodeURIComponent(file.name), true);
    const credentials = getCredentials();
    if (credentials) xhr.setRequestHeader("Authorization", "Basic " + credentials);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");

    xhr.upload.onprogress = event => {
      if (!event.lengthComputable || canceled) return;
      if (onProgress) onProgress(event.loaded / event.total * 100);
    };

    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText || "{}") || {}; } catch (e) { body = {}; }
      if (xhr.status >= 200 && xhr.status < 300 && body.ok !== false) {
        resolve(body);
        return;
      }
      reject(new ApiError((body && (body.error || body.message)) || ("HTTP " + xhr.status), {
        status: xhr.status,
        body
      }));
    };

    xhr.onerror = () => {
      reject(new ApiError("网络连接中断，文件未完成上传", { status: 0, network: true }));
    };

    xhr.onabort = () => {
      reject(new ApiError("已取消", { status: 0, canceled: true }));
    };

    xhr.send(file);
  });

  return {
    promise,
    abort() {
      canceled = true;
      try { xhr.abort(); } catch (e) { /* ignore */ }
    }
  };
}

/**
 * @param {number} status
 * @param {{error?: string, message?: string}|null} body
 * @param {number} maxUpload
 * @returns {{title: string, detail: string, hint: string}}
 */
export function explainUploadFailure(status, body, maxUpload) {
  const msg = (body && (body.error || body.message)) || "";
  if (status === 401) return {
    title: "登录状态已失效（401）",
    detail: "服务器要求重新登录后才能上传。",
    hint: "请返回主界面重新登录，然后回到上传页点击「重新上传」，已选文件不会丢失。"
  };
  if (status === 403) return {
    title: "当前账号没有上传权限（403）",
    detail: msg || "服务器拒绝了这个账号的上传请求。",
    hint: "请联系管理员为该账号授予上传权限后再试。"
  };
  if (status === 413) return {
    title: "文件大小超出限制（413）",
    detail: msg || ("单个文件最大 " + fmtBytes(maxUpload) + "。"),
    hint: "请拆分或压缩包体后重新上传；已选文件会保留在列表里。"
  };
  if (status === 507) return {
    title: "服务器存储空间不足（507）",
    detail: msg || "服务器磁盘空间已写满，文件无法写入。",
    hint: "请联系管理员清理磁盘空间，然后点击「重新上传」。"
  };
  if (status === 400 || status === 415) {
    let title = "文件校验未通过（400）";
    if (/上限|大小/.test(msg)) title = "文件大小超出限制（400）";
    else if (/为空|0 字节/.test(msg)) title = "文件内容为空（400）";
    else if (/只接受|格式/.test(msg)) title = "文件格式不受支持（400）";
    else if (/wheel|zip/.test(msg)) title = "wheel 文件结构损坏（400）";
    else if (/网络|不完整/.test(msg)) title = "上传数据不完整（400）";
    return {
      title,
      detail: msg || "服务器拒绝了该文件，但未返回具体原因。",
      hint: "请按提示修正文件后点击「重新上传」；已选文件会保留。"
    };
  }
  if (status === 404) return {
    title: "上传接口不存在（404）",
    detail: "服务器上没有 /api/upload 接口。",
    hint: "请确认连接的是正确的包管理服务地址，或联系管理员检查部署版本。"
  };
  if (status >= 500) return {
    title: "服务端处理异常（HTTP " + status + "）",
    detail: msg || "服务器在处理上传时发生内部错误，未返回具体原因。",
    hint: "请稍后点击「重新上传」重试；若持续失败，请联系管理员查看服务端日志。"
  };
  return {
    title: "上传被服务器拒绝（HTTP " + status + "）",
    detail: msg || "服务器返回了未预期的状态码。",
    hint: "请重试；若问题持续，请联系管理员并提供该状态码。"
  };
}
