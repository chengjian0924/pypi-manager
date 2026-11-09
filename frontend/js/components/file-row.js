import { esc } from "../utils/dom.js";
import { fmtBytes } from "../utils/format.js";

const REMOVE_ICON = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>`;

/** @param {import('../types/models.js').UploadItem} item */
export function uploadStatusText(item) {
  if (item.status === "ok") return item.message || "已上传";
  if (item.status === "canceled") return "已取消";
  if (item.status === "err") return item.clientInvalid ? "校验未通过" : "上传失败";
  if (item.status === "running") return (item.pct >= 99.5 ? 99 : Math.round(item.pct)) + "%";
  return "待上传";
}

/**
 * @param {import('../types/models.js').UploadItem} item
 * @param {boolean} uploading
 */
export function renderFileRow(item, uploading) {
  const cls = item.status === "ok" ? "ok"
    : item.status === "err" ? "err"
      : item.status === "running" ? "run"
        : item.status === "canceled" ? "canceled" : "";

  let action = "";
  if (uploading && item.status === "running") {
    action = `<button class="file-cancel" type="button" data-action="cancel" data-id="${item.id}">取消</button>`;
  } else if (!uploading) {
    action = `<button class="file-x" type="button" data-action="remove" data-id="${item.id}" aria-label="移除文件">
      ${REMOVE_ICON}
    </button>`;
  }

  return `<div class="file-row" data-id="${item.id}">
    <div class="file-meta">
      <span class="file-name" title="${esc(item.file.name)}">${esc(item.file.name)}</span>
      <span class="file-sub"><span>${fmtBytes(item.file.size)}</span></span>
      ${item.err ? `<span class="file-err">${esc(item.err)}</span>` : ""}
    </div>
    <span class="file-status ${cls}">${esc(uploadStatusText(item))}</span>
    ${action}
  </div>`;
}
