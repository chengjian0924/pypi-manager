import { createStore } from "./index.js";
import * as packagesApi from "../api/packages.js";
import { KEYS, readJson, removeSession, writeJson } from "../utils/storage.js";
import { versionMatches } from "../utils/format.js";

/** @type {import('../types/models.js').Filters} */
export const DEFAULT_FILTERS = {
  name: "",
  type: "",
  from: "",
  to: "",
  version: "",
  showDel: false
};

export const packagesStore = createStore({
  projects: [],
  stats: { files: 0, projects: 0, size_h: "–", wheel: 0, sdist: 0, trash: null },
  loading: true,
  error: "",
  filters: Object.assign({}, DEFAULT_FILTERS),
  pinned: []
});

export function getPackages() {
  return packagesStore.getState();
}

/** @param {Partial<import('../types/models.js').Filters>} patch */
export function setFilters(patch) {
  const state = packagesStore.getState();
  packagesStore.setState({
    filters: Object.assign({}, state.filters, patch)
  });
}

export function resetFilters() {
  packagesStore.setState({ filters: Object.assign({}, DEFAULT_FILTERS) });
}

/** @param {string[]} names */
export function setPinned(names) {
  packagesStore.setState({ pinned: Array.isArray(names) ? names.slice() : [] });
}

/** @param {import('../types/models.js').Filters} filters */
export function hasFilters(filters) {
  return !!(filters.name || filters.type || filters.from || filters.to ||
    filters.version || filters.showDel);
}

/** @param {{filename?: string, kind?: string}} f */
function fileKindOf(f) {
  if (f && f.kind) return f.kind;
  return /\.whl$/i.test((f && f.filename) || "") ? "wheel" : "sdist";
}

/**
 * @param {import('../types/models.js').Package} project
 * @param {import('../types/models.js').FileEntry} file
 * @param {import('../types/models.js').Filters} filters
 */
export function fileMatches(project, file, filters) {
  if (!filters.showDel && file.deleted) return false;
  if (filters.type && fileKindOf(file) !== filters.type) return false;
  const day = String(file.uploaded_at || "").slice(0, 10);
  if (filters.from && day && day < filters.from) return false;
  if (filters.to && day && day > filters.to) return false;
  if (filters.version && !versionMatches(file.version, filters.version)) return false;
  if (filters.name) {
    const keyword = filters.name.toLowerCase();
    const hay = [project.name, project.display, file.filename, file.note].join(" ").toLowerCase();
    if (!hay.includes(keyword)) return false;
  }
  return true;
}

/**
 * @param {ReturnType<typeof packagesStore.getState>} [state]
 * @returns {import('../types/models.js').Package[]}
 */
export function getFilteredProjects(state = packagesStore.getState()) {
  const filters = state.filters;
  const out = [];
  (state.projects || []).forEach(project => {
    const files = (project.files || []).filter(file => fileMatches(project, file, filters));
    if (files.length) out.push(Object.assign({}, project, { files }));
  });
  if (state.pinned.length) {
    out.sort((a, b) => {
      const ia = state.pinned.indexOf(a.name), ib = state.pinned.indexOf(b.name);
      if (ia === ib) return 0;
      if (ia < 0) return 1;
      if (ib < 0) return -1;
      return ia - ib;
    });
  }
  return out;
}

/** @param {number} id */
export function findFile(id) {
  const state = packagesStore.getState();
  for (const project of state.projects) {
    for (const file of project.files) {
      if (file.id === id) return [project, file];
    }
  }
  return [null, null];
}

/** 并发刷新时只让最后一次响应写入状态，避免旧响应覆盖新视图 */
let loadSeq = 0;

/**
 * 重新拉取列表。是否连带回收站历史行由 filters.showDel 决定：
 * 默认轻量（deleted=0），进入回收站视图时才拉全量。
 * @param {{quiet?: boolean}} [options] quiet=true 时补拉失败只保留当前视图，
 *        不把整页替换成错误提示（用于「查看 / 返回列表」这类视图切换）。
 */
export async function load(options = {}) {
  const seq = ++loadSeq;
  const quiet = !!options.quiet;
  const includeDeleted = !!packagesStore.getState().filters.showDel;
  packagesStore.setState({ loading: true, error: "" });
  try {
    const data = await packagesApi.fetchPackages(includeDeleted);
    if (seq !== loadSeq) return data;
    packagesStore.setState({
      projects: Array.isArray(data && data.projects) ? data.projects : [],
      stats: (data && data.stats) ||
        { files: 0, projects: 0, size_h: "–", wheel: 0, sdist: 0, trash: null },
      loading: false,
      error: ""
    });
    return data;
  } catch (error) {
    if (seq !== loadSeq) throw error;
    packagesStore.setState({
      loading: false,
      error: quiet ? "" : (error.message || "加载失败")
    });
    throw error;
  }
}

/** @param {number} id @param {string} note */
export async function saveNote(id, note) {
  await packagesApi.updateNote(id, note);
  load().catch(() => { /* 列表刷新失败时保留当前视图 */ });
}

/** @param {number} id */
export async function deleteFile(id) {
  await packagesApi.deleteFile(id);
  load().catch(() => { /* 列表刷新失败时保留当前视图 */ });
}

/** @param {number} id */
export async function restoreFile(id) {
  await packagesApi.restoreFile(id);
  load().catch(() => { /* 列表刷新失败时保留当前视图 */ });
}

/** @param {number} id */
export async function purgeFile(id) {
  await packagesApi.purgeFile(id);
  load().catch(() => { /* 列表刷新失败时保留当前视图 */ });
}

/** 清空回收站并刷新列表 */
export async function emptyTrash() {
  const result = await packagesApi.emptyTrash();
  load().catch(() => { /* 列表刷新失败时保留当前视图 */ });
  return result;
}

/** @param {string[]} projects @param {number} count */
export function saveUploadResult(projects, count) {
  writeJson(sessionStorage, KEYS.UPLOAD_RESULT, {
    projects: projects,
    count: count,
    at: Date.now()
  });
}

/** @returns {{projects?: string[], count?: number, at?: number}|null} */
export function consumeUploadResult() {
  const result = readJson(sessionStorage, KEYS.UPLOAD_RESULT);
  removeSession(KEYS.UPLOAD_RESULT);
  return result;
}

/** @param {number} scroll */
export function saveListState(scroll) {
  const state = packagesStore.getState();
  writeJson(sessionStorage, KEYS.LIST_STATE, Object.assign({}, state.filters, {
    scroll: scroll || 0,
    at: Date.now()
  }));
}

/** 恢复筛选并返回保存的滚动位置。 */
export function restoreListState() {
  const saved = readJson(sessionStorage, KEYS.LIST_STATE);
  if (!saved) return null;
  packagesStore.setState({
    filters: {
      name: saved.name || "",
      type: saved.type || "",
      from: saved.from || "",
      to: saved.to || "",
      version: saved.version || "",
      showDel: !!saved.showDel
    }
  });
  return saved;
}
