import { getCredentials } from "../utils/storage.js";

export class ApiError extends Error {
  /**
   * @param {string} message
   * @param {{status?: number, body?: *, timeout?: boolean, network?: boolean, canceled?: boolean}} [info]
   */
  constructor(message, info = {}) {
    super(message);
    this.name = "ApiError";
    this.status = info.status || 0;
    this.body = info.body === undefined ? null : info.body;
    this.timeout = !!info.timeout;
    this.network = !!info.network;
    this.canceled = !!info.canceled;
  }
}

/** @type {null | (() => void)} */
let unauthorizedHandler = null;

/** @param {null | (() => void)} handler */
export function setUnauthorizedHandler(handler) {
  unauthorizedHandler = handler;
}

/** @param {Record<string, string>} headers @param {boolean} withAuth @param {string|null|undefined} credentials */
function buildHeaders(headers, withAuth, credentials) {
  const out = Object.assign({}, headers || {});
  const credValue = credentials != null ? credentials : (withAuth ? getCredentials() : "");
  if (credValue) out.Authorization = "Basic " + credValue;
  return out;
}

/** @param {Response} response */
async function readBody(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("json")) {
    try { return await response.json(); } catch (e) { return null; }
  }
  return await response.text();
}

/**
 * @param {string} path
 * @param {{method?: string, headers?: Record<string,string>, body?: BodyInit|null,
 *          timeout?: number, withAuth?: boolean, credentials?: string|null,
 *          skipUnauthorized?: boolean}} [options]
 */
export async function request(path, options = {}) {
  const {
    method = "GET",
    headers = {},
    body = null,
    timeout = 30000,
    withAuth = true,
    credentials = null,
    skipUnauthorized = false
  } = options;

  const controller = new AbortController();
  const timer = timeout > 0 ? setTimeout(() => controller.abort(), timeout) : null;

  try {
    const response = await fetch(path, {
      method,
      headers: buildHeaders(headers, withAuth, credentials),
      body,
      signal: controller.signal,
      credentials: "same-origin"
    });
    const payload = await readBody(response);
    if (response.status === 401 && !skipUnauthorized && unauthorizedHandler) {
      unauthorizedHandler();
    }
    if (!response.ok) {
      const message = (payload && (payload.error || payload.message)) || ("HTTP " + response.status);
      throw new ApiError(message, { status: response.status, body: payload });
    }
    return payload;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error && error.name === "AbortError") {
      throw new ApiError("请求超时，请稍后重试", { status: 0, timeout: true });
    }
    throw new ApiError("无法连接服务器，请检查网络或确认服务已启动", { status: 0, network: true });
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * @param {string} path
 * @param {{timeout?: number, headers?: Record<string,string>}} [options]
 */
export async function requestBlob(path, options = {}) {
  const { timeout = 60000, headers = {} } = options;
  const controller = new AbortController();
  const timer = timeout > 0 ? setTimeout(() => controller.abort(), timeout) : null;

  try {
    const response = await fetch(path, {
      method: "GET",
      headers: buildHeaders(headers, true, null),
      signal: controller.signal,
      credentials: "same-origin"
    });
    if (response.status === 401) {
      if (unauthorizedHandler) unauthorizedHandler();
      throw new ApiError("需要登录", { status: 401 });
    }
    if (!response.ok) {
      const payload = await readBody(response);
      const message = (payload && (payload.error || payload.message)) || ("HTTP " + response.status);
      throw new ApiError(message, { status: response.status, body: payload });
    }
    return await response.blob();
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error && error.name === "AbortError") {
      throw new ApiError("请求超时，请稍后重试", { status: 0, timeout: true });
    }
    throw new ApiError("无法连接服务器，请检查网络或确认服务已启动", { status: 0, network: true });
  } finally {
    if (timer) clearTimeout(timer);
  }
}
