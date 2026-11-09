import { esc } from "../utils/dom.js";
import { fileKind, newestVersion, formatUploadedAt } from "../utils/format.js";

const CARET_ICON = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>`;
const CLOSE_ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>`;

/**
 * 表格列定义：宽度写在 th 上（table-layout:fixed），因此同一个 <col> 结构
 * 在所有项目卡片里宽度完全一致，表头与内容严格对齐。
 * compact 模式会整列移除，而不是用 CSS 隐藏——隐藏列在 fixed 布局下仍会占位。
 */
const COLUMNS = [
  { key: "fn", label: "文件名", cls: "c-fn" },
  { key: "ver", label: "版本", cls: "c-ver" },
  { key: "kind", label: "类型", cls: "c-kind" },
  { key: "size", label: "大小", cls: "c-size" },
  { key: "time", label: "上传时间", cls: "c-time" },
  { key: "user", label: "上传人", cls: "c-user" },
  { key: "status", label: "状态", cls: "c-status" },
  { key: "act", label: "操作", cls: "c-act" }
];

const COMPACT_HIDDEN = ["kind", "user"];

/**
 * @param {import('../types/models.js').FileEntry} file
 * @param {boolean} canManage
 */
function renderFileRow(file, canManage, isAdmin, columns) {
  const action = file.deleted
    ? (canManage
      ? `<button class="btn btn-sm" type="button" data-action="restore" data-id="${file.id}">恢复</button>
         ${isAdmin ? `<button class="btn btn-sm btn-danger" type="button" data-action="purge" data-id="${file.id}">彻底删除</button>` : ""}`
      : "")
    : `<a class="btn btn-sm" href="/files/${encodeURIComponent(file.filename)}">下载</a>
       ${canManage ? `<button class="btn btn-sm btn-danger" type="button" data-action="delete" data-id="${file.id}">删除</button>` : ""}`;

  const cells = {
    fn: `<td class="fn"><span class="fn-name">${esc(file.filename)}</span></td>`,
    ver: `<td data-label="版本"><span class="mono">${esc(file.version)}</span></td>`,
    kind: `<td data-label="类型"><span class="tag kind">${fileKind(file) === "wheel" ? "Wheel" : "SDist"}</span></td>`,
    size: `<td data-label="大小" class="num">${esc(file.size_h)}</td>`,
    time: `<td data-label="上传时间">${esc(formatUploadedAt(file.uploaded_at))}</td>`,
    user: `<td data-label="上传人">${esc(file.uploaded_by || "—")}</td>`,
    status: `<td data-label="状态">${file.deleted
      ? (file.deleted_reason === "replace" ? '<span class="tag del">已覆盖</span>' : '<span class="tag del">已删除</span>')
      : '<span class="tag ok">正常</span>'}</td>`,
    act: `<td class="act">
        <span class="act-group">
          <button class="btn btn-sm" type="button" data-action="detail" data-id="${file.id}">详情</button>
          ${action}
        </span>
      </td>`
  };

  return `<tr>${columns.map(col => cells[col.key]).join("")}</tr>`;
}

/**
 * @param {import('../types/models.js').Package} project
 * @param {{isNew?: boolean, canManage: (file: import('../types/models.js').FileEntry) => boolean,
 *          isAdmin?: boolean, compact?: boolean}} options
 */
export function renderProject(project, options) {
  const { isNew = false, canManage, isAdmin = false, compact = false } = options;
  const columns = compact ? COLUMNS.filter(col => !COMPACT_HIDDEN.includes(col.key)) : COLUMNS;
  const rows = project.files.map(file => renderFileRow(file, canManage(file), isAdmin, columns)).join("");
  const head = columns
    .map(col => `<th class="${col.cls}">${col.label}</th>`)
    .join("");

  return `
    <section class="proj open${isNew ? " new" : ""}">
      <div class="phead" role="button" tabindex="0">
        <span class="caret">${CARET_ICON}</span>
        <span class="pname">${esc(project.name)}</span>${isNew ? '<span class="tag new">新上传</span>' : ""}
        <span class="pmeta">${project.files.length} 个分发包 · 最新 ${esc(newestVersion(project.files))}</span>
        <span class="pdesc">${esc(project.description || "")}</span>
      </div>
      <div class="pbody">
        <div class="table-wrap">
          <table>
            <thead><tr>${head}</tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>
    </section>`;
}

/**
 * @param {import('../types/models.js').Package} project
 * @param {import('../types/models.js').FileEntry} file
 * @param {boolean} editable
 */
export function renderDetailSheet(project, file, editable) {
  return `
    <div class="sheet-head">
      <div>
        <h3 class="truncate" title="${esc(file.filename)}">${esc(file.filename)}</h3>
        <p class="s">${esc(project.name)} · 版本 ${esc(file.version)}</p>
      </div>
      <button class="icon-btn" type="button" data-action="close-modal" aria-label="关闭">
        ${CLOSE_ICON}
      </button>
    </div>
    <div class="sheet-body">
      <dl class="kv">
        <dt>sha256</dt><dd class="mono" style="font-size:11.5px">${esc(file.sha256)}</dd>
        <dt>大小</dt><dd>${esc(file.size_h)}（${file.size} 字节）</dd>
        <dt>标签</dt><dd>${esc([file.py_tag, file.abi_tag, file.plat_tag].filter(Boolean).join(" · ") || "—")}</dd>
        <dt>Python</dt><dd>${esc(file.requires_python || "未声明")}</dd>
        <dt>依赖</dt><dd>${file.dep_count} 个 Requires-Dist</dd>
        <dt>摘要</dt><dd>${esc(file.summary || "—")}</dd>
        <dt>导入人</dt><dd>${esc(file.uploaded_by)}</dd>
        <dt>导入时间</dt><dd>${esc(file.uploaded_at)}</dd>
      </dl>
      <div class="field">
        <label for="noteInput">备注</label>
        <input type="text" id="noteInput" placeholder="填写备注…" value="${esc(file.note)}" ${editable ? "" : "disabled"}>
      </div>
    </div>
    <div class="sheet-foot">
      <button class="btn" type="button" data-action="close-modal">关闭</button>
      ${editable ? `<button class="btn btn-primary" type="button" data-action="save-note" data-id="${file.id}">保存备注</button>` : ""}
    </div>`;
}
