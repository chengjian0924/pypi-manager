import { authStore, canManage, isAdmin } from "../store/auth.js";
import * as packages from "../store/packages.js";
import { packagesStore } from "../store/packages.js";
import { setView } from "../store/ui.js";
import { createFilterBar } from "../components/filter-bar.js";
import { renderProject, renderDetailSheet } from "../components/package-card.js";
import { toast } from "../components/toast.js";
import { exportCsv } from "../api/admin.js";
import { fetchClientConfig, resetClientConfig, saveClientConfig } from "../api/settings.js";
import { esc, delegate, scrollToTop } from "../utils/dom.js";

const FILES_ICON = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/></svg>`;
const PROJECTS_ICON = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5"/><path d="m3 17 9 5 9-5"/></svg>`;
const SIZE_ICON = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>`;
const CLOSE_ICON_LG = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>`;
const PACKAGE_ICON = `<svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 8.5 12 3 3 8.5v7L12 21l9-5.5v-7Z"/><path d="M3 8.5 12 14l9-5.5"/><path d="M12 14v7"/></svg>`;
const SEARCH_ICON = `<svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>`;

let cleanups = [];
let filterBar = null;
let listEl = null;
let sheetEl = null;
let modalEl = null;
let pagerEl = null;
let pageInfoEl = null;
let pagerCountEl = null;
let prevBtn = null;
let nextBtn = null;
let sizeSelect = null;
let trashBar = null;
let trashText = null;
let trashView = null;
let trashEmpty = null;

const PAGE_SIZES = [10, 20, 50];
let page = 1;
let pageSize = PAGE_SIZES[0];
/** 中等宽度下表格收起「类型 / 上传人」两列，给文件名留出空间 */
let compactTable = false;

function closeModal() {
  modalEl.classList.remove("on");
}

/** 分页控件：总数恒定，页码/按钮状态随筛选结果刷新 */
function renderPager(total, totalPages, fileCount, filters) {
  if (!pagerEl) return;
  pagerEl.hidden = total === 0;
  if (!total) return;
  pageInfoEl.textContent = "第 " + page + " / " + totalPages + " 页";
  prevBtn.disabled = page <= 1;
  nextBtn.disabled = page >= totalPages;
  pagerCountEl.innerHTML = "<b>" + total + "</b> 个包名 · <b>" + fileCount + "</b> 个分发包" +
    (packages.hasFilters(filters) ? " · 已应用筛选" : "");
}

/** 回收站概览条：不占用列表空间，有内容或正处于回收站视图时出现在统计卡下方 */
function renderTrashBar(trash, showDel) {
  if (!trashBar) return;
  const count = (trash && trash.count) || 0;
  trashBar.hidden = !count && !showDel;
  trashView.textContent = showDel ? "返回列表" : "查看";
  trashEmpty.hidden = !count || authStore.getState().role !== "admin";
  if (!count) {
    trashText.innerHTML = showDel ? "回收站已清空" : "";
    return;
  }
  const parts = ["回收站 <b>" + count + "</b> 个", "占用 " + esc(trash.size_h || "–")];
  if (trash.oldest_left_days != null) {
    parts.push(trash.oldest_left_days > 0
      ? "最早一条 <b>" + trash.oldest_left_days + "</b> 天后清理"
      : "最早一条即将清理");
  } else if (trash.keep_days) {
    parts.push("保留 " + trash.keep_days + " 天");
  }
  trashText.innerHTML = parts.join(" · ");
}

function renderAll() {
  const state = packagesStore.getState();
  const stats = state.stats || {};
  const initialLoading = state.loading && !state.projects.length;

  if (!initialLoading && !state.error) {
    const files = stats.files;
    const projects = stats.projects;
    document.getElementById("sFiles").textContent = files == null ? "–" : files;
    document.getElementById("sFilesSub").textContent =
      "Wheel " + (stats.wheel || 0) + " · 源码包 " + (stats.sdist || 0);
    document.getElementById("sProjects").textContent = projects == null ? "–" : projects;
    document.getElementById("sProjectsSub").textContent =
      files && projects ? "平均每个包名 " + (files / projects).toFixed(1) + " 个分发包"
        : "去重后的包名";
    document.getElementById("sSize").textContent = stats.size_h || "–";
    document.getElementById("sSizeSub").textContent =
      stats.trash && stats.trash.count ? "回收站另占 " + stats.trash.size_h : "无回收站占用";
    renderTrashBar(stats.trash, !!state.filters.showDel);
  }

  if (initialLoading) {
    listEl.innerHTML = '<div class="empty">加载中…</div>';
    renderPager(0, 0, 0, state.filters);
    return;
  }
  if (state.error) {
    listEl.innerHTML = '<div class="empty">加载失败：' + esc(state.error) + "</div>";
    renderPager(0, 0, 0, state.filters);
    return;
  }

  const allProjects = packages.getFilteredProjects(state);
  let fileCount = 0;
  allProjects.forEach(project => { fileCount += project.files.length; });

  const totalPages = Math.max(1, Math.ceil(allProjects.length / pageSize));
  if (page > totalPages) page = totalPages;
  const shownProjects = allProjects.slice((page - 1) * pageSize, page * pageSize);
  renderPager(allProjects.length, totalPages, fileCount, state.filters);

  if (!allProjects.length) {
    if (!(state.projects || []).length) {
      listEl.innerHTML = `<div class="empty">
        ${PACKAGE_ICON}
        <div>还没有分发包。点击顶部「上传分发包」导入。</div>
      </div>`;
    } else {
      listEl.innerHTML = `<div class="empty">
        ${SEARCH_ICON}
        <div>没有符合当前筛选条件的包。</div>
        <button class="btn btn-sm" type="button" data-action="reset-filters">重置筛选</button>
      </div>`;
    }
    return;
  }

  const pinnedSet = new Set(state.pinned);
  listEl.innerHTML = shownProjects.map(project => renderProject(project, {
    isNew: pinnedSet.has(project.name),
    canManage: canManage,
    isAdmin: authStore.getState().role === "admin",
    compact: compactTable
  })).join("");
}

/**
 * 名称 / 类型 / 日期 / 版本筛选都在浏览器里算，不发请求；
 * 只有 showDel 翻转会改变服务端返回的数据集，此时才按需补拉一次列表。
 * 补拉失败时保留当前视图，不把整页替换成错误提示。
 */
function applyFilters(filters) {
  const before = packagesStore.getState().filters.showDel;
  packages.setFilters(filters);
  if (!!filters.showDel !== before) {
    packages.load({ quiet: true }).catch(() => { /* 保留当前视图 */ });
  }
}

function handleReset() {
  page = 1;
  filterBar.clearInputs();
  applyFilters(packages.DEFAULT_FILTERS);
  toast("已重置筛选条件");
  scrollToTop("smooth");
}

async function handleExport() {
  try {
    const blob = await exportCsv();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "pypi-manifest.csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  } catch (error) {
    if (error.status === 401) return;
    toast("导出失败：" + error.message, true);
  }
}

function openDetail(id) {
  const [project, file] = packages.findFile(id);
  if (!file) return;
  sheetEl.innerHTML = renderDetailSheet(project, file, canManage(file));
  modalEl.classList.add("on");
}

async function handleSaveNote(id) {
  try {
    await packages.saveNote(id, document.getElementById("noteInput").value);
    toast("已保存");
    closeModal();
  } catch (error) {
    toast(error.message, true);
  }
}

async function handleDelete(id) {
  const [, file] = packages.findFile(id);
  if (!file) return;
  if (!confirm("确认删除 " + file.filename + " ？\n文件会移入回收站，可恢复，但当前源里会立即消失。")) return;
  try {
    await packages.deleteFile(id);
    toast("已移入回收站");
  } catch (error) {
    toast(error.message, true);
  }
}

async function handleRestore(id) {
  try {
    await packages.restoreFile(id);
    toast("已恢复");
  } catch (error) {
    toast(error.message, true);
  }
}

async function handlePurge(id) {
  const [, file] = packages.findFile(id);
  if (!file) return;
  if (!confirm("彻底删除 " + file.filename + " ？\n文件与记录都会被清除，无法恢复。")) return;
  try {
    await packages.purgeFile(id);
    toast("已彻底删除");
  } catch (error) {
    toast(error.message, true);
  }
}

/** 文案里的 {base}/{host} 按本次访问地址替换：换域名或加反代前缀都不用改内容 */
function fillCfgText(text) {
  return String(text || "")
    .replace(/\{base\}/g, location.origin)
    .replace(/\{host\}/g, location.hostname);
}

function cfgMeta(cfg) {
  if (!cfg || !cfg.custom) return "";
  const when = cfg.updated_at ? String(cfg.updated_at).replace("T", " ").slice(0, 19) : "";
  const who = cfg.updated_by ? " · " + esc(cfg.updated_by) : "";
  const time = when ? " · " + esc(when) : "";
  return `<div class="cfg-note"><b>已自定义</b>${who}${time}</div>`;
}

/** 客户端接入（只读）：内容由服务端给出，admin 可在弹窗内直接编辑 */
async function openSettings() {
  let cfg;
  try {
    cfg = await fetchClientConfig();
  } catch (error) {
    toast("客户端配置加载失败：" + error.message, true);
    return;
  }
  renderSettingsView(cfg);
}

function renderSettingsView(cfg) {
  const admin = isAdmin();
  const order = (cfg && cfg.order) || [];
  const labels = (cfg && cfg.labels) || {};
  const sections = (cfg && cfg.sections) || {};
  const blocks = order.map(key => {
    const text = sections[key];
    if (!text) return "";
    return '<div class="sec-label">' + esc(labels[key] || key) + "</div>" +
      '<div class="hintbox">' + esc(fillCfgText(text)) + "</div>";
  }).join("");

  sheetEl.innerHTML = `
    <div class="sheet-head">
      <div>
        <h3>客户端接入</h3>
      </div>
      <button class="icon-btn" type="button" data-action="close-modal" aria-label="关闭">
        ${CLOSE_ICON_LG}
      </button>
    </div>
    <div class="sheet-body">
      ${cfgMeta(cfg)}
      ${blocks || '<div class="empty">暂无可展示的配置</div>'}
    </div>
    <div class="sheet-foot">
      ${admin ? '<button class="btn" type="button" data-cfg="edit">编辑</button>' : ""}
      ${admin && cfg && cfg.custom ? '<button class="btn" type="button" data-cfg="reset">恢复默认</button>' : ""}
      <button class="btn btn-primary" type="button" data-action="close-modal">好的</button>
    </div>`;
  modalEl.classList.add("on");

  sheetEl.querySelectorAll("[data-cfg]").forEach(btn => {
    btn.addEventListener("click", () => {
      if (btn.dataset.cfg === "edit") renderSettingsEdit(cfg);
      else if (btn.dataset.cfg === "reset") handleCfgReset();
    });
  });
}

function renderSettingsEdit(cfg) {
  const order = (cfg && cfg.order) || [];
  const labels = (cfg && cfg.labels) || {};
  const sections = (cfg && cfg.sections) || {};
  const maxChars = Number(cfg && cfg.max_section_chars) || 4000;
  // 按 key 顺序渲染三段输入框，标题沿用服务端给的名字
  const fields = order.map(key => `
      <div class="field cfg-field">
        <label for="cfg-${esc(key)}">${esc(labels[key] || key)}</label>
        <textarea id="cfg-${esc(key)}" class="cfg-editor" data-key="${esc(key)}"
          spellcheck="false" maxlength="${maxChars}"></textarea>
      </div>`).join("");

  sheetEl.innerHTML = `
    <div class="sheet-head">
      <div>
        <h3>编辑客户端接入</h3>
        <p class="s">保存后所有登录用户刷新即可看到</p>
      </div>
      <button class="icon-btn" type="button" data-cfg="cancel" aria-label="关闭">
        ${CLOSE_ICON_LG}
      </button>
    </div>
    <div class="sheet-body">
      <div class="cfg-note">
        <span><code>{base}</code> 会替换成 <code>${esc(location.origin)}</code>，
        <code>{host}</code> 替换成 <code>${esc(location.hostname)}</code>；
        某段留空则用内置默认。单段上限 ${maxChars} 字符。</span>
      </div>
      ${fields}
    </div>
    <div class="sheet-foot">
      <button class="btn" type="button" data-cfg="cancel">取消</button>
      ${cfg && cfg.custom ? '<button class="btn" type="button" data-cfg="reset">恢复默认</button>' : ""}
      <button class="btn btn-primary" type="button" data-cfg="save">保存</button>
    </div>`;

  // 预填用 value 而不是拼 HTML：命令里带引号、反斜杠、换行，走 textContent 才不会被解析
  // 高度跟着内容走，避免默认模板一进来就要在框里滚动
  const autoGrow = ta => {
    ta.style.height = "auto";
    ta.style.height = Math.min(Math.max(ta.scrollHeight + 2, 96), 340) + "px";
  };
  sheetEl.querySelectorAll(".cfg-editor").forEach(ta => {
    ta.value = sections[ta.dataset.key] || "";
    autoGrow(ta);
    ta.addEventListener("input", () => autoGrow(ta));
  });

  const saveBtn = sheetEl.querySelector('[data-cfg="save"]');
  sheetEl.querySelectorAll("[data-cfg]").forEach(btn => {
    const act = btn.dataset.cfg;
    if (act === "cancel") btn.addEventListener("click", () => renderSettingsView(cfg));
    else if (act === "reset") btn.addEventListener("click", handleCfgReset);
    else if (act === "save") {
      btn.addEventListener("click", async () => {
        const payload = {};
        sheetEl.querySelectorAll(".cfg-editor").forEach(ta => {
          payload[ta.dataset.key] = ta.value;
        });
        if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = "保存中…"; }
        try {
          const next = await saveClientConfig(payload);
          toast("已保存，其他人生效需刷新页面");
          renderSettingsView(next);
        } catch (error) {
          toast(error.message, true);
          if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = "保存"; }
        }
      });
    }
  });
}

async function handleCfgReset() {
  if (!confirm("恢复默认会清空已自定义的文案，确定继续？")) return;
  try {
    const next = await resetClientConfig();
    toast("已恢复默认文案");
    renderSettingsView(next);
  } catch (error) {
    toast(error.message, true);
  }
}

/**
 * @param {HTMLElement} container
 */
export async function mount(container) {
  setView("list");
  container.innerHTML = `
  <main class="view" id="viewList">
    <section class="stats" aria-label="仓库统计">
      <div class="stat">
        <div class="stat-top"><span class="stat-label">分发包</span><span class="stat-ico">${FILES_ICON}</span></div>
        <b id="sFiles">–</b>
        <span class="stat-sub" id="sFilesSub">Wheel 0 · 源码包 0</span>
      </div>
      <div class="stat">
        <div class="stat-top"><span class="stat-label">包名</span><span class="stat-ico">${PROJECTS_ICON}</span></div>
        <b id="sProjects">–</b>
        <span class="stat-sub" id="sProjectsSub">去重后的包名</span>
      </div>
      <div class="stat">
        <div class="stat-top"><span class="stat-label">占用空间</span><span class="stat-ico">${SIZE_ICON}</span></div>
        <b id="sSize">–</b>
        <span class="stat-sub" id="sSizeSub">无回收站占用</span>
      </div>
    </section>

    <div class="trash-bar" id="trashBar" hidden>
      <span class="tb-text" id="trashText"></span>
      <button class="btn btn-sm" type="button" id="trashView">查看</button>
      <button class="btn btn-sm btn-danger" type="button" id="trashEmpty" hidden>清空回收站</button>
    </div>

    <div id="filterMount"></div>

    <div class="projects" id="list"><div class="empty">加载中…</div></div>

    <nav class="pager" id="pager" aria-label="分页" hidden>
      <div class="pager-ctl">
        <button class="btn btn-sm" id="prevPage" type="button">上一页</button>
        <span class="pager-info" id="pageInfo">第 1 / 1 页</span>
        <button class="btn btn-sm" id="nextPage" type="button">下一页</button>
        <label class="pager-size">每页
          <select id="pageSize">
            <option value="10">10</option>
            <option value="20">20</option>
            <option value="50">50</option>
          </select> 条
        </label>
      </div>
      <div class="pager-count" id="filterCount">–</div>
    </nav>
  </main>`;

  listEl = container.querySelector("#list");
  modalEl = document.getElementById("modal");
  sheetEl = document.getElementById("sheet");
  pagerEl = container.querySelector("#pager");
  pageInfoEl = container.querySelector("#pageInfo");
  pagerCountEl = container.querySelector("#filterCount");
  prevBtn = container.querySelector("#prevPage");
  nextBtn = container.querySelector("#nextPage");
  sizeSelect = container.querySelector("#pageSize");
  sizeSelect.value = String(pageSize);
  trashBar = container.querySelector("#trashBar");
  trashText = container.querySelector("#trashText");
  trashView = container.querySelector("#trashView");
  trashEmpty = container.querySelector("#trashEmpty");

  filterBar = createFilterBar(container.querySelector("#filterMount"), {
    onChange: filters => {
      page = 1;
      applyFilters(filters);
    },
    onReset: handleReset
  });

  // 列数随断点变化：桌面 8 列，中等宽度 6 列，窄屏（≤720，CSS 堆叠卡片）保持 8 列
  const compactMql = window.matchMedia("(min-width:721px) and (max-width:979px)");
  compactTable = compactMql.matches;
  const onCompactChange = event => {
    compactTable = event.matches;
    renderAll();
  };
  if (compactMql.addEventListener) compactMql.addEventListener("change", onCompactChange);
  else if (compactMql.addListener) compactMql.addListener(onCompactChange);

  // 顶部按钮：列表页专属，事件直接绑定到当前页面实例
  document.getElementById("exportNav").onclick = handleExport;
  document.getElementById("settingsNav").onclick = openSettings;

  prevBtn.addEventListener("click", () => {
    if (page <= 1) return;
    page -= 1;
    renderAll();
    scrollToTop("smooth");
  });
  nextBtn.addEventListener("click", () => {
    page += 1;
    renderAll();
    scrollToTop("smooth");
  });
  sizeSelect.addEventListener("change", () => {
    pageSize = Number(sizeSelect.value) || PAGE_SIZES[0];
    page = 1;
    renderAll();
    scrollToTop("smooth");
  });

  trashView.addEventListener("click", () => {
    page = 1;
    applyFilters({ showDel: !packagesStore.getState().filters.showDel });
    if (filterBar) filterBar.setFilters(packagesStore.getState().filters);
  });

  trashEmpty.addEventListener("click", async () => {
    if (!confirm("清空回收站会彻底删除其中所有文件，无法恢复。确定继续？")) return;
    trashEmpty.disabled = true;
    try {
      const result = await packages.emptyTrash();
      toast(result && result.files ? "已清空回收站，" + result.files + " 个文件被删除" : "回收站已清空");
    } catch (error) {
      toast(error.message, true);
    } finally {
      trashEmpty.disabled = false;
    }
  });

  const listCleanup = delegate(listEl, "[data-action]", "click", (event, match) => {
    if (match.tagName === "BUTTON") event.preventDefault();
    const action = match.dataset.action;
    const id = Number(match.dataset.id);
    if (action === "reset-filters") handleReset();
    else if (action === "detail") openDetail(id);
    else if (action === "delete") handleDelete(id);
    else if (action === "restore") handleRestore(id);
    else if (action === "purge") handlePurge(id);
  });

  const headClick = delegate(listEl, ".phead", "click", (event, head) => {
    if (head.parentNode) head.parentNode.classList.toggle("open");
  });

  const headKeydown = delegate(listEl, ".phead", "keydown", (event, head) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (head.parentNode) head.parentNode.classList.toggle("open");
    }
  });

  const sheetCleanup = delegate(sheetEl, "[data-action]", "click", (event, match) => {
    const action = match.dataset.action;
    if (action === "close-modal") closeModal();
    else if (action === "save-note") handleSaveNote(Number(match.dataset.id));
  });

  cleanups.push(
    listCleanup, headClick, headKeydown, sheetCleanup,
    packagesStore.subscribe(renderAll),
    () => {
      if (compactMql.removeEventListener) compactMql.removeEventListener("change", onCompactChange);
      else if (compactMql.removeListener) compactMql.removeListener(onCompactChange);
    },
    () => filterBar.destroy()
  );

  const uploadResult = packages.consumeUploadResult();
  let saved = null;
  if (uploadResult && Array.isArray(uploadResult.projects) && uploadResult.projects.length) {
    page = 1;
    packages.resetFilters();
    filterBar.clearInputs();
    packages.setPinned(uploadResult.projects.slice());
  } else {
    saved = packages.restoreListState();
    if (saved) filterBar.setFilters(packagesStore.getState().filters);
  }

  renderAll();

  try {
    await packages.load();
  } catch (error) {
    return;
  }
  if (!authStore.getState().authOk) return;

  if (uploadResult && packagesStore.getState().pinned.length) {
    scrollToTop("auto");
    return;
  }

  if (saved) {
    requestAnimationFrame(() => window.scrollTo(0, saved.scroll || 0));
  }
}

export function saveState() {
  packages.saveListState(window.scrollY);
}

export function unmount() {
  saveState();
  cleanups.forEach(fn => {
    try { fn(); } catch (e) { /* ignore */ }
  });
  cleanups = [];
  filterBar = null;
}
