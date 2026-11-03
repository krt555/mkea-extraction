/* 化学文献信息提取 — front-end.
 * No framework on purpose: one file, no build step, easy to demo and to read.
 */

const S = {
  status: null,
  fields: [],
  papers: [],
  paper: null,          // full payload of the paper open in the review view
  recordIndex: 0,
  page: 1,
  pageCache: new Map(), // `${paperFk}:${page}` -> text
  dirty: new Map(),     // field key -> new value
  activeField: null,
  pollers: new Map(),
  compareRows: [],      // all records available for comparison
  compareSel: new Set(),// selected record_id set
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try { detail = (await response.json()).error || detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

function toast(message, kind = "") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  $("toastWrap").append(node);
  setTimeout(() => node.remove(), 4200);
}

/* ------------------------------------------------------------------ */
/* theme                                                               */
/* ------------------------------------------------------------------ */

const THEME_KEY = "cheminfo-theme";

function applyTheme(name) {
  document.documentElement.dataset.theme = name;
  const dark = name === "dark";
  $("themeIcon").textContent = dark ? "☾" : "☀";
  $("themeLabel").textContent = dark ? "夜间模式" : "白天模式";
  try { localStorage.setItem(THEME_KEY, name); } catch (_) {}
}

$("themeToggle").addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

(function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem(THEME_KEY); } catch (_) {}
  if (!saved) {
    const prefersLight = window.matchMedia
      && window.matchMedia("(prefers-color-scheme: light)").matches;
    saved = prefersLight ? "light" : "dark";
  }
  applyTheme(saved);
})();

/* ------------------------------------------------------------------ */
/* navigation                                                          */
/* ------------------------------------------------------------------ */

const VIEW_META = {
  library: ["文献库", "上传论文、创建抽取任务、查看进度与记录数量"],
  review: ["提取结果", "左右联动：点击字段跳到证据原文，可直接修改并保存"],
  compare: ["样品对比", "跨论文勾选多条记录，生成条件矩阵并高亮差异项"],
  search: ["跨论文检索", "按字段值与数值区间筛选，横向比较不同论文的合成条件"],
  jobs: ["任务与日志", "抽取任务状态、使用的引擎与模型、运行提示"],
};

function showView(name) {
  document.querySelectorAll(".nav-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) =>
    v.classList.toggle("active", v.id === `view-${name}`));
  const [title, sub] = VIEW_META[name];
  $("viewTitle").textContent = title;
  $("viewSub").textContent = sub;
  if (name === "jobs") loadJobs();
  if (name === "search") runSearch();
  if (name === "compare") loadCompare();
}

$("nav").addEventListener("click", (event) => {
  const button = event.target.closest(".nav-item");
  if (button) showView(button.dataset.view);
});

/* ------------------------------------------------------------------ */
/* status                                                              */
/* ------------------------------------------------------------------ */

async function loadStatus() {
  S.status = await api("/api/status");
  S.fields = S.status.fields;

  const llm = S.status.llm;
  $("engineDot").className = `dot ${llm.available ? "on" : "off"}`;
  $("engineName").textContent = llm.available ? "LLM Agent 就绪" : "规则引擎模式";
  $("engineHint").textContent = llm.available
    ? `${llm.model} · anchored 模式（全文候选 + 工具审核）`
    : "未配置 API Key，使用确定性规则引擎抽取，功能完整可用";

  const c = S.status.counts;
  $("statGrid").innerHTML = `
    <div class="stat"><b>${c.papers}</b><span>论文</span></div>
    <div class="stat"><b>${c.records}</b><span>记录</span></div>
    <div class="stat"><b>${c.reviewed}</b><span>已复核</span></div>
    <div class="stat"><b>${c.running}</b><span>运行中</span></div>`;

  const fieldSelect = $("searchField");
  if (fieldSelect.options.length <= 1) {
    S.fields.forEach((f) => fieldSelect.add(new Option(f.label, f.key)));
  }
}

/* ------------------------------------------------------------------ */
/* library: grouped by extraction state                                */
/* ------------------------------------------------------------------ */

/** 把任务状态归一成四种库内分组。 */
function groupOf(paper) {
  const job = paper.latest_job;
  if (job && (job.status === "running" || job.status === "queued")) return "running";
  if (paper.n_records > 0) return "done";
  if (job && job.status === "failed") return "failed";
  return "pending";
}

const STAGE_SEQUENCE = ["解析 PDF", "抽取", "后处理", "完成"];

/** 把后端阶段文字映射到固定的 4 步，便于显示“第几步”。 */
function stageStep(stage) {
  if (!stage) return 0;
  if (stage.includes("排队")) return 0;
  if (stage.includes("解析")) return 1;
  if (stage.includes("LLM") || stage.includes("规则") || stage.includes("Agent")) return 2;
  if (stage.includes("后处理") || stage.includes("证据")) return 3;
  if (stage.includes("完成")) return 4;
  return 1;
}

/** 渲染 4 步进度条的文字部分，已完成的步骤用对勾替代序号。 */
function stepsHtml(step) {
  return STAGE_SEQUENCE.map((label, index) => {
    const position = index + 1;
    const cls = step > position ? "done" : step === position ? "now" : "";
    const marker = step > position ? "✓" : `${position}.`;
    return `<span class="${cls}">${marker} ${esc(label)}</span>`;
  }).join("");
}

function runningCardHtml(paper) {
  const job = paper.latest_job;
  const pct = Math.max(3, Math.min(100, job.progress || 0));
  const queued = job.status === "queued";
  return `
  <div class="running-card" data-id="${paper.id}">
    <div class="running-top">
      <span class="running-key">${esc(paper.paper_key)}</span>
      <span class="running-title">${esc(paper.title)}</span>
      <span class="running-pct">${pct}%</span>
    </div>
    <div class="running-bar"><i style="width:${pct}%"></i></div>
    <div class="running-foot">
      <span class="running-stage">
        <i class="spin"></i>${esc(queued ? "排队中，等待空闲线程" : job.stage || "处理中")}
      </span>
      <span class="running-steps">${stepsHtml(stageStep(job.stage))}</span>
    </div>
    <div class="running-elapsed" data-started="${esc(job.created_at || "")}"></div>
  </div>`;
}

function paperCardHtml(paper, group) {
  const job = paper.latest_job;
  return `
  <div class="paper-card" data-id="${paper.id}">
    <div class="paper-top">
      <div>
        <div class="paper-key">${esc(paper.paper_key)}</div>
        <h3 class="paper-title">${esc(paper.title)}</h3>
      </div>
      ${statusBadge(job, paper)}
    </div>
    <div class="paper-dims">
      <span>${paper.pages} 页</span><span>${paper.n_tables} 表</span>
      <span>${(paper.chars / 1000).toFixed(1)}k 字符</span>
      ${paper.n_records ? `<span>${paper.n_reviewed}/${paper.n_records} 已复核</span>` : ""}
    </div>
    ${group === "failed" && job ? `<div class="paper-err">${esc(job.message || "未知错误")}</div>` : ""}
    <div class="paper-actions">
      <select class="select sm" data-mode="${paper.id}" title="抽取模式">
        <option value="anchored">anchored</option>
        <option value="oneshot">oneshot</option>
        <option value="agent">agent</option>
        <option value="rules">rules</option>
      </select>
      <button class="btn sm" data-act="run">${group === "done" ? "重抽" : "抽取"}</button>
      <button class="btn sm" data-act="open" ${paper.n_records ? "" : "disabled"}>结果</button>
      <button class="btn sm danger" data-act="del">删除</button>
    </div>
  </div>`;
}

function statusBadge(job, paper) {
  if (paper && paper.n_records > 0 && job && job.status === "succeeded") {
    return `<span class="badge ok">${paper.n_records} 条记录</span>`;
  }
  if (!job) return '<span class="badge idle">未抽取</span>';
  const map = {
    queued: ["run", "排队中"],
    running: ["run", `${job.progress || 0}%`],
    succeeded: ["ok", `${job.n_records} 条记录`],
    failed: ["err", "失败"],
  };
  const [cls, text] = map[job.status] || ["idle", job.status];
  return `<span class="badge ${cls}">${esc(text)}</span>`;
}

async function loadPapers() {
  const data = await api("/api/papers");
  S.papers = data.papers;
  renderLibrary();
  renderPaperSwitch();
  renderAskScope();
  S.papers.forEach((p) => {
    const job = p.latest_job;
    if (job && (job.status === "running" || job.status === "queued")) pollJob(job.id);
  });
}

function renderLibrary() {
  const buckets = { running: [], done: [], pending: [], failed: [] };
  S.papers.forEach((p) => buckets[groupOf(p)].push(p));

  $("libraryEmpty").hidden = S.papers.length > 0;

  $("groupRunning").hidden = !buckets.running.length;
  $("runningCount").textContent = buckets.running.length;
  $("runningList").innerHTML = buckets.running.map(runningCardHtml).join("");

  const fill = (group, groupId, gridId, countId) => {
    const items = buckets[group];
    $(groupId).hidden = !items.length;
    $(countId).textContent = items.length;
    $(gridId).innerHTML = items.map((p) => paperCardHtml(p, group)).join("");
  };
  fill("done", "groupDone", "doneGrid", "doneCount");
  fill("pending", "groupPending", "pendingGrid", "pendingCount");
  fill("failed", "groupFailed", "failedGrid", "failedCount");
}

async function handleCardAction(event) {
  const card = event.target.closest(".paper-card");
  const button = event.target.closest("button[data-act]");
  if (!card || !button) return;
  const id = Number(card.dataset.id);

  if (button.dataset.act === "run") {
    const mode = card.querySelector(`[data-mode="${id}"]`).value;
    try {
      const job = await api("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paper_fk: id, mode }),
      });
      toast(`任务已创建（${mode}），进度显示在“正在抽取”`);
      await loadPapers();
      pollJob(job.id);
    } catch (error) { toast(error.message, "err"); }
  }

  if (button.dataset.act === "open") {
    showView("review");
    await openPaper(id);
  }

  if (button.dataset.act === "del") {
    const paper = S.papers.find((p) => p.id === id);
    if (!confirm(`删除「${paper ? paper.paper_key : id}」及其全部抽取记录？`)) return;
    await api(`/api/papers/${id}`, { method: "DELETE" });
    if (S.paper && S.paper.id === id) { S.paper = null; renderFields(); }
    toast("已删除");
    await loadPapers();
    await loadStatus();
  }
}

["doneGrid", "pendingGrid", "failedGrid"].forEach((id) =>
  $(id).addEventListener("click", handleCardAction));

/** 显示已用时长；LLM 阶段额外提示耗时属正常。 */
function renderElapsed(node, startedAt, slow) {
  if (!node) return;
  const started = Date.parse(startedAt || "");
  if (!started) { node.textContent = ""; return; }
  const seconds = Math.max(0, Math.round((Date.now() - started) / 1000));
  const text = seconds < 60
    ? `${seconds} 秒`
    : `${Math.floor(seconds / 60)} 分 ${String(seconds % 60).padStart(2, "0")} 秒`;
  node.textContent = slow
    ? `已用 ${text} · LLM 单篇推理通常需要 1–3 分钟，请耐心等待`
    : `已用 ${text}`;
}

function pollJob(jobId) {
  if (S.pollers.has(jobId)) return;
  const timer = setInterval(async () => {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      const card = document.querySelector(`.running-card[data-id="${job.paper_fk}"]`);
      if (card && (job.status === "running" || job.status === "queued")) {
        // 原地更新进度，避免整页重绘导致进度条跳动
        const pct = Math.max(3, Math.min(100, job.progress || 0));
        card.querySelector(".running-pct").textContent = `${pct}%`;
        const bar = card.querySelector(".running-bar > i");
        bar.style.width = `${pct}%`;
        const stage = card.querySelector(".running-stage");
        stage.innerHTML = `<i class="spin"></i>${esc(job.stage || "处理中")}`;
        card.querySelector(".running-steps").innerHTML = stepsHtml(stageStep(job.stage));
        // LLM 调用是个长耗时黑盒，百分比会长时间停住；
        // 用流动条纹 + 已用时长说明“仍在运行”，避免看起来像卡死。
        const slow = /LLM|Agent|规则/.test(job.stage || "");
        bar.classList.toggle("indeterminate", slow);
        renderElapsed(card.querySelector(".running-elapsed"), job.started_at || job.created_at, slow);
      }
      if (job.status === "succeeded" || job.status === "failed") {
        clearInterval(timer);
        S.pollers.delete(jobId);
        if (job.status === "succeeded") {
          toast(`${job.paper_key || "论文"} 抽取完成：${job.n_records} 条记录（${job.engine}）`);
          (job.issues || []).forEach((issue) => toast(issue, "warn"));
        } else {
          toast(`抽取失败：${job.message}`, "err");
        }
        await loadPapers();
        await loadStatus();
        if (S.paper && S.paper.id === job.paper_fk) await openPaper(job.paper_fk);
      }
    } catch (error) {
      clearInterval(timer);
      S.pollers.delete(jobId);
    }
  }, 1000);
  S.pollers.set(jobId, timer);
}

/* ------------------------------------------------------------------ */
/* upload                                                              */
/* ------------------------------------------------------------------ */

async function uploadFiles(files) {
  for (const file of files) {
    const form = new FormData();
    form.append("file", file);
    try {
      const paper = await api("/api/papers", { method: "POST", body: form });
      toast(`${paper.paper_key} 已解析：${paper.pages} 页，可在“待抽取”中启动任务`);
    } catch (error) {
      toast(`${file.name}：${error.message}`, "err");
    }
  }
  await loadPapers();
  await loadStatus();
}

$("fileInput").addEventListener("change", (event) => {
  if (event.target.files.length) uploadFiles([...event.target.files]);
  event.target.value = "";
});

const zone = $("uploadZone");
["dragenter", "dragover"].forEach((type) =>
  zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.add("hot"); }));
["dragleave", "drop"].forEach((type) =>
  zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.remove("hot"); }));
zone.addEventListener("drop", (event) => {
  const files = [...event.dataTransfer.files].filter((f) => f.name.toLowerCase().endsWith(".pdf"));
  if (files.length) uploadFiles(files); else toast("请拖入 PDF 文件", "warn");
});

$("seedBtn").addEventListener("click", async () => {
  try {
    await api("/api/demo/seed", { method: "POST" });
    toast("演示数据已载入，抽取进度见“正在抽取”");
    await loadPapers(); await loadStatus();
  } catch (error) { toast(error.message, "err"); }
});

/* ------------------------------------------------------------------ */
/* review (提取结果)                                                   */
/* ------------------------------------------------------------------ */

/** 卡片式论文切换器：一眼看到有哪些论文、各有几条记录、当前在看哪篇。 */
function renderPaperSwitch() {
  const usable = S.papers.filter((p) => p.n_records > 0);
  const box = $("paperSwitch");
  if (!usable.length) {
    box.innerHTML = '<span class="group-note">还没有已完成抽取的论文。</span>';
    return;
  }
  box.innerHTML = usable.map((p) => `
    <button class="switch-card ${S.paper && S.paper.id === p.id ? "active" : ""}"
            data-id="${p.id}" title="${esc(p.title)}">
      <span class="switch-top">
        <span class="switch-key">${esc(p.paper_key)}</span>
        <span class="switch-count">${p.n_reviewed}/${p.n_records}</span>
      </span>
      <span class="switch-title">${esc(p.title)}</span>
    </button>`).join("");
}

$("paperSwitch").addEventListener("click", async (event) => {
  const card = event.target.closest(".switch-card");
  if (!card) return;
  const id = Number(card.dataset.id);
  if (S.paper && S.paper.id === id) return;
  if (S.dirty.size && !confirm("当前记录有未保存的修改，切换会丢失。继续？")) return;
  await openPaper(id);
});

function renderAskScope() {
  const scope = $("askScope");
  const current = scope.value;
  scope.innerHTML = '<option value="">全部论文</option>' + S.papers
    .map((p) => `<option value="${p.id}">${esc(p.paper_key)} · ${esc(p.title)}</option>`)
    .join("");
  if (current && S.papers.some((p) => String(p.id) === current)) scope.value = current;
}

async function openPaper(paperFk) {
  S.paper = await api(`/api/papers/${paperFk}`);
  S.recordIndex = 0;
  S.dirty.clear();
  S.activeField = null;

  const job = S.paper.latest_job;
  $("reviewMeta").innerHTML = [
    `${S.paper.pages} 页`,
    `${S.paper.n_records} 条记录`,
    `${S.paper.n_reviewed} 条已复核`,
    job ? `引擎 ${esc(job.engine || "—")}` : "未抽取",
    job && job.model ? `模型 ${esc(job.model)}` : "",
  ].filter(Boolean).map((t) => `<span>${t}</span>`).join("");

  renderPaperSwitch();
  renderRecordTabs();
  renderFields();
  await showPage(firstEvidencePage() || 1);
}

function currentRecord() {
  return S.paper && S.paper.records ? S.paper.records[S.recordIndex] : null;
}

function firstEvidencePage() {
  const record = currentRecord();
  if (!record) return null;
  for (const key of S.fields.map((f) => f.key)) {
    const items = record.evidence[key];
    if (items && items.length) return items[0].page;
  }
  return null;
}

function renderRecordTabs() {
  const records = (S.paper && S.paper.records) || [];
  $("recordTabs").innerHTML = records.map((r, i) =>
    `<button class="record-tab ${i === S.recordIndex ? "active" : ""} ${r.reviewed ? "done" : ""}"
             data-i="${i}">样品 ${r.record_index}</button>`).join("");
}

$("recordTabs").addEventListener("click", (event) => {
  const tab = event.target.closest(".record-tab");
  if (!tab) return;
  if (S.dirty.size && !confirm("当前记录有未保存的修改，切换会丢失。继续？")) return;
  S.recordIndex = Number(tab.dataset.i);
  S.dirty.clear();
  renderRecordTabs();
  renderFields();
  showPage(firstEvidencePage() || S.page);
});

function renderFields() {
  const record = currentRecord();
  const list = $("fieldList");
  if (!record) {
    list.innerHTML = '<div class="placeholder">这篇论文还没有抽取记录，请先在文献库创建抽取任务。</div>';
    $("reviewFoot").hidden = true;
    return;
  }
  $("reviewFoot").hidden = false;
  $("reviewedFlag").checked = record.reviewed;

  list.innerHTML = S.fields.map((f) => {
    const value = record.fields[f.key];
    const items = record.evidence[f.key] || [];
    const conf = record.confidence[f.key];
    return `
    <div class="field-row ${value ? "" : "empty-val"}" data-key="${f.key}">
      <label class="field-label" title="${esc(f.key)}">${esc(f.label)}</label>
      <input class="field-input" data-key="${f.key}"
             value="${esc(value ?? "")}" placeholder="null（未报道）" />
      <div class="field-side">
        ${conf !== undefined ? `<span class="conf">${(conf * 100).toFixed(0)}%</span>` : ""}
        ${items.map((item, index) => `<span class="page-pill" data-page="${item.page}"
             data-key="${f.key}" data-idx="${index}">p.${item.page}</span>`).join("")}
      </div>
    </div>`;
  }).join("");
}

$("fieldList").addEventListener("click", (event) => {
  const pill = event.target.closest(".page-pill");
  const row = event.target.closest(".field-row");
  if (pill) {
    event.stopPropagation();
    focusField(pill.dataset.key, Number(pill.dataset.page), Number(pill.dataset.idx));
  } else if (row) {
    focusField(row.dataset.key);
  }
});

$("fieldList").addEventListener("input", (event) => {
  const input = event.target.closest(".field-input");
  if (!input) return;
  const record = currentRecord();
  const original = record.fields[input.dataset.key] ?? "";
  const changed = input.value !== String(original);
  input.classList.toggle("dirty", changed);
  if (changed) S.dirty.set(input.dataset.key, input.value);
  else S.dirty.delete(input.dataset.key);
});

/** Field -> original text: the core bidirectional-locating interaction. */
async function focusField(key, page = null, index = 0) {
  const record = currentRecord();
  if (!record) return;
  S.activeField = key;
  document.querySelectorAll(".field-row").forEach((row) =>
    row.classList.toggle("active", row.dataset.key === key));

  const items = record.evidence[key] || [];
  const item = items[index] || items[0];
  if (!item) {
    $("evidenceBox").hidden = true;
    if (page) await showPage(page);
    else toast("该字段没有证据引文（可能为 null 或人工填写）", "warn");
    return;
  }
  $("evidenceBox").hidden = false;
  $("evidenceQuote").textContent = item.quote;
  await showPage(page || item.page, item.quote);
}

async function showPage(page, highlight = null) {
  if (!S.paper) return;
  const total = S.paper.pages;
  S.page = Math.max(1, Math.min(total, page));
  $("pageLabel").textContent = `${S.page} / ${total}`;

  const cacheKey = `${S.paper.id}:${S.page}`;
  let text = S.pageCache.get(cacheKey);
  if (text === undefined) {
    try {
      text = (await api(`/api/papers/${S.paper.id}/pages/${S.page}`)).text;
    } catch (error) {
      text = "";
    }
    S.pageCache.set(cacheKey, text);
  }

  const box = $("pageText");
  if (!text.trim()) {
    box.innerHTML = '<div class="placeholder">该页没有可用文本层（可能是纯图片页）。</div>';
    return;
  }
  box.innerHTML = highlight ? highlightQuote(text, highlight) : esc(text);
  const mark = box.querySelector("mark");
  if (mark) mark.scrollIntoView({ block: "center", behavior: "smooth" });
  else box.scrollTop = 0;
}

/** Whitespace-tolerant quote highlighting: PDF text layers break lines mid-sentence. */
function highlightQuote(text, quote) {
  // Retrieval snippets are elided with "…"; strip the markers and use the
  // longest clean fragment as the needle.
  const needle = String(quote || "")
    .split("…")
    .map((part) => part.trim())
    .sort((a, b) => b.length - a.length)[0]
    ?.replace(/\s+/g, " ") || "";
  if (!needle) return esc(text);

  const build = (probe) => {
    const pattern = probe.split(" ").map((word) =>
      word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+");
    try { return new RegExp(pattern, "i"); } catch (_) { return null; }
  };

  const words = needle.split(" ");
  const sizes = [...new Set([
    words.length,
    Math.ceil(words.length * 0.6),
    Math.ceil(words.length * 0.3),
    Math.min(8, words.length),
  ])].filter((n) => n >= 3);
  for (const size of sizes) {
    const regex = build(words.slice(0, size).join(" "));
    if (!regex) continue;
    const match = regex.exec(text);
    if (match) {
      return esc(text.slice(0, match.index)) +
             "<mark>" + esc(match[0]) + "</mark>" +
             esc(text.slice(match.index + match[0].length));
    }
  }
  return esc(text);
}

$("pagePrev").addEventListener("click", () => showPage(S.page - 1));
$("pageNext").addEventListener("click", () => showPage(S.page + 1));

$("saveRecord").addEventListener("click", async () => {
  const record = currentRecord();
  if (!record) return;
  const fields = {};
  S.dirty.forEach((value, key) => { fields[key] = value; });
  try {
    const updated = await api(`/api/records/${record.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields, reviewed: $("reviewedFlag").checked }),
    });
    S.paper.records[S.recordIndex] = { ...updated, paper_key: S.paper.paper_key };
    S.dirty.clear();
    renderRecordTabs();
    renderFields();
    toast(updated.n_changes ? `已保存 ${updated.n_changes} 处修改` : "已保存");
    await loadPapers();
    await loadStatus();
  } catch (error) { toast(error.message, "err"); }
});

$("exportJsonl").addEventListener("click", () =>
  window.open(S.paper ? `/api/export?fmt=jsonl&paper_fk=${S.paper.id}` : "/api/export?fmt=jsonl"));
$("exportCsv").addEventListener("click", () =>
  window.open(S.paper ? `/api/export?fmt=csv&paper_fk=${S.paper.id}` : "/api/export?fmt=csv"));

/* ------------------------------------------------------------------ */
/* compare (样品对比)                                                  */
/* ------------------------------------------------------------------ */

/** 归一化后再比较：避免大小写、空白、全角差异造成假“差异项”。 */
function normValue(value) {
  if (value === null || value === undefined || value === "") return "";
  return String(value).trim().toLowerCase().replace(/\s+/g, " ");
}

async function loadCompare() {
  let data;
  try { data = await api("/api/search"); }
  catch (error) { toast(error.message, "err"); return; }

  S.compareRows = data.results;
  // 默认选中前若干条，演示时打开即有内容
  if (!S.compareSel.size) {
    S.compareRows.slice(0, Math.min(4, S.compareRows.length))
      .forEach((r) => S.compareSel.add(r.record_id));
  } else {
    const valid = new Set(S.compareRows.map((r) => r.record_id));
    [...S.compareSel].forEach((id) => { if (!valid.has(id)) S.compareSel.delete(id); });
  }
  renderPickList();
  renderCompare();
}

function renderPickList() {
  const box = $("pickList");
  if (!S.compareRows.length) {
    box.innerHTML = '<div class="placeholder">还没有抽取记录。</div>';
    return;
  }
  // 按论文分组，跨论文选取时结构清楚
  const groups = new Map();
  S.compareRows.forEach((r) => {
    if (!groups.has(r.paper_key)) groups.set(r.paper_key, { title: r.paper_title, items: [] });
    groups.get(r.paper_key).items.push(r);
  });

  box.innerHTML = [...groups.entries()].map(([key, group]) => `
    <div class="pick-group">
      <div class="pick-group-head">${esc(key)}</div>
      <div class="pick-group-title" title="${esc(group.title)}">${esc(group.title)}</div>
      ${group.items.map((r) => {
        const hint = r.fields.mesostructure || r.fields.surfactant || "—";
        return `
        <label class="pick-item ${S.compareSel.has(r.record_id) ? "on" : ""}"
               data-rid="${esc(r.record_id)}">
          <input type="checkbox" ${S.compareSel.has(r.record_id) ? "checked" : ""} />
          <span class="pick-name">r${String(r.record_index).padStart(2, "0")}</span>
          <span class="pick-hint" title="${esc(hint)}">${esc(hint)}</span>
        </label>`;
      }).join("")}
    </div>`).join("");
}

$("pickList").addEventListener("change", (event) => {
  const item = event.target.closest(".pick-item");
  if (!item) return;
  const rid = item.dataset.rid;
  if (event.target.checked) S.compareSel.add(rid); else S.compareSel.delete(rid);
  item.classList.toggle("on", event.target.checked);
  renderCompare();
});

$("pickAll").addEventListener("click", () => {
  S.compareRows.forEach((r) => S.compareSel.add(r.record_id));
  renderPickList(); renderCompare();
});
$("pickNone").addEventListener("click", () => {
  S.compareSel.clear();
  renderPickList(); renderCompare();
});
$("diffOnly").addEventListener("change", renderCompare);

function selectedRecords() {
  return S.compareRows.filter((r) => S.compareSel.has(r.record_id));
}

/** 条件矩阵：行=字段，列=样品；逐行判断是否所有样品取值一致。 */
function renderCompare() {
  const picked = selectedRecords();
  const body = $("compareBody");
  const legend = $("compareLegend");

  if (picked.length < 2) {
    legend.hidden = true;
    body.innerHTML = '<div class="placeholder">在左侧勾选 2 条以上记录，这里会生成条件矩阵并高亮差异项。</div>';
    return;
  }
  legend.hidden = false;

  const rows = S.fields.map((f) => {
    const values = picked.map((r) => r.fields[f.key]);
    const distinct = new Set(values.map(normValue));
    // 全为空不算差异，也不算有效共同条件
    const allEmpty = [...distinct].every((v) => v === "");
    return { field: f, values, diff: distinct.size > 1, allEmpty, distinct: distinct.size };
  });

  const diffRows = rows.filter((r) => r.diff);
  const sameRows = rows.filter((r) => !r.diff && !r.allEmpty);
  const shown = $("diffOnly").checked ? diffRows : rows;

  const header = `
    <thead>
      <tr>
        <th class="field-col">字段</th>
        ${picked.map((r) => `
          <th>
            <span class="ch-key">${esc(r.paper_key)} · r${String(r.record_index).padStart(2, "0")}</span>
            <span class="ch-title" title="${esc(r.paper_title)}">${esc(r.paper_title)}</span>
          </th>`).join("")}
      </tr>
    </thead>`;

  const bodyRows = shown.map((row) => `
    <tr class="${row.diff ? "row-diff" : ""}">
      <th class="field-col">
        ${esc(row.field.label)}${row.diff ? `<span class="diff-tag">${row.distinct} 种</span>` : ""}
      </th>
      ${row.values.map((value) => `
        <td class="${row.diff ? "cell-diff" : ""}">
          ${value ? esc(value) : '<span class="null">未报道</span>'}
        </td>`).join("")}
    </tr>`).join("");

  const summary = `
    <div class="compare-summary">
      已选 ${picked.length} 条样品 ·
      <b>${diffRows.length} 个差异项</b>（${diffRows.map((r) => esc(r.field.label)).join("、") || "无"}） ·
      <em>${sameRows.length} 个共同条件</em>
    </div>`;

  body.innerHTML = summary +
    `<table class="compare-table">${header}<tbody>${bodyRows}</tbody></table>`;
}

$("exportCompare").addEventListener("click", () => {
  const picked = selectedRecords();
  if (picked.length < 2) { toast("请先勾选至少 2 条记录", "warn"); return; }

  const header = ["字段", ...picked.map((r) => `${r.paper_key}_r${String(r.record_index).padStart(2, "0")}`), "是否差异项"];
  const lines = [header];
  S.fields.forEach((f) => {
    const values = picked.map((r) => r.fields[f.key] ?? "");
    const diff = new Set(values.map(normValue)).size > 1;
    lines.push([f.label, ...values, diff ? "差异" : "一致"]);
  });

  const csv = lines
    .map((row) => row.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(","))
    .join("\r\n");
  // BOM 让 Excel 正确识别 UTF-8
  const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "sample-comparison.csv";
  link.click();
  URL.revokeObjectURL(url);
  toast(`已导出 ${picked.length} 条样品的对比表`);
});

/* ------------------------------------------------------------------ */
/* cross-paper search                                                  */
/* ------------------------------------------------------------------ */

const SEARCH_COLUMNS = ["mesostructure", "surfactant", "silica_source", "surface_area", "pore_size", "synthesis_temperature", "morphology"];

async function runSearch() {
  const params = new URLSearchParams();
  const q = $("searchQ").value.trim();
  if (q) params.set("q", q);
  if ($("searchField").value) params.set("field", $("searchField").value);
  [["saMin", "sa_min"], ["saMax", "sa_max"], ["poreMin", "pore_min"], ["poreMax", "pore_max"]]
    .forEach(([id, key]) => { if ($(id).value.trim()) params.set(key, $(id).value.trim()); });
  if ($("reviewedOnly").checked) params.set("reviewed_only", "true");

  let data;
  try { data = await api(`/api/search?${params}`); }
  catch (error) { toast(error.message, "err"); return; }

  const labels = Object.fromEntries(S.fields.map((f) => [f.key, f.label]));
  $("searchTable").querySelector("thead").innerHTML =
    `<tr><th>论文</th><th>样品</th>${SEARCH_COLUMNS.map((k) => `<th>${esc(labels[k])}</th>`).join("")}</tr>`;

  $("searchTable").querySelector("tbody").innerHTML = data.results.length
    ? data.results.map((r) => `
      <tr>
        <td class="link" data-open="${r.paper_fk}" title="${esc(r.paper_title)}">${esc(r.paper_key)}</td>
        <td>r${String(r.record_index).padStart(2, "0")}</td>
        ${SEARCH_COLUMNS.map((k) => {
          const value = r.fields[k];
          const numeric = k === "surface_area" || k === "pore_size";
          return value
            ? `<td class="${numeric ? "num" : ""}" title="${esc(value)}">${esc(value)}</td>`
            : '<td class="null">—</td>';
        }).join("")}
      </tr>`).join("")
    : `<tr><td colspan="${SEARCH_COLUMNS.length + 2}"><div class="placeholder">没有符合条件的记录。</div></td></tr>`;

  const stats = data.results
    .map((r) => r.surface_area_num).filter((v) => typeof v === "number");
  $("searchSummary").textContent = stats.length
    ? `命中 ${data.total} 条记录 · 比表面积 ${Math.min(...stats)}–${Math.max(...stats)} m²/g（均值 ${(stats.reduce((a, b) => a + b, 0) / stats.length).toFixed(0)}）`
    : `命中 ${data.total} 条记录`;
}

$("searchBtn").addEventListener("click", runSearch);
$("searchQ").addEventListener("keydown", (e) => { if (e.key === "Enter") runSearch(); });
$("searchTable").addEventListener("click", async (event) => {
  const cell = event.target.closest("[data-open]");
  if (!cell) return;
  showView("review");
  await openPaper(Number(cell.dataset.open));
});

/* ------------------------------------------------------------------ */
/* jobs                                                                */
/* ------------------------------------------------------------------ */

async function loadJobs() {
  const data = await api("/api/jobs");
  $("jobTable").querySelector("tbody").innerHTML = data.jobs.length
    ? data.jobs.map((j) => `
      <tr>
        <td>${j.id}</td>
        <td class="link" data-open="${j.paper_fk}">${esc(j.paper_key)}</td>
        <td>${statusBadge(j)}</td>
        <td>${esc(j.engine || "—")}</td>
        <td>${esc(j.model || "—")}</td>
        <td class="num">${j.n_records}</td>
        <td title="${esc((j.issues || []).join(" / ") || j.message)}">${esc((j.issues || [])[0] || j.message || "—")}</td>
        <td>${esc((j.created_at || "").replace("T", " ").slice(0, 16))}</td>
      </tr>`).join("")
    : '<tr><td colspan="8"><div class="placeholder">还没有任务记录。</div></td></tr>';
}

$("jobTable").addEventListener("click", async (event) => {
  const cell = event.target.closest("[data-open]");
  if (!cell) return;
  showView("review");
  await openPaper(Number(cell.dataset.open));
});

/* ------------------------------------------------------------------ */
/* ask panel                                                           */
/* ------------------------------------------------------------------ */

$("askToggle").addEventListener("click", () => $("askPanel").classList.toggle("open"));
$("askClose").addEventListener("click", () => $("askPanel").classList.remove("open"));
$("askScope").addEventListener("change", (event) => {
  const option = event.target.selectedOptions[0];
  $("askScopeLabel").textContent = option ? option.textContent : "全部论文";
});

$("askBody").addEventListener("click", async (event) => {
  const chip = event.target.closest(".chip");
  if (chip) { $("askInput").value = chip.textContent; $("askForm").requestSubmit(); return; }
  const cite = event.target.closest(".cite");
  if (!cite) return;
  const paperFk = Number(cite.dataset.paper);
  const page = Number(cite.dataset.page);
  if (!paperFk) { toast("该引文对应的论文已不在库中", "warn"); return; }
  showView("review");
  // openPaper() navigates to the record's first evidence page, so the jump to
  // the cited page must happen after it resolves.
  await openPaper(paperFk);
  await showPage(page, cite.dataset.quote);
});

$("askForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = $("askInput").value.trim();
  if (!question) return;
  $("askInput").value = "";

  const body = $("askBody");
  body.insertAdjacentHTML("beforeend", `<div class="ask-msg user">${esc(question)}</div>`);
  const pending = document.createElement("div");
  pending.className = "ask-msg bot";
  pending.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
  body.append(pending);
  body.scrollTop = body.scrollHeight;

  try {
    const scope = $("askScope").value;
    const result = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, paper_fk: scope ? Number(scope) : null }),
    });
    const citations = (result.citations || []).map((c) => `
      <div class="cite" data-paper="${paperFkOf(c.paper_key)}" data-page="${c.page}"
           data-quote="${esc(c.quote)}">
        <b>${esc(c.paper_key)} · p.${c.page}</b><br/>${esc(c.quote)}
      </div>`).join("");
    pending.innerHTML = `
      <p>${esc(result.answer).replace(/\n/g, "<br/>")}</p>
      ${citations ? `<div class="cite-list">${citations}</div>` : ""}
      <p class="muted">回答引擎：${esc(result.engine === "llm" ? "LLM + 检索" : "检索（抽取式）")} · 点击引文跳转原文</p>`;
  } catch (error) {
    pending.innerHTML = `<p>出错了：${esc(error.message)}</p>`;
  }
  body.scrollTop = body.scrollHeight;
});

function paperFkOf(paperKey) {
  const paper = S.papers.find((p) => p.paper_key === paperKey);
  return paper ? paper.id : "";
}

/* ------------------------------------------------------------------ */

(async function boot() {
  try {
    await loadStatus();
    await loadPapers();
    if (S.papers.length) {
      const withRecords = S.papers.find((p) => p.n_records > 0);
      if (withRecords) await openPaper(withRecords.id);
    }
  } catch (error) {
    toast(`初始化失败：${error.message}`, "err");
  }
})();
