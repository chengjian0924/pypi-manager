import { navigate } from "../router.js";
import { isAdmin } from "../store/auth.js";
import { setView } from "../store/ui.js";
import { fetchAudit } from "../api/audit.js";
import { toast } from "../components/toast.js";
import { esc, delegate, scrollToTop } from "../utils/dom.js";
import { fmtBytes } from "../utils/format.js";

/** 动作 → [中文名, 色调]。色调对应 .tag.ok / .tag.warn / .tag.del */
const ACTIONS = {
  login: ["登录", ""],
  login_failed: ["登录失败", "warn"],
  forbidden: ["越权拦截", "del"],
  upload: ["上传", "ok"],
  replace: ["覆盖版本", "warn"],
  delete: ["移入回收站", "warn"],
  restore: ["恢复", "ok"],
  purge: ["彻底删除", "del"],
  trash_empty: ["清空回收站", "del"],
  edit_note: ["改文件备注", ""],
  edit_project: ["改项目备注", ""],
  edit_client_config: ["改客户端配置", ""],
  user_create: ["创建账号", "ok"],
  user_update: ["修改账号", ""],
  user_password: ["重置密码", "warn"],
  user_password_self: ["自助改密", ""],
  user_batch_create: ["批量建号", "ok"],
  user_delete: ["删除账号", "del"],
  backup: ["备份", ""],
  reconcile: ["启动对账", ""],
  server_error: ["服务异常", "del"]
};

/** 「仅风险操作」筛选命中的动作：安全事件 + 不可恢复操作 */
const RISKY = new Set(["login_failed", "forbidden", "purge", "trash_empty", "user_delete", "server_error"]);

/** 记录里固定存在的字段，其余字段都算「详情」 */
const BASE_KEYS = ["ts", "user", "action", "target", "ip"];

const FIELD_LABELS = {
  ts: "时间", user: "操作人", action: "动作", target: "对象", ip: "来源 IP",
  role: "角色", scope: "可见范围", project: "项目", version: "版本",
  sha256: "SHA-256", size: "大小", copies: "清除副本", files: "条数",
  note: "备注", description: "项目描述", error_id: "错误编号", method: "请求方法",
  sections: "改动段落", custom: "保留自定义", reset: "恢复默认",
  exception: "异常类型", db_check: "数据库检查", tmp_removed: "清理临时文件",
  missing_marked: "标记丢失", orphans_moved: "隔离孤儿文件", trash_removed: "清理回收站"
};

const PAGE_SIZES = [20, 50, 100];
const LOAD_LIMIT = 1000;
const REFRESH_MS = 15000;
const SEARCH_ICON = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>`;
const CLOSE_ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>`;

/** @type {import('../types/models.js').AuditEntry[]} */
let rows = [];
/** 当前筛选结果（导出与详情回查都用它，避免与渲染结果不一致） */
let viewRows = [];
let keyword = "";
let actionFilter = "";
let riskyOnly = false;
let page = 1;
let pageSize = PAGE_SIZES[0];
let autoRefresh = false;
let autoTimer = null;
let searchTimer = null;
let loadSeq = 0;
let modalOpen = false;
let cleanups = [];

/** @type {Record<string, HTMLElement>} */
const el = {};

function actionLabel(action) {
  const hit = ACTIONS[action];
  return hit ? hit[0] : (action || "未知");
}

function actionTone(action) {
  const hit = ACTIONS[action];
  return hit ? hit[1] : "";
}

function extraPairs(rec) {
  return Object.keys(rec)
    .filter(key => BASE_KEYS.indexOf(key) < 0)
    .map(key => [key, rec[key]]);
}

function detailText(rec) {
  const pairs = extraPairs(rec);
  if (!pairs.length) return "";
  try { return JSON.stringify(Object.fromEntries(pairs)); } catch (e) { return ""; }
}

/** ISO 时间戳（2026-11-09T11:14:24+08:00）→ 2026-11-09 11:14:24 */
function fmtTs(value) {
  const s = String(value || "");
  if (s.length < 16) return s || "—";
  return s.slice(0, 10) + " " + s.slice(11, 19);
}

function fieldValue(key, value) {
  if (value == null || value === "") return "—";
  if (key === "size" && typeof value === "number") return fmtBytes(value);
  if (typeof value === "object") {
    try { return JSON.stringify(value); } catch (e) { return String(value); }
  }
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
}

function filteredRows() {
  const kw = keyword.trim().toLowerCase();
  return rows.filter(rec => {
    if (actionFilter && rec.action !== actionFilter) return false;
    if (riskyOnly && !RISKY.has(rec.action)) return false;
    if (!kw) return true;
    const hay = [
      rec.user, rec.target, rec.ip, actionLabel(rec.action), rec.action, detailText(rec)
    ].join(" ").toLowerCase();
    return hay.indexOf(kw) >= 0;
  });
}

function hasFilters() {
  return !!(keyword || actionFilter || riskyOnly);
}

/** 动作下拉：只列出当前数据里出现过的动作，按固定顺序 + 未知动作字母序 */
function syncActionOptions() {
  if (!el.action) return;
  const counts = new Map();
  rows.forEach(rec => counts.set(rec.action, (counts.get(rec.action) || 0) + 1));
  const known = Object.keys(ACTIONS).filter(key => counts.has(key));
  const unknown = Array.from(counts.keys())
    .filter(key => !ACTIONS[key])
    .sort((a, b) => String(a).localeCompare(String(b)));
  const options = [`<option value="">全部动作</option>`].concat(
    known.concat(unknown).map(key =>
      `<option value="${esc(key)}">${esc(actionLabel(key))}（${counts.get(key)}）</option>`)
  ).join("");
  if (el.action.innerHTML !== options) {
    el.action.innerHTML = options;
    el.action.value = counts.has(actionFilter) ? actionFilter : "";
    if (!counts.has(actionFilter)) actionFilter = "";
  }
}

function rowHtml(rec, idx) {
  const tone = actionTone(rec.action);
  const target = rec.target ? esc(rec.target) : "—";
  const ip = rec.ip ? esc(rec.ip) : "—";
  return `<tr>
    <td class="au-ts" data-label="时间">${esc(fmtTs(rec.ts))}</td>
    <td data-label="操作人"><span class="um-name">${esc(rec.user || "—")}</span></td>
    <td data-label="动作"><span class="tag kind${tone ? " " + tone : ""}">${esc(actionLabel(rec.action))}</span></td>
    <td class="au-target" data-label="对象">${target}</td>
    <td class="au-ip" data-label="来源 IP">${ip}</td>
    <td class="au-detail-cell" data-label="详情">
      <button class="btn btn-sm" type="button" data-action="detail" data-idx="${idx}">查看</button>
    </td>
  </tr>`;
}

function renderPager() {
  if (!el.pager) return;
  const totalPages = Math.max(1, Math.ceil(viewRows.length / pageSize));
  if (page > totalPages) page = totalPages;
  el.pager.hidden = viewRows.length === 0;
  if (!viewRows.length) return;
  el.pageInfo.textContent = "第 " + page + " / " + totalPages + " 页";
  el.prev.disabled = page <= 1;
  el.next.disabled = page >= totalPages;
  if (el.filterCount) {
    el.filterCount.innerHTML = `<b>${viewRows.length}</b> 条记录` +
      (hasFilters() ? ` · 共 <b>${rows.length}</b> 条 · 已应用筛选` : "");
  }
}

function render() {
  viewRows = filteredRows();
  const totalPages = Math.max(1, Math.ceil(viewRows.length / pageSize));
  if (page > totalPages) page = totalPages;
  if (page < 1) page = 1;
  const start = (page - 1) * pageSize;
  const slice = viewRows.slice(start, start + pageSize);

  if (el.count) {
    const loaded = rows.length >= LOAD_LIMIT
      ? `最近 ${rows.length} 条（更早记录未加载）`
      : `共 ${rows.length} 条`;
    el.count.textContent = hasFilters()
      ? `筛选出 ${viewRows.length} / ${rows.length} 条`
      : loaded;
  }

  if (!el.body) return;
  if (!slice.length) {
    el.body.innerHTML = `<tr><td colspan="6" class="um-empty">${
      rows.length ? "没有符合筛选条件的记录" : "暂无审计记录"}</td></tr>`;
  } else {
    el.body.innerHTML = slice.map((rec, i) => rowHtml(rec, start + i)).join("");
  }
  renderPager();
}

function stopAuto() {
  if (autoTimer) clearInterval(autoTimer);
  autoTimer = null;
}

function startAuto() {
  stopAuto();
  autoTimer = setInterval(() => {
    if (document.hidden) return;          // 页面在后台不打扰服务端
    if (document.getElementById("modal")?.classList.contains("on")) return;  // 正在看详情时暂停
    load({ quiet: true });
  }, REFRESH_MS);
}

async function load(options = {}) {
  const seq = ++loadSeq;
  if (!options.quiet && el.body) {
    el.body.innerHTML = '<tr><td colspan="6" class="um-empty">加载中…</td></tr>';
  }
  try {
    const data = await fetchAudit({ limit: LOAD_LIMIT });
    if (seq !== loadSeq) return;          // 旧响应不覆盖新响应
    rows = data.items;
    syncActionOptions();
    render();
  } catch (error) {
    if (seq !== loadSeq) return;
    if (error.status === 401) return;     // 统一由 client.js 的 401 回调跳登录
    if (error.status === 403) {
      toast("仅管理员可以查看审计日志", true);
      navigate("/", { replace: true });
      return;
    }
    if (options.quiet) return;
    if (el.count) el.count.textContent = "加载失败";
    if (el.body) {
      el.body.innerHTML = `<tr><td colspan="6" class="um-empty">${esc(error.message)}</td></tr>`;
    }
    toast(error.message, true);
  }
}

function closeDetail() {
  const modal = document.getElementById("modal");
  if (modal) modal.classList.remove("on");
  modalOpen = false;
}

function openDetail(rec) {
  const sheet = document.getElementById("sheet");
  const modal = document.getElementById("modal");
  if (!sheet || !modal) return;

  const keys = BASE_KEYS.filter(key => key !== "action")
    .concat(extraPairs(rec).map(pair => pair[0]));
  const fields = keys.map(key => `<dt>${esc(FIELD_LABELS[key] || key)}</dt>
      <dd>${esc(fieldValue(key, rec[key]))}</dd>`).join("");

  sheet.innerHTML = `
    <div class="sheet-head">
      <div>
        <h3>${esc(actionLabel(rec.action))}</h3>
        <p class="s">${esc(fmtTs(rec.ts))} · ${esc(rec.user || "—")}${rec.ip ? " · " + esc(rec.ip) : ""}</p>
      </div>
      <button class="icon-btn" type="button" data-action="au-close" aria-label="关闭">${CLOSE_ICON}</button>
    </div>
    <div class="sheet-body">
      <dl class="au-detail">${fields}</dl>
      <div class="sec-label">原始记录</div>
      <pre class="hintbox au-json">${esc(JSON.stringify(rec, null, 2))}</pre>
    </div>
    <div class="sheet-foot">
      <button class="btn" type="button" data-action="au-close">关闭</button>
    </div>`;

  sheet.querySelectorAll('[data-action="au-close"]').forEach(btn => {
    btn.addEventListener("click", closeDetail);
  });
  modal.classList.add("on");
  modalOpen = true;
}

function csvCell(value) {
  const s = String(value == null ? "" : value);
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

function stamp() {
  const d = new Date();
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

function exportCsv() {
  if (!viewRows.length) {
    toast("当前没有可导出的记录", true);
    return;
  }
  const header = ["时间", "操作人", "动作", "对象", "来源 IP", "详情"];
  const lines = [header.join(",")].concat(viewRows.map(rec => [
    rec.ts, rec.user, actionLabel(rec.action), rec.target, rec.ip, detailText(rec)
  ].map(csvCell).join(",")));
  // 带 BOM，Excel 打开中文不乱码
  const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `audit-${stamp()}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast(`已导出 ${viewRows.length} 条记录`);
}

function resetFilters() {
  keyword = "";
  actionFilter = "";
  riskyOnly = false;
  page = 1;
  if (el.search) el.search.value = "";
  if (el.action) el.action.value = "";
  if (el.risky) el.risky.checked = false;
  render();
}

/** @param {HTMLElement} container */
export function mount(container) {
  if (!isAdmin()) {
    setTimeout(() => {
      toast("仅管理员可以查看审计日志", true);
      navigate("/", { replace: true });
    }, 0);
    return;
  }
  setView("audit");

  container.innerHTML = `
  <section class="view" id="viewAudit">
    <div class="page-head">
      <div class="page-title">
        <h2>审计日志</h2>
      </div>
      <span class="au-hint">登录、上传、删除、账号变更等操作留痕，按时间倒序</span>
    </div>

    <div class="filters au-filters" role="search" aria-label="审计日志筛选">
      <div class="filter-row au-filter-row">
        <div class="search">
          ${SEARCH_ICON}
          <input type="text" id="auSearch" placeholder="搜索操作人 / 对象 / IP / 详情" aria-label="搜索审计记录">
        </div>
        <select id="auAction" aria-label="按动作筛选">
          <option value="">全部动作</option>
        </select>
        <label class="switch" title="只看登录失败、越权拦截、彻底删除、服务异常等">
          <input type="checkbox" id="auRisky">
          <span class="switch-track"><span class="switch-thumb"></span></span>
          <span>仅风险操作</span>
        </label>
        <label class="switch" title="每 15 秒刷新最近记录（页面在后台时暂停）">
          <input type="checkbox" id="auAuto">
          <span class="switch-track"><span class="switch-thumb"></span></span>
          <span>自动刷新</span>
        </label>
        <button class="btn" id="auReset" type="button">重置筛选</button>
        <button class="btn" id="auReload" type="button">刷新</button>
        <button class="btn" id="auExport" type="button">导出 CSV</button>
      </div>
    </div>

    <div class="um-card">
      <div class="um-head">
        <h3>操作记录</h3>
        <span class="um-count" id="auCount">加载中…</span>
      </div>
      <div class="um-scroll">
        <table class="um-table au-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>操作人</th>
              <th>动作</th>
              <th>对象</th>
              <th>来源 IP</th>
              <th>详情</th>
            </tr>
          </thead>
          <tbody id="auBody"><tr><td colspan="6" class="um-empty">加载中…</td></tr></tbody>
        </table>
      </div>
    </div>

    <nav class="pager" id="auPager" aria-label="分页" hidden>
      <div class="pager-ctl">
        <button class="btn btn-sm" id="auPrev" type="button">上一页</button>
        <span class="pager-info" id="auPageInfo">第 1 / 1 页</span>
        <button class="btn btn-sm" id="auNext" type="button">下一页</button>
        <label class="pager-size">每页
          <select id="auPageSize">
            ${PAGE_SIZES.map(size => `<option value="${size}">${size}</option>`).join("")}
          </select> 条
        </label>
      </div>
      <div class="pager-count" id="auFilterCount">–</div>
    </nav>
  </section>`;

  el.body = container.querySelector("#auBody");
  el.count = container.querySelector("#auCount");
  el.search = container.querySelector("#auSearch");
  el.action = container.querySelector("#auAction");
  el.risky = container.querySelector("#auRisky");
  el.auto = container.querySelector("#auAuto");
  el.pager = container.querySelector("#auPager");
  el.pageInfo = container.querySelector("#auPageInfo");
  el.filterCount = container.querySelector("#auFilterCount");
  el.prev = container.querySelector("#auPrev");
  el.next = container.querySelector("#auNext");
  el.pageSize = container.querySelector("#auPageSize");
  el.pageSize.value = String(pageSize);

  el.search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      keyword = el.search.value.trim();
      page = 1;
      render();
    }, 160);
  });
  el.action.addEventListener("change", () => {
    actionFilter = el.action.value;
    page = 1;
    render();
  });
  el.risky.addEventListener("change", () => {
    riskyOnly = el.risky.checked;
    page = 1;
    render();
  });
  el.auto.addEventListener("change", () => {
    autoRefresh = el.auto.checked;
    if (autoRefresh) {
      startAuto();
      toast("已开启自动刷新（15 秒）");
    } else {
      stopAuto();
    }
  });

  container.querySelector("#auReset").addEventListener("click", () => {
    resetFilters();
    toast("已重置筛选条件");
  });
  container.querySelector("#auReload").addEventListener("click", () => {
    load().then(() => toast("已刷新"));
  });
  container.querySelector("#auExport").addEventListener("click", exportCsv);

  el.prev.addEventListener("click", () => {
    if (page <= 1) return;
    page -= 1;
    render();
    scrollToTop("smooth");
  });
  el.next.addEventListener("click", () => {
    page += 1;
    render();
    scrollToTop("smooth");
  });
  el.pageSize.addEventListener("change", () => {
    pageSize = Number(el.pageSize.value) || PAGE_SIZES[0];
    page = 1;
    render();
    scrollToTop("smooth");
  });

  cleanups.push(delegate(el.body, '[data-action="detail"]', "click", (event, match) => {
    const idx = Number(match.dataset.idx);
    const rec = viewRows[idx];
    if (rec) openDetail(rec);
  }));

  // 详情弹窗支持 Esc 关闭（main.js 已处理遮罩点击）
  const onKeydown = event => {
    if (event.key === "Escape" && modalOpen) closeDetail();
  };
  document.addEventListener("keydown", onKeydown);
  cleanups.push(() => document.removeEventListener("keydown", onKeydown));

  load();
}

export function unmount() {
  stopAuto();
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = null;
  autoRefresh = false;
  modalOpen = false;
  cleanups.forEach(fn => {
    try { fn(); } catch (e) { /* ignore */ }
  });
  cleanups = [];
  rows = [];
  viewRows = [];
  keyword = "";
  actionFilter = "";
  riskyOnly = false;
  page = 1;
  Object.keys(el).forEach(key => { delete el[key]; });
}
