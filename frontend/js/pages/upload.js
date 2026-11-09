import { navigate, showLogin } from "../router.js";
import { ensureAuth } from "../store/auth.js";
import {
  setView, getMaxUpload, getUploadState, findUploadItem, uploadActions, uiStore
} from "../store/ui.js";
import { saveUploadResult } from "../store/packages.js";
import { uploadFile, explainUploadFailure } from "../api/upload.js";
import { renderFileRow } from "../components/file-row.js";
import { toast } from "../components/toast.js";
import { fmtBytes } from "../utils/format.js";
import { delegate, scrollToTop } from "../utils/dom.js";

const CLOUD_ICON = `<svg class="drop-ico" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 16.5A4.5 4.5 0 0 1 6.4 8a6 6 0 0 1 11.4 1.4A4 4 0 0 1 18 17"/><path d="M12 12v7"/><path d="m9 15 3-3 3 3"/></svg>`;
const CLOSE_ICON = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>`;

let cleanups = [];
let handles = new Map();
let lastUpload = null;

function validateUploadFile(file, maxUpload) {
  const name = file.name || "";
  const low = name.toLowerCase();
  if (!/\.(whl|tar\.gz|zip)$/.test(low)) {
    return "不支持的文件格式：只接受 .whl / .tar.gz / .zip";
  }
  if (!file.size) {
    return "文件内容为空（0 字节），请确认导出过程已完整结束";
  }
  if (file.size > maxUpload) {
    return "文件大小 " + file.size.toLocaleString("en-US") + " 字节（约 " + fmtBytes(file.size) +
      "）超出上限 " + maxUpload.toLocaleString("en-US") + " 字节（" + fmtBytes(maxUpload) +
      "），请拆分或联系管理员";
  }
  if (low.endsWith(".whl")) {
    const parts = name.slice(0, -4).split("-");
    if (parts.length < 5) {
      return "wheel 文件名不符合 PEP 427：应为 名称-版本-py标签-abi标签-平台标签.whl";
    }
  }
  if (low.endsWith(".tar.gz")) {
    const base = name.slice(0, -7);
    const i = base.lastIndexOf("-");
    if (i <= 0 || !/^\d/.test(base.slice(i + 1))) {
      return "源码包文件名应为 名称-版本.tar.gz，且版本需以数字开头";
    }
  }
  return "";
}

function hideUpResult() {
  const box = document.getElementById("upResult");
  if (box) box.hidden = true;
}

function dismissUpAlert() {
  const box = document.getElementById("upAlert");
  if (box) box.hidden = true;
}

function showUploadError(title, detail, hint) {
  const box = document.getElementById("upAlert");
  if (!box) return;
  document.getElementById("upAlertTitle").textContent = title;
  document.getElementById("upAlertDetail").textContent = detail || "";
  const hintNode = document.getElementById("upAlertHint");
  hintNode.textContent = hint || "";
  hintNode.hidden = !hint;
  box.hidden = false;
  try { box.scrollIntoView({ behavior: "smooth", block: "nearest" }); } catch (e) { /* ignore */ }
  toast(title, true);
}

function showUploadResult(okItems, failedItems, canceledItems, invalidItems) {
  const box = document.getElementById("upResult");
  if (!box) return;
  const skipped = okItems.filter(item => item.result && item.result.skipped).length;
  const parts = [];
  parts.push("成功 " + (okItems.length - skipped) + " 个");
  if (skipped) parts.push("已存在跳过 " + skipped + " 个");
  if (failedItems.length) parts.push("失败 " + failedItems.length + " 个");
  if (canceledItems.length) parts.push("取消 " + canceledItems.length + " 个");
  if (invalidItems.length) parts.push("校验未通过 " + invalidItems.length + " 个");
  document.getElementById("upResultTitle").textContent = "上传结果汇总：" + parts.join(" · ");

  const names = [];
  okItems.forEach(item => {
    const project = item.result && item.result.project;
    const name = project || item.file.name;
    if (names.indexOf(name) < 0) names.push(name);
  });
  document.getElementById("upResultDetail").textContent = names.length
    ? "已上传项目：" + names.join("、")
    : "本次没有文件上传成功。";

  const hintNode = document.getElementById("upResultHint");
  let hint = "";
  if (failedItems.length || canceledItems.length) {
    hint = "失败或取消的文件仍保留在上方列表，可点击「重新上传」重试。";
  }
  if (okItems.length) {
    hint += (hint ? " " : "") + "点击「返回主界面」将自动刷新包列表，最新上传的包会置顶展示。";
  } else if (!hint) {
    hint = "请调整文件后重新上传。";
  }
  hintNode.textContent = hint;
  box.hidden = false;
  try { box.scrollIntoView({ behavior: "smooth", block: "nearest" }); } catch (e) { /* ignore */ }
}

function updateSummary() {
  const button = document.getElementById("upSubmit");
  const note = document.getElementById("upSummary");
  if (!button || !note) return;
  const state = getUploadState();
  const items = state.files;
  const uploading = state.uploading;

  const pending = items.filter(item => item.status === "pending").length;
  const retryable = items.filter(item => item.status === "err" && !item.clientInvalid).length;
  const canceled = items.filter(item => item.status === "canceled").length;
  const invalid = items.filter(item => item.clientInvalid).length;
  const ok = items.filter(item => item.status === "ok").length;
  const running = items.filter(item => item.status === "running").length;

  let text;
  if (!items.length) text = "尚未选择文件";
  else if (running) text = "正在上传，" + running + " 个文件进行中…";
  else {
    text = "共 " + items.length + " 个：待上传 " + pending + " · 已成功 " + ok;
    if (retryable) text += " · 上传失败 " + retryable;
    if (canceled) text += " · 已取消 " + canceled;
    if (invalid) text += " · 校验未通过 " + invalid + "（移除后重新选择）";
  }
  note.textContent = text;

  const hasError = items.some(item => item.status === "err" && !item.clientInvalid);
  button.textContent = uploading ? "上传中…" : ((hasError || canceled) ? "重新上传" : "开始上传");
  button.disabled = uploading || (pending + retryable + canceled === 0);
}

function renderQueue() {
  const box = document.getElementById("upQueue");
  const list = document.getElementById("upList");
  const drop = document.getElementById("upDrop");
  if (!box || !list) return;

  const state = getUploadState();
  const items = state.files;

  document.getElementById("upCount").textContent = items.length;
  box.hidden = !items.length;
  list.innerHTML = items.map(item => renderFileRow(item, state.uploading)).join("");
  if (drop) drop.classList.toggle("busy", state.uploading);
  updateSummary();
}

function addFiles(files) {
  if (!files || !files.length) return;
  hideUpResult();
  const maxUpload = getMaxUpload();
  const prepared = [];
  const bad = [];

  files.forEach(file => {
    const error = validateUploadFile(file, maxUpload);
    prepared.push({
      file,
      status: error ? "err" : "pending",
      err: error,
      clientInvalid: !!error
    });
    if (error) bad.push({ name: file.name, err: error });
  });

  uploadActions.add(prepared);

  if (bad.length) {
    showUploadError("选择的部分文件未通过校验",
      bad.map(item => item.name + "：" + item.err).join("；"),
      "未通过校验的文件已保留在列表中，可单独移除并重新选择；其余合法文件不受影响，可直接上传。");
  } else {
    dismissUpAlert();
  }
}

function cancelFile(id) {
  const item = findUploadItem(id);
  if (!item) return;
  if (item.status === "running") {
    uploadActions.update(id, { canceled: true });
    const handle = handles.get(id);
    if (handle) handle.abort();
    return;
  }
  if (item.status === "pending" && getUploadState().uploading) {
    uploadActions.update(id, { canceled: true, status: "canceled" });
  }
}

function removeFile(id) {
  if (getUploadState().uploading) return;
  uploadActions.remove(id);
  if (!getUploadState().files.length) dismissUpAlert();
}

function clearQueue() {
  if (getUploadState().uploading) return;
  uploadActions.clear();
  hideUpResult();
  dismissUpAlert();
}

async function uploadOne(id) {
  const item = findUploadItem(id);
  if (!item) return;
  if (item.canceled) {
    uploadActions.update(id, { status: "canceled", err: "" });
    return;
  }

  uploadActions.update(id, { status: "running", pct: 0, err: "", failure: null, message: "" });
  let lastRender = 0;

  const handle = uploadFile(item.file, {
    onProgress: pct => {
      const now = Date.now();
      if (now - lastRender < 120) return;
      lastRender = now;
      const latest = findUploadItem(id);
      if (!latest || latest.canceled) return;
      uploadActions.update(id, { status: "running", pct: pct });
    }
  });
  handles.set(id, handle);

  try {
    const body = await handle.promise;
    uploadActions.update(id, {
      status: "ok",
      err: "",
      pct: 100,
      message: body.skipped ? "已存在，跳过" : "已上传",
      result: body
    });
  } catch (error) {
    const latest = findUploadItem(id);
    if (error.canceled || (latest && latest.canceled)) {
      uploadActions.update(id, { status: "canceled", err: "", failure: null, pct: 0 });
    } else if (error.network || !error.status) {
      const info = {
        title: "网络连接中断",
        detail: "无法与服务器建立连接，文件未完成上传。",
        hint: "请检查网络 / VPN 与服务器状态，然后点击「重新上传」；已选文件会保留，无需重新选择。"
      };
      uploadActions.update(id, { status: "err", err: info.detail, failure: info });
    } else {
      const info = explainUploadFailure(error.status, error.body, getMaxUpload());
      uploadActions.update(id, { status: "err", err: info.detail, failure: info });
    }
  } finally {
    handles.delete(id);
  }
}

async function startUpload() {
  if (getUploadState().uploading) return;
  if (!await ensureAuth()) {
    showLogin("请先登录后再上传");
    return;
  }

  const targets = getUploadState().files.filter(item =>
    item.status === "pending" || item.status === "canceled" ||
    (item.status === "err" && !item.clientInvalid));

  if (!targets.length) {
    showUploadError("没有可上传的文件",
      "队列中没有待上传的文件。",
      getUploadState().files.length
        ? "请移除校验未通过的文件，重新选择正确的包文件后再上传。"
        : "请先拖拽或选择 .whl / .tar.gz / .zip 文件。");
    return;
  }

  hideUpResult();
  dismissUpAlert();
  uploadActions.setUploading(true);
  uploadActions.resetForRetry(targets.map(item => item.id));
  renderQueue();

  await Promise.all(targets.map(item => uploadOne(item.id)));

  uploadActions.setUploading(false);
  renderQueue();

  const items = getUploadState().files;
  const okItems = items.filter(item => item.status === "ok");
  const failedItems = items.filter(item => item.status === "err" && !item.clientInvalid);
  const canceledItems = items.filter(item => item.status === "canceled");
  const invalidItems = items.filter(item => item.clientInvalid);

  if (okItems.length) {
    const projects = [];
    okItems.forEach(item => {
      const project = item.result && item.result.project;
      if (project && projects.indexOf(project) < 0) projects.push(project);
    });
    saveUploadResult(projects, okItems.length);
  }

  showUploadResult(okItems, failedItems, canceledItems, invalidItems);

  if (failedItems.length) {
    const first = failedItems[0];
    showUploadError(first.failure.title,
      first.file.name + "：" + first.failure.detail +
        (failedItems.length > 1 ? "；另有 " + (failedItems.length - 1) + " 个文件失败，详见列表状态。" : ""),
      first.failure.hint);
  } else if (okItems.length) {
    const skipped = okItems.filter(item => item.result && item.result.skipped).length;
    toast("上传完成：成功 " + (okItems.length - skipped) + " 个" +
      (skipped ? "，已存在跳过 " + skipped + " 个" : "") +
      (canceledItems.length ? "，取消 " + canceledItems.length + " 个" : ""));
  }
}

/**
 * @param {HTMLElement} container
 */
export function mount(container) {
  setView("upload");

  container.innerHTML = `
  <section class="view" id="viewUpload">
    <div class="page-head">
      <div class="page-title">
        <h2>发布分发包</h2>
      </div>
    </div>

    <div class="alert" id="upAlert" role="alert" aria-live="assertive" hidden>
      <span class="alert-ico" aria-hidden="true">!</span>
      <div class="alert-main">
        <b id="upAlertTitle"></b>
        <p id="upAlertDetail"></p>
        <p class="alert-hint" id="upAlertHint"></p>
      </div>
      <button class="icon-btn btn-close-sm" type="button" data-action="dismiss-alert" aria-label="关闭提示">
        ${CLOSE_ICON}
      </button>
    </div>

    <div class="alert alert-ok" id="upResult" role="status" aria-live="polite" hidden>
      <span class="alert-ico alert-ico-ok" aria-hidden="true">✓</span>
      <div class="alert-main">
        <b id="upResultTitle"></b>
        <p id="upResultDetail"></p>
        <p class="alert-hint" id="upResultHint"></p>
      </div>
      <button class="btn btn-sm btn-primary" type="button" data-action="go-list">返回主界面</button>
    </div>

    <div class="up-grid">
      <div class="up-card">
        <div class="up-drop" id="upDrop" role="button" tabindex="0" aria-label="选择要上传的包文件">
          ${CLOUD_ICON}
          <div class="drop-title">拖拽文件到这里，或点击选择</div>
          <input type="file" id="upFile" multiple accept=".whl,.tar.gz,.zip" hidden>
        </div>

        <div class="up-queue" id="upQueue" hidden>
          <div class="up-queue-head">
            <span>已选文件 <b id="upCount">0</b></span>
            <button class="btn btn-sm" id="clearQueueBtn" type="button">清空全部</button>
          </div>
          <div class="up-list" id="upList"></div>
        </div>

        <div class="up-foot">
          <button class="btn btn-primary" id="upSubmit" type="button">开始上传</button>
          <span class="up-note" id="upSummary">尚未选择文件</span>
        </div>
      </div>
    </div>
  </section>`;

  const drop = container.querySelector("#upDrop");
  const input = container.querySelector("#upFile");
  const list = container.querySelector("#upList");

  drop.addEventListener("click", event => {
    if (event.target !== input) input.click();
  });
  input.addEventListener("click", event => event.stopPropagation());
  input.addEventListener("change", () => {
    addFiles(Array.from(input.files));
    input.value = "";
  });
  ["dragenter", "dragover"].forEach(type => drop.addEventListener(type, event => {
    event.preventDefault();
    drop.classList.add("hot");
  }));
  ["dragleave", "drop"].forEach(type => drop.addEventListener(type, event => {
    event.preventDefault();
    drop.classList.remove("hot");
  }));
  drop.addEventListener("drop", event => addFiles(Array.from(event.dataTransfer.files)));
  drop.addEventListener("keydown", event => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      input.click();
    }
  });

  const pageCleanup = delegate(container, "[data-action]", "click", (event, match) => {
    const action = match.dataset.action;
    if (action === "go-list") {
      scrollToTop("smooth");
      navigate("/");
    } else if (action === "dismiss-alert") {
      dismissUpAlert();
    }
  });

  const listCleanup = delegate(list, "[data-action]", "click", (event, match) => {
    event.preventDefault();
    const action = match.dataset.action;
    const id = Number(match.dataset.id);
    if (action === "cancel") cancelFile(id);
    else if (action === "remove") removeFile(id);
  });

  const clearButton = container.querySelector("#clearQueueBtn");
  clearButton.addEventListener("click", clearQueue);
  container.querySelector("#upSubmit").addEventListener("click", startUpload);

  lastUpload = getUploadState();
  const unsubscribe = uiStore.subscribe(state => {
    if (state.upload === lastUpload) return;
    lastUpload = state.upload;
    renderQueue();
  });

  cleanups.push(pageCleanup, listCleanup, unsubscribe);

  renderQueue();
}

export function unmount() {
  cleanups.forEach(fn => {
    try { fn(); } catch (e) { /* ignore */ }
  });
  cleanups = [];
}
