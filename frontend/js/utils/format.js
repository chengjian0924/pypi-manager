/** @param {number|null|undefined} n */
export function fmtBytes(n) {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let v = Number(n) || 0, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v : v.toFixed(v >= 10 ? 1 : 2)) + " " + u[i];
}

/** @param {string} value */
export function formatUploadedAt(value) {
  return String(value || "").slice(0, 16).replace("T", " ");
}

/** @param {{kind?: string, filename?: string}} f */
export function fileKind(f) {
  if (f && f.kind) return f.kind;
  return /\.whl$/i.test((f && f.filename) || "") ? "wheel" : "sdist";
}

/** @param {*} v */
export function verParts(v) {
  return String(v == null ? "" : v).replace(/^[vV]/, "").split(/[.\-+_]/).map(x => {
    const n = parseInt(x, 10);
    return isNaN(n) ? 0 : n;
  });
}

/** @param {*} a @param {*} b */
export function cmpVer(a, b) {
  const pa = verParts(a), pb = verParts(b);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] || 0) - (pb[i] || 0);
    if (d) return d < 0 ? -1 : 1;
  }
  return 0;
}

/** @param {*} v @param {string} expr */
export function versionMatches(v, expr) {
  if (!expr) return true;
  const s = String(v == null ? "" : v);
  const m = expr.match(/^(>=|<=|==|!=|>|<|=)\s*(.+)$/);
  if (m) {
    const c = cmpVer(s, m[2].trim());
    if (m[1] === ">=") return c >= 0;
    if (m[1] === "<=") return c <= 0;
    if (m[1] === ">") return c > 0;
    if (m[1] === "<") return c < 0;
    if (m[1] === "!=") return c !== 0;
    return c === 0;
  }
  return s.toLowerCase().includes(expr.toLowerCase());
}

/** @param {{version: string}[]} files */
export function newestVersion(files) {
  const vs = files.map(f => f.version);
  return vs.length ? vs.sort((a, b) => {
    const pa = a.split(".").map(Number), pb = b.split(".").map(Number);
    for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
      const d = (pb[i] || 0) - (pa[i] || 0);
      if (d) return d;
    }
    return 0;
  })[0] : "—";
}

/** @param {string} role */
export function roleLabel(role) {
  return role === "admin" ? "管理员" : (role === "uploader" ? "上传者" : (role === "viewer" ? "只读" : ""));
}
