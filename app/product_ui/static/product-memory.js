(() => {
  "use strict";

  const DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

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

  function showToast(title, detail, type = "success") {
    const region = document.querySelector("#toastRegion");
    if (!region) return;

    const toast = document.createElement("div");
    toast.className = `toast${type === "error" ? " error" : ""}`;
    const indicator = document.createElement("span");
    indicator.className = "toast-indicator";
    const copy = document.createElement("div");
    const heading = document.createElement("strong");
    heading.textContent = title;
    const body = document.createElement("span");
    body.textContent = detail;
    copy.append(heading, body);
    toast.append(indicator, copy);
    region.append(toast);
    window.setTimeout(() => toast.remove(), 4200);
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

  function prepareDocxUI() {
    const input = document.querySelector("#fileInput");
    if (input) {
      const values = new Set(
        String(input.getAttribute("accept") || "")
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean)
      );
      values.add(".docx");
      values.add(DOCX_MIME);
      input.setAttribute("accept", Array.from(values).join(","));
    }

    const scope = document.querySelector(".metric-scope strong");
    if (scope && !scope.textContent.includes("DOCX")) {
      scope.textContent = "PDF · MD · DOCX";
    }

    const formatRow = document.querySelector(".format-row");
    if (formatRow && !Array.from(formatRow.children).some((node) => node.textContent === "DOCX")) {
      const badge = document.createElement("span");
      badge.textContent = "DOCX";
      formatRow.insertBefore(badge, formatRow.lastElementChild);
    }
  }

  async function uploadDocx(file) {
    const id = workspaceId();
    if (!id) {
      showToast("Choose a workspace", "Create or select a workspace before adding sources.", "error");
      return;
    }

    const progress = document.querySelector("#uploadProgress");
    const filename = document.querySelector("#uploadFilename");
    const dropzone = document.querySelector("#dropzone");
    const input = document.querySelector("#fileInput");
    if (filename) filename.textContent = file.name;
    if (progress) progress.classList.remove("hidden");
    if (dropzone) dropzone.disabled = true;

    try {
      const form = new FormData();
      form.append("workspace_id", String(id));
      form.append("file", file, file.name);
      const response = await fetch("/documents/upload", {
        method: "POST",
        body: form,
      });

      let payload = null;
      try {
        payload = await response.json();
      } catch (_) {
        payload = null;
      }

      if (!response.ok) {
        const detail = typeof payload?.detail === "string"
          ? payload.detail
          : `Upload failed with status ${response.status}.`;
        throw new Error(detail);
      }

      showToast(
        "DOCX indexed",
        `${payload.filename || file.name} · ${Number(payload.chunk_count || 0)} chunks`
      );
      await refreshPersistentDocuments();
    } catch (error) {
      showToast("DOCX upload failed", error?.message || "Unexpected upload error.", "error");
    } finally {
      if (progress) progress.classList.add("hidden");
      if (dropzone) dropzone.disabled = false;
      if (input) input.value = "";
    }
  }

  function isDocx(file) {
    return Boolean(file && String(file.name || "").toLowerCase().endsWith(".docx"));
  }

  // app.js owns PDF/Markdown uploads. Capture DOCX before its target-level
  // handlers so DOCX follows the same backend upload endpoint without changing
  // the existing PDF/Markdown behavior.
  document.addEventListener("change", (event) => {
    if (event.target?.id !== "fileInput") return;
    const file = event.target.files?.[0];
    if (!isDocx(file)) return;
    event.stopImmediatePropagation();
    void uploadDocx(file);
  }, true);

  document.addEventListener("drop", (event) => {
    const dropzone = event.target?.closest?.("#dropzone");
    if (!dropzone) return;
    const file = event.dataTransfer?.files?.[0];
    if (!isDocx(file)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    dropzone.classList.remove("dragging");
    void uploadDocx(file);
  }, true);

  document.addEventListener("click", (event) => {
    const nav = event.target.closest('[data-view="knowledge"]');
    if (nav) setTimeout(refreshPersistentDocuments, 50);
  });

  window.addEventListener("focus", refreshPersistentDocuments);
  prepareDocxUI();
  setInterval(refreshPersistentDocuments, 3000);
  setTimeout(refreshPersistentDocuments, 500);
})();
