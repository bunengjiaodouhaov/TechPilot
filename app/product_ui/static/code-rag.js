(() => {
  "use strict";

  const navStack = document.querySelector(".nav-stack");
  const mainStage = document.querySelector(".main-stage");
  if (!navStack || !mainStage) return;

  let nav = document.querySelector('[data-view="repository"]');
  if (!nav) {
    nav = document.createElement("button");
    nav.className = "nav-item";
    nav.dataset.view = "repository";
    nav.type = "button";
    nav.innerHTML = `
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 9 4 12l4 3M16 9l4 3-4 3M14 5l-4 14"/></svg>
      <span>代码仓库</span>
    `;
    const knowledge = navStack.querySelector('[data-view="knowledge"]');
    knowledge?.insertAdjacentElement("afterend", nav);
  }

  let section = document.querySelector("#repositoryView");
  if (!section) {
    section = document.createElement("section");
    section.className = "view code-rag-view";
    section.id = "repositoryView";
    mainStage.append(section);
  }

  section.innerHTML = `
    <header class="topbar">
      <div>
        <div class="eyebrow"><span></span> REPOSITORY INTELLIGENCE</div>
        <h1>代码仓库问答</h1>
      </div>
      <div class="topbar-actions">
        <select id="repoSelect" class="repo-select" aria-label="Repository"></select>
        <button class="ghost-button" id="repoReindex" type="button">重新索引</button>
      </div>
    </header>
    <div class="code-rag-content">
      <section class="code-rag-hero">
        <p class="section-kicker">CODE RAG</p>
        <h2>上传自己的代码仓库，然后直接问代码。</h2>
        <p>上传 ZIP 后安全解压到 TechPilot 的受控仓库目录，再复用现有 RepositoryReadBoundary、Python symbol chunking 和 hybrid retrieval。</p>

        <div class="repo-upload-row">
          <input id="repoZipInput" type="file" accept=".zip,application/zip" hidden />
          <button class="secondary-button" id="repoUploadButton" type="button">上传 ZIP 仓库</button>
          <span id="repoUploadHint">支持 Python 仓库 · 最大 50 MB</span>
        </div>

        <div class="code-rag-stats">
          <div><span>当前仓库</span><strong id="repoName">—</strong></div>
          <div><span>Python 文件</span><strong id="repoFiles">—</strong></div>
          <div><span>代码块</span><strong id="repoChunks">—</strong></div>
        </div>
      </section>

      <form class="code-rag-form" id="repoAskForm">
        <textarea id="repoQuestion" rows="3" maxlength="2000"
          placeholder="例如：这个项目的认证中间件是怎么组织的？"></textarea>
        <div class="code-rag-form-bottom">
          <span id="repoStatus">选择仓库后会按需建立代码索引。</span>
          <button class="send-button" type="submit" aria-label="Ask repository">→</button>
        </div>
      </form>

      <section class="code-rag-answer hidden" id="repoAnswerCard">
        <div class="code-rag-answer-head">
          <strong>TechPilot · Code RAG</strong>
          <span id="repoGrounding"></span>
        </div>
        <div class="code-rag-answer-text" id="repoAnswer"></div>
        <div class="code-rag-evidence" id="repoEvidence"></div>
      </section>
    </div>
  `;

  const els = {
    select: document.querySelector("#repoSelect"),
    zip: document.querySelector("#repoZipInput"),
    upload: document.querySelector("#repoUploadButton"),
    uploadHint: document.querySelector("#repoUploadHint"),
    reindex: document.querySelector("#repoReindex"),
    name: document.querySelector("#repoName"),
    files: document.querySelector("#repoFiles"),
    chunks: document.querySelector("#repoChunks"),
    status: document.querySelector("#repoStatus"),
    form: document.querySelector("#repoAskForm"),
    question: document.querySelector("#repoQuestion"),
    card: document.querySelector("#repoAnswerCard"),
    answer: document.querySelector("#repoAnswer"),
    grounding: document.querySelector("#repoGrounding"),
    evidence: document.querySelector("#repoEvidence"),
  };

  function activeRepositoryId() {
    return els.select.value || localStorage.getItem("techpilot.repositoryId") || "techpilot";
  }

  async function json(response) {
    let payload = null;
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(payload?.detail || `HTTP ${response.status}`);
    return payload;
  }

  async function loadRepositories(preferred = null) {
    const repositories = await json(await fetch("/repository/repositories", { cache: "no-store" }));
    const wanted = preferred || localStorage.getItem("techpilot.repositoryId") || "techpilot";
    els.select.replaceChildren();

    repositories.forEach((repo) => {
      const option = document.createElement("option");
      option.value = repo.repository_id;
      option.textContent = `${repo.name}${repo.builtin ? " · built-in" : ""}`;
      els.select.append(option);
    });

    const exists = repositories.some((repo) => repo.repository_id === wanted);
    els.select.value = exists ? wanted : "techpilot";
    localStorage.setItem("techpilot.repositoryId", els.select.value);
    await loadStatus();
  }

  async function loadStatus() {
    const id = activeRepositoryId();
    els.status.textContent = "正在建立/读取代码索引…";
    try {
      const data = await json(await fetch(
        `/repository/status?repository_id=${encodeURIComponent(id)}`,
        { cache: "no-store" }
      ));
      els.name.textContent = data.repository || id;
      els.files.textContent = String(data.python_files ?? "—");
      els.chunks.textContent = String(data.chunks ?? "—");
      els.status.textContent = "代码索引就绪。";
    } catch (error) {
      els.status.textContent = `Code RAG 请求失败: ${error.message}`;
    }
  }

  async function activateRepository() {
    document.querySelectorAll(".nav-item[data-view]").forEach(
      (item) => item.classList.toggle("active", item === nav)
    );
    document.querySelectorAll(".main-stage > .view").forEach(
      (view) => view.classList.remove("active")
    );
    section.classList.add("active");
    try {
      await loadRepositories();
    } catch (error) {
      els.status.textContent = `仓库列表加载失败: ${error.message}`;
    }
  }

  nav.addEventListener("click", activateRepository);
  document.querySelectorAll('.nav-item[data-view]:not([data-view="repository"])').forEach((item) => {
    item.addEventListener("click", () => section.classList.remove("active"));
  });

  els.select.addEventListener("change", async () => {
    localStorage.setItem("techpilot.repositoryId", els.select.value);
    els.card.classList.add("hidden");
    await loadStatus();
  });

  els.upload.addEventListener("click", () => els.zip.click());
  els.zip.addEventListener("change", async () => {
    const file = els.zip.files?.[0];
    if (!file) return;
    els.upload.disabled = true;
    els.uploadHint.textContent = `正在上传 ${file.name}…`;

    try {
      const form = new FormData();
      form.append("file", file, file.name);
      const uploaded = await json(await fetch("/repository/repositories/upload", {
        method: "POST",
        body: form,
      }));
      els.uploadHint.textContent = `${uploaded.name} 已保存，正在建立索引…`;
      await loadRepositories(uploaded.repository_id);
      els.uploadHint.textContent = `${uploaded.name} 已就绪`;
    } catch (error) {
      els.uploadHint.textContent = `上传失败: ${error.message}`;
    } finally {
      els.upload.disabled = false;
      els.zip.value = "";
    }
  });

  els.reindex.addEventListener("click", async () => {
    const id = activeRepositoryId();
    els.status.textContent = "正在重新索引…";
    try {
      const data = await json(await fetch(
        `/repository/reindex?repository_id=${encodeURIComponent(id)}`,
        { method: "POST" }
      ));
      els.files.textContent = String(data.python_files ?? "—");
      els.chunks.textContent = String(data.chunks ?? "—");
      els.status.textContent = "代码索引就绪。";
    } catch (error) {
      els.status.textContent = `重新索引失败: ${error.message}`;
    }
  });

  els.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = els.question.value.trim();
    if (!question) return;
    const repositoryId = activeRepositoryId();

    els.status.textContent = "正在检索代码证据并生成回答…";
    els.card.classList.add("hidden");

    try {
      const data = await json(await fetch("/repository/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          repository_id: repositoryId,
          question,
          limit: 5,
        }),
      }));
      els.answer.textContent = data.answer || "";
      els.grounding.textContent = data.refused
        ? "证据不足"
        : `${data.citations.length} 条代码证据`;
      els.evidence.innerHTML = (data.citations || []).map((item) => `
        <details class="code-evidence-item">
          <summary>
            <strong>${item.file_path}</strong>
            <span>${item.symbol} · L${item.line_start}-${item.line_end}</span>
          </summary>
          <pre></pre>
        </details>
      `).join("");
      els.evidence.querySelectorAll("pre").forEach((pre, index) => {
        pre.textContent = data.citations[index].excerpt;
      });
      els.card.classList.remove("hidden");
      els.status.textContent = "代码索引就绪。";
    } catch (error) {
      els.status.textContent = `Code RAG 请求失败: ${error.message}`;
    }
  });
})();
