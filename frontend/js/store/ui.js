import { createStore } from "./index.js";
import { DEFAULT_MAX_UPLOAD } from "../api/admin.js";
import {
  KEYS, getThemePreference, setLocal, removeLocal
} from "../utils/storage.js";

const THEME_LABELS = { auto: "跟随系统", light: "浅色", dark: "深色" };

function currentThemeMode() {
  const attr = document.documentElement.getAttribute("data-mode");
  if (attr === "light" || attr === "dark") return attr;
  return getThemePreference();
}

export const uiStore = createStore({
  view: document.documentElement.getAttribute("data-view") || "list",
  themeMode: currentThemeMode(),
  config: { max_upload: DEFAULT_MAX_UPLOAD, version: "", open_read: false },
  upload: { files: [], seq: 0, uploading: false }
});

/** @param {string} mode */
export function themeLabel(mode) {
  return THEME_LABELS[mode] || THEME_LABELS.auto;
}

export function getThemeMode() {
  return uiStore.getState().themeMode;
}

/** @param {'auto'|'light'|'dark'} mode */
export function setTheme(mode) {
  if (mode === "auto") removeLocal(KEYS.THEME);
  else setLocal(KEYS.THEME, mode);
  if (typeof window.__applyTheme === "function") window.__applyTheme(mode);
  uiStore.setState({ themeMode: mode });
  return mode;
}

export function cycleTheme() {
  const order = ["auto", "light", "dark"];
  const current = getThemeMode();
  const next = order[(order.indexOf(current) + 1) % order.length];
  setTheme(next);
  return next;
}

/** @param {string} view */
export function setView(view) {
  document.documentElement.setAttribute("data-view", view);
  uiStore.setState({ view });
}

/** @param {{max_upload?: number, version?: string, open_read?: boolean}} config */
export function setConfig(config) {
  uiStore.setState({
    config: {
      max_upload: Number(config && config.max_upload) || DEFAULT_MAX_UPLOAD,
      version: (config && config.version) || "",
      open_read: !!(config && config.open_read)
    }
  });
  return uiStore.getState().config;
}

export function getMaxUpload() {
  return uiStore.getState().config.max_upload || DEFAULT_MAX_UPLOAD;
}

export function getUploadState() {
  return uiStore.getState().upload;
}

/** @param {number} id */
export function findUploadItem(id) {
  return uiStore.getState().upload.files.find(item => item.id === id) || null;
}

export const uploadActions = {
  /**
   * @param {{file: File, status?: string, err?: string, clientInvalid?: boolean}[]} items
   * @returns {Array<import('../types/models.js').UploadItem>}
   */
  add(items) {
    const state = uiStore.getState().upload;
    let seq = state.seq;
    const added = items.map(item => {
      const id = ++seq;
      return Object.assign({ id }, item, {
        status: item.status || "pending",
        err: item.err || "",
        clientInvalid: !!item.clientInvalid,
        pct: 0,
        message: "",
        canceled: false
      });
    });
    uiStore.setState({
      upload: Object.assign({}, state, { seq, files: state.files.concat(added) })
    });
    return added;
  },

  /** @param {number} id @param {object} patch */
  update(id, patch) {
    const state = uiStore.getState().upload;
    let changed = false;
    const files = state.files.map(item => {
      if (item.id !== id) return item;
      changed = true;
      return Object.assign({}, item, patch);
    });
    if (changed) uiStore.setState({ upload: Object.assign({}, state, { files }) });
  },

  /** @param {number} id */
  remove(id) {
    const state = uiStore.getState().upload;
    uiStore.setState({
      upload: Object.assign({}, state, { files: state.files.filter(item => item.id !== id) })
    });
  },

  clear() {
    const state = uiStore.getState().upload;
    uiStore.setState({
      upload: Object.assign({}, state, { files: [], uploading: false })
    });
  },

  /** @param {boolean} uploading */
  setUploading(uploading) {
    const state = uiStore.getState().upload;
    uiStore.setState({ upload: Object.assign({}, state, { uploading: !!uploading }) });
  },

  /** @param {number[]} ids */
  resetForRetry(ids) {
    const state = uiStore.getState().upload;
    const files = state.files.map(item => {
      if (ids.indexOf(item.id) < 0) return item;
      return Object.assign({}, item, {
        status: "pending", err: "", failure: null, pct: 0, message: "", canceled: false
      });
    });
    uiStore.setState({ upload: Object.assign({}, state, { files }) });
  }
};
