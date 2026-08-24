(() => {
  "use strict";

  function workspaceId() {
    const value = Number.parseInt(localStorage.getItem("techpilot.workspaceId") || "", 10);
    return Number.isInteger(value) && value > 0 ? value : null;
  }

  function isKnowledgeVisible() {
    const view = document.querySelector("#knowledgeView");
    return Boolean(view && view.classList.contains("active"));
  }

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[ch]);
  }

  async function refreshPersistentDocuments() {
    const id = workspaceId();
    if (!id || !isKnowledgeVisible()) return;

    const list = document.querySelector("#uploadList");
    const empty = document.querySelector("#emptyUploads");
    const sourceCount = document.querySelector("#uploadedCount");
    const chunkCount = document.querySelector("#chunkCount");
    const contextSourceCount = document.querySelector("#contextSourceCount");
    const contextChunkCount = document.querySelector("#contextChunkCount");
    if (!list) return;

    try {
      const response = await fetch(`/workspaces/${id}/documents`, { cache: "no-store" });
      if (!response.ok) return;
      const docs = await response.json();
      const completed = docs.filter((item) => item.status === "COMPLETED" || item.status === "PARTIAL");
      const chunks = completed.reduce((sum, item) => sum + Number(item.chunk_count || 0), 0);

      if (sourceCount) sourceCount.textContent = String(completed.length);
      if (chunkCount) chunkCount.textContent = String(chunks);
      if (contextSourceCount) contextSourceCount.textContent = String(completed.length);
      if (contextChunkCount) contextChunkCount.textContent = String(chunks);

      if (!completed.length) {
        if (empty) empty.classList.remove("hidden");
        list.replaceChildren();
        return;
      }

      if (empty) empty.classList.add("hidden");
      list.innerHTML = completed.map((item) => `
        <article class="persistent-source-card">
          <div class="persistent-source-main">
            <span class="persistent-source-type">${esc(String(item.file_type || "").toUpperCase())}</span>
            <div>
              <strong>${esc(item.name)}</strong>
              <small>Document #${item.id} · ${Number(item.chunk_count || 0)} chunks · persisted</small>
            </div>
          </div>
          <span class="persistent-source-status">${esc(item.status)}</span>
        </article>
      `).join("");
    } catch (_) {}
  }

  document.addEventListener("click", (event) => {
    const nav = event.target.closest('[data-view="knowledge"]');
    if (nav) setTimeout(refreshPersistentDocuments, 50);
  });

  window.addEventListener("focus", refreshPersistentDocuments);
  setInterval(refreshPersistentDocuments, 3000);
  setTimeout(refreshPersistentDocuments, 500);
})();
