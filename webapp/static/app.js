/* MesoScope front-end.
 * No framework on purpose: one file, no build step, easy to demo and to read.
 */

const S = {
  status: null,
  fields: [],
  papers: [],
  paper: null,          // full payload of the paper open in the workbench
  recordIndex: 0,
  page: 1,
  pageCache: new Map(), // `${paperFk}:${page}` -> text
  dirty: new Map(),     // field key -> new value
  activeField: null,
  pollers: new Map(),
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
  setTimeout(() => node.remove(), 3600);
}

/* ------------------------------------------------------------------ */
/* navigation                                                          */
/* ------------------------------------------------------------------ */

const VIEW_META = {
  library: ["文献库", "上传论文、创建抽取任务、查看记录数量"],
  review: ["复核工作台", "左右联动：点击字段跳到证据原文，可直接修改并保存"],
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
}

$("nav").addEventListener("click", (event) => {
  const button = event.target.closest(".nav-item");
  if (button) showView(button.dataset.view);
});

/* ------------------------------------------------------------------ */
/* status + library                                                    */
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

function statusBadge(job) {
  if (!job) return '<span class="badge idle">未抽取</span>';
  const map = {
    queued: ["run", "排队中"],
    running: ["run", job.stage || "运行中"],
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
  renderPaperSelects();
  S.papers.forEach((p) => {
    const job = p.latest_job;
    if (job && (job.status === "running" || job.status === "queued")) pollJob(job.id);
  });
}

function renderLibrary() {
  const grid = $("paperGrid");
  $("libraryEmpty").hidden = S.papers.length > 0;
  grid.innerHTML = S.papers.map((p) => {
    const job = p.latest_job;
    const running = job && (job.status === "running" || job.status === "queued");
    return `
    <div class="paper-card" data-id="${p.id}">
      <div class="paper-top">
        <div>
          <div class="paper-key">${esc(p.paper_key)}</div>
          <h3 class="paper-title">${esc(p.title)}</h3>
        </div>
        ${statusBadge(job)}
      </div>
      <div class="paper-dims">
        <span>${p.pages} 页</span><span>${p.n_tables} 表</span>
        <span>${(p.chars / 1000).toFixed(1)}k 字符</span>
        ${p.n_records ? `<span>${p.n_reviewed}/${p.n_records} 已复核</span>` : ""}
      </div>
      ${running ? `<div class="progress"><i style="width:${job.progress}%"></i></div>` : ""}
      <div class="paper-actions">
        <select class="select sm" data-mode="${p.id}" title="抽取模式">
          <option value="anchored">anchored</option>
          <option value="oneshot">oneshot</option>
          <option value="agent">agent</option>
          <option value="rules">rules</option>
        </select>
        <button class="btn sm" data-act="run" ${running ? "disabled" : ""}>抽取</button>
        <button class="btn sm" data-act="open" ${p.n_records ? "" : "disabled"}>复核</button>
        <button class="btn sm ghost" data-act="del">删</button>
      </div>
    </div>`;
  }).join("");
}

$("paperGrid").addEventListener("click", async (event) => {
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
      toast(`任务已创建（${mode}）`);
      pollJob(job.id);
      loadPapers();
    } catch (error) { toast(error.message, "err"); }
  }

  if (button.dataset.act === "open") {
    $("reviewPaper").value = String(id);
    showView("review");
    await openPaper(id);
  }

  if (button.dataset.act === "del") {
    if (!confirm("删除这篇论文及其全部抽取记录？")) return;
    await api(`/api/papers/${id}`, { method: "DELETE" });
    if (S.paper && S.paper.id === id) { S.paper = null; renderFields(); }
    toast("已删除");
    loadPapers(); loadStatus();
  }
});

function pollJob(jobId) {
  if (S.pollers.has(jobId)) return;
  const timer = setInterval(async () => {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      const card = document.querySelector(`.paper-card[data-id="${job.paper_fk}"]`);
      if (card) {
        card.querySelector(".badge").outerHTML = statusBadge(job);
        const bar = card.querySelector(".progress > i");
        if (bar) bar.style.width = `${job.progress}%`;
      }
      if (job.status === "succeeded" || job.status === "failed") {
        clearInterval(timer);
        S.pollers.delete(jobId);
        if (job.status === "succeeded") {
          toast(`${job.n_records} 条记录已抽取（${job.engine}）`);
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
  }, 1200);
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
      toast(`${paper.paper_key} 已解析：${paper.pages} 页`);
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
    toast("演示数据已载入");
    await loadPapers(); await loadStatus();
  } catch (error) { toast(error.message, "err"); }
});

/* ------------------------------------------------------------------ */
/* review workbench                                                    */
/* ------------------------------------------------------------------ */

function renderPaperSelects() {
  const options = S.papers.map((p) =>
    `<option value="${p.id}">${esc(p.paper_key)} · ${esc(p.title)}</option>`).join("");
  const review = $("reviewPaper");
  const current = review.value;
  review.innerHTML = options || '<option value="">（暂无论文）</option>';
  if (current && S.papers.some((p) => String(p.id) === current)) review.value = current;

  const scope = $("askScope");
  const scopeCurrent = scope.value;
  scope.innerHTML = '<option value="">全部论文</option>' + options;
  scope.value = scopeCurrent;
}

$("reviewPaper").addEventListener("change", (event) => {
  if (event.target.value) openPaper(Number(event.target.value));
});

async function openPaper(paperFk) {
  S.paper = await api(`/api/papers/${paperFk}`);
  S.recordIndex = 0;
  S.dirty.clear();
  S.activeField = null;
  $("reviewPaper").value = String(paperFk);

  const job = S.paper.latest_job;
  $("reviewMeta").innerHTML = [
    `${S.paper.pages} 页`,
    `${S.paper.n_records} 条记录`,
    job ? `引擎 ${esc(job.engine || "—")}` : "未抽取",
    job && job.model ? `模型 ${esc(job.model)}` : "",
  ].filter(Boolean).map((t) => `<span>${t}</span>`).join("");

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
        ${items.map((item) => `<span class="page-pill" data-page="${item.page}"
             data-key="${f.key}" data-idx="${items.indexOf(item)}">p.${item.page}</span>`).join("")}
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

  // Try the whole quote, then progressively shorter prefixes.
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
    loadStatus();
  } catch (error) { toast(error.message, "err"); }
});

$("exportJsonl").addEventListener("click", () =>
  window.open(S.paper ? `/api/export?fmt=jsonl&paper_fk=${S.paper.id}` : "/api/export?fmt=jsonl"));
$("exportCsv").addEventListener("click", () =>
  window.open(S.paper ? `/api/export?fmt=csv&paper_fk=${S.paper.id}` : "/api/export?fmt=csv"));

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
