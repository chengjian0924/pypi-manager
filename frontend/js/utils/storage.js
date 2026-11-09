export const KEYS = {
  CRED: "pm_cred",
  THEME: "pm_theme",
  LIST_STATE: "pm_list_state",
  UPLOAD_RESULT: "pm_upload_result",
  UPLOAD_ORIGIN: "pm_upload_origin"
};

/** @param {string} key */
export function getSession(key) {
  try { return sessionStorage.getItem(key); } catch (e) { return null; }
}

/** @param {string} key @param {string} value */
export function setSession(key, value) {
  try { sessionStorage.setItem(key, value); } catch (e) { /* ignore */ }
}

/** @param {string} key */
export function removeSession(key) {
  try { sessionStorage.removeItem(key); } catch (e) { /* ignore */ }
}

/** @param {string} key */
export function getLocal(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}

/** @param {string} key @param {string} value */
export function setLocal(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* ignore */ }
}

/** @param {string} key */
export function removeLocal(key) {
  try { localStorage.removeItem(key); } catch (e) { /* ignore */ }
}

/** @param {Storage} storage @param {string} key */
export function readJson(storage, key) {
  try {
    const raw = storage.getItem(key);
    const value = raw ? JSON.parse(raw) : null;
    return value && typeof value === "object" ? value : null;
  } catch (e) {
    return null;
  }
}

/** @param {Storage} storage @param {string} key @param {*} value */
export function writeJson(storage, key, value) {
  try { storage.setItem(key, JSON.stringify(value)); } catch (e) { /* ignore */ }
}

export function getCredentials() {
  return getSession(KEYS.CRED) || "";
}

/** @param {string} value */
export function setCredentials(value) {
  if (value) setSession(KEYS.CRED, value);
  else removeSession(KEYS.CRED);
}

export function removeCredentials() {
  removeSession(KEYS.CRED);
}

export function getThemePreference() {
  const saved = getLocal(KEYS.THEME);
  return (saved === "light" || saved === "dark") ? saved : "auto";
}
