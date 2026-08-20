(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function normalizeWorkspaceId(value) {
    const parsed = Number.parseInt(String(value ?? ""), 10);
    return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
  }

  const state = {
    workspaceId: normalizeWorkspaceId(localStorage.getItem("techpilot.workspaceId")),
    workspaces: [],
    pendingDeleteWorkspaceId: null,
    activeView: "research",
    citations: [],
    uploads: [],
    selectedUploadId: null,
    busy: false,
  };

  const els = {
    appShell: $("#appShell"),
    navItems: $$(".nav-item[data-view]"),
    researchView: $("#researchView"),
    knowledgeView: $("#knowledgeView"),
    welcomeState: $("#welcomeState"),
    conversation: $("#conversation"),
    chatScroll: $("#chatScroll"),
    askForm: $("#askForm"),
    questionInput: $("#questionInput"),
    sendButton: $("#sendButton"),
    clearConversation: $("#clearConversation"),
    composerWorkspace: $("#composerWorkspace"),
    researchWorkspace: $("#researchWorkspace"),
    knowledgeWorkspace: $("#knowledgeWorkspace"),
    workspaceButton: $("#workspaceButton"),
    workspaceButtonValue: $("#workspaceButtonValue"),
    researchWorkspaceButton: $("#researchWorkspaceButton"),
    knowledgeWorkspaceButton: $("#knowledgeWorkspaceButton"),
    composerWorkspaceButton: $("#composerWorkspaceButton"),
    workspaceModal: $("#workspaceModal"),
    closeWorkspaceModal: $("#closeWorkspaceModal"),
    workspaceList: $("#workspaceList"),
    showCreateWorkspace: $("#showCreateWorkspace"),
    createWorkspaceForm: $("#createWorkspaceForm"),
    workspaceNameInput: $("#workspaceNameInput"),
    cancelCreateWorkspace: $("#cancelCreateWorkspace"),
    createWorkspace: $("#createWorkspace"),
    workspaceDeleteConfirm: $("#workspaceDeleteConfirm"),
    deleteWorkspaceName: $("#deleteWorkspaceName"),
    cancelDeleteWorkspace: $("#cancelDeleteWorkspace"),
    confirmDeleteWorkspace: $("#confirmDeleteWorkspace"),
    statusDot: $("#statusDot"),
    systemStatus: $("#systemStatus"),
    systemDetail: $("#systemDetail"),
    healthList: $("#healthList"),
    refreshHealth: $("#refreshHealth"),
    contextPanel: $("#contextPanel"),
    panelKicker: $("#panelKicker"),
    panelTitle: $("#panelTitle"),
    panelMeta: $("#panelMeta"),
    researchContext: $("#researchContext"),
    knowledgeContext: $("#knowledgeContext"),
    citationList: $("#citationList"),
    fileInput: $("#fileInput"),
    dropzone: $("#dropzone"),
    uploadProgress: $("#uploadProgress"),
    uploadFilename: $("#uploadFilename"),
    uploadList: $("#uploadList"),
    emptyUploads: $("#emptyUploads"),
    uploadedCount: $("#uploadedCount"),
    chunkCount: $("#chunkCount"),
    contextWorkspace: $("#contextWorkspace"),
    contextSourceCount: $("#contextSourceCount"),
    contextChunkCount: $("#contextChunkCount"),
    sourceDetailStatus: $("#sourceDetailStatus"),
    sourceDetailEmpty: $("#sourceDetailEmpty"),
    sourceDetailCard: $("#sourceDetailCard"),
    detailType: $("#detailType"),
    detailName: $("#detailName"),
    detailId: $("#detailId"),
    detailChunks: $("#detailChunks"),
    detailStatus: $("#detailStatus"),
    detailChecksum: $("#detailChecksum"),
    toastRegion: $("#toastRegion"),
  };

  function currentWorkspace() {
    return state.workspaces.find((workspace) => workspace.id === state.workspaceId) || null;
  }

  function updateWorkspaceDisplay() {
    const workspace = currentWorkspace();
    if (!workspace) {
      els.workspaceButtonValue.textContent = "No workspace";
      els.composerWorkspace.textContent = "None";
      els.researchWorkspace.textContent = "No workspace";
      els.knowledgeWorkspace.textContent = "No workspace";
      els.contextWorkspace.textContent = "—";
      return;
    }

    const name = String(workspace.name || `Workspace ${workspace.id}`);
    const longLabel = `${name} · #${workspace.id}`;
    els.workspaceButtonValue.textContent = name;
    els.workspaceButtonValue.title = longLabel;
    els.composerWorkspace.textContent = name;
    els.researchWorkspace.textContent = longLabel;
    els.knowledgeWorkspace.textContent = longLabel;
    els.contextWorkspace.textContent = `#${workspace.id}`;
  }

  function setWorkspace(workspace) {
    const nextId = normalizeWorkspaceId(workspace?.id);
    if (!nextId) return false;

    const changed = state.workspaceId !== nextId;
    state.workspaceId = nextId;
    localStorage.setItem("techpilot.workspaceId", String(nextId));
    updateWorkspaceDisplay();

    if (changed) {
      state.citations = [];
      state.uploads = [];
      state.selectedUploadId = null;
      resetConversation();
      renderUploads();
    }
    renderWorkspaceList();
    return true;
  }

  function clearWorkspaceSelection() {
    state.workspaceId = null;
    localStorage.removeItem("techpilot.workspaceId");
    state.citations = [];
    state.uploads = [];
    state.selectedUploadId = null;
    updateWorkspaceDisplay();
    resetConversation();
    renderUploads();
  }

  async function loadWorkspaces({ preferCurrent = true } = {}) {
    try {
      const response = await fetch("/workspaces", { cache: "no-store" });
      const payload = await parseResponse(response);
      state.workspaces = Array.isArray(payload) ? payload : [];
      const selected = (preferCurrent && state.workspaces.find((item) => item.id === state.workspaceId)) || state.workspaces[0] || null;
      if (selected) setWorkspace(selected);
      else clearWorkspaceSelection();
      renderWorkspaceList();
      return true;
    } catch (error) {
      state.workspaces = [];
      clearWorkspaceSelection();
      renderWorkspaceList({ error: friendlyError(error) });
      showToast("Workspace manager unavailable", friendlyError(error), "error");
      return false;
    }
  }

  function renderWorkspaceList({ error = "" } = {}) {
    if (!els.workspaceList) return;
    els.workspaceList.replaceChildren();

    if (error) {
      const message = document.createElement("div");
      message.className = "workspace-empty";
      message.textContent = error;
      els.workspaceList.append(message);
      return;
    }

    if (!state.workspaces.length) {
      const empty = document.createElement("div");
      empty.className = "workspace-empty";
      empty.textContent = "No workspaces yet. Create one to start asking questions and indexing sources.";
      els.workspaceList.append(empty);
      return;
    }

    state.workspaces.forEach((workspace) => {
      const active = workspace.id === state.workspaceId;
      const row = document.createElement("div");
      row.className = `workspace-row${active ? " active" : ""}`;

      const icon = document.createElement("span");
      icon.className = "workspace-row-icon";
      icon.textContent = String(workspace.name || "W").trim().slice(0, 1).toUpperCase() || "W";

      const copy = document.createElement("div");
      copy.className = "workspace-row-copy";
      const name = document.createElement("strong");
      name.textContent = workspace.name || `Workspace ${workspace.id}`;
      const meta = document.createElement("span");
      meta.textContent = `Workspace #${workspace.id}`;
      copy.append(name, meta);

      const selectButton = document.createElement("button");
      selectButton.className = "workspace-select";
      selectButton.type = "button";
      selectButton.textContent = active ? "Active" : "Use";
      selectButton.disabled = active;
      selectButton.addEventListener("click", () => {
        setWorkspace(workspace);
        closeWorkspaceModal();
        showToast("Workspace changed", `${workspace.name} is now active.`);
      });

      const deleteButton = document.createElement("button");
      deleteButton.className = "workspace-delete";
      deleteButton.type = "button";
      deleteButton.setAttribute("aria-label", `Delete ${workspace.name || "workspace"}`);
      deleteButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13"/></svg>';
      deleteButton.addEventListener("click", () => showDeleteWorkspaceConfirm(workspace));

      row.append(icon, copy, selectButton, deleteButton);
      els.workspaceList.append(row);
    });
  }

  function showCreateWorkspaceForm() {
    hideDeleteWorkspaceConfirm();
    els.createWorkspaceForm.classList.remove("hidden");
    els.workspaceNameInput.value = "";
    requestAnimationFrame(() => els.workspaceNameInput.focus());
  }

  function hideCreateWorkspaceForm() {
    els.createWorkspaceForm.classList.add("hidden");
    els.workspaceNameInput.value = "";
  }

  function showDeleteWorkspaceConfirm(workspace) {
    hideCreateWorkspaceForm();
    state.pendingDeleteWorkspaceId = workspace.id;
    els.deleteWorkspaceName.textContent = workspace.name || `Workspace #${workspace.id}`;
    els.workspaceDeleteConfirm.classList.remove("hidden");
  }

  function hideDeleteWorkspaceConfirm() {
    state.pendingDeleteWorkspaceId = null;
    els.workspaceDeleteConfirm.classList.add("hidden");
  }

  function openWorkspaceModal() {
    hideCreateWorkspaceForm();
    hideDeleteWorkspaceConfirm();
    els.workspaceModal.classList.remove("hidden");
    loadWorkspaces();
  }

  function closeWorkspaceModal() {
    els.workspaceModal.classList.add("hidden");
    hideCreateWorkspaceForm();
    hideDeleteWorkspaceConfirm();
  }

  async function createWorkspace(name) {
    const normalized = String(name || "").trim();
    if (!normalized) {
      showToast("Workspace name required", "Enter a name before creating the workspace.", "error");
      return;
    }

    els.createWorkspace.disabled = true;
    try {
      const response = await fetch("/workspaces", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: normalized }),
      });
      const workspace = await parseResponse(response);
      await loadWorkspaces({ preferCurrent: false });
      const created = state.workspaces.find((item) => item.id === workspace.id) || workspace;
      setWorkspace(created);
      hideCreateWorkspaceForm();
      showToast("Workspace created", `${created.name} is ready.`);
    } catch (error) {
      showToast("Create failed", friendlyError(error), "error");
    } finally {
      els.createWorkspace.disabled = false;
    }
  }

  async function deleteWorkspace() {
    const workspaceId = state.pendingDeleteWorkspaceId;
    if (!workspaceId) return;
    const target = state.workspaces.find((item) => item.id === workspaceId);
    els.confirmDeleteWorkspace.disabled = true;
    try {
      const response = await fetch(`/workspaces/${encodeURIComponent(workspaceId)}`, { method: "DELETE" });
      if (!response.ok) await parseResponse(response);
      const wasActive = state.workspaceId === workspaceId;
      state.workspaces = state.workspaces.filter((item) => item.id !== workspaceId);
      hideDeleteWorkspaceConfirm();
      if (wasActive) {
        const next = state.workspaces[0] || null;
        if (next) setWorkspace(next);
        else clearWorkspaceSelection();
      } else {
        renderWorkspaceList();
      }
      showToast("Workspace deleted", target?.name || `Workspace #${workspaceId}`);
    } catch (error) {
      showToast("Delete blocked", friendlyError(error), "error");
    } finally {
      els.confirmDeleteWorkspace.disabled = false;
    }
  }

  function syncContextPanel() {
    const researchHasEvidence = state.activeView === "research" && state.citations.length > 0;
    const knowledgeActive = state.activeView === "knowledge";
    const show = researchHasEvidence || knowledgeActive;
    els.appShell.classList.toggle("has-context", show);

    if (knowledgeActive) {
      els.panelKicker.textContent = "WORKSPACE SOURCE STATE";
      els.panelTitle.textContent = "Index context";
      els.panelMeta.textContent = "session local";
      els.researchContext.classList.add("hidden");
      els.knowledgeContext.classList.remove("hidden");
    } else {
      els.panelKicker.textContent = "CURRENT ANSWER";
      els.panelTitle.textContent = "Evidence";
      els.panelMeta.textContent = `${state.citations.length} source${state.citations.length === 1 ? "" : "s"}`;
      els.researchContext.classList.remove("hidden");
      els.knowledgeContext.classList.add("hidden");
    }
  }

  function switchView(view) {
    if (!view || !["research", "knowledge"].includes(view)) return;
    state.activeView = view;
    els.navItems.forEach((item) => item.classList.toggle("active", item.dataset.view === view));
    els.researchView.classList.toggle("active", view === "research");
    els.knowledgeView.classList.toggle("active", view === "knowledge");
    syncContextPanel();
  }

  function autoResizeTextarea() {
    els.questionInput.style.height = "auto";
    els.questionInput.style.height = `${Math.min(els.questionInput.scrollHeight, 170)}px`;
  }

  function scrollChatToBottom() {
    requestAnimationFrame(() => {
      els.chatScroll.scrollTo({ top: els.chatScroll.scrollHeight, behavior: "smooth" });
    });
  }

  function formatTime(date = new Date()) {
    return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(date);
  }

  function appendMessage({ role, text, refused = false, sourceCount = 0, loading = false, error = false }) {
    els.welcomeState.classList.add("hidden");

    const article = document.createElement("article");
    article.className = `message ${role}${error ? " error" : ""}`;

    const head = document.createElement("div");
    head.className = "message-head";

    const avatar = document.createElement("span");
    avatar.className = "message-avatar";
    avatar.textContent = role === "user" ? "YOU" : "TP";

    const author = document.createElement("span");
    author.className = "message-author";
    author.textContent = role === "user" ? "You" : error ? "TechPilot · request error" : loading ? "TechPilot · researching" : "TechPilot · grounded result";

    const time = document.createElement("span");
    time.className = "message-time";
    time.textContent = formatTime();

    head.append(avatar, author, time);

    const body = document.createElement("div");
    body.className = "message-body";
    if (loading) {
      const dots = document.createElement("span");
      dots.className = "thinking";
      dots.setAttribute("aria-label", "TechPilot is thinking");
      dots.append(document.createElement("span"), document.createElement("span"), document.createElement("span"));
      body.append(dots);
    } else {
      body.textContent = text;
    }

    article.append(head, body);

    if (!loading && role === "assistant") {
      const footer = document.createElement("div");
      footer.className = "answer-footer";
      const grounding = document.createElement("span");
      grounding.className = "answer-pill";
      grounding.textContent = error ? "Request failed" : refused ? "Evidence incomplete" : "Evidence-grounded";
      footer.append(grounding);
      if (sourceCount > 0) {
        const sources = document.createElement("span");
        sources.className = "answer-pill source";
        sources.textContent = `${sourceCount} supporting source${sourceCount === 1 ? "" : "s"}`;
        footer.append(sources);
      }
      if (refused) {
        const refusal = document.createElement("span");
        refusal.className = "answer-pill refused";
        refusal.textContent = "Insufficient evidence";
        footer.append(refusal);
      }
      article.append(footer);
    }

    els.conversation.append(article);
    scrollChatToBottom();
    return article;
  }

  function setBusy(busy) {
    state.busy = busy;
    els.sendButton.disabled = busy || !els.questionInput.value.trim();
    els.questionInput.disabled = busy;
  }

  async function parseResponse(response) {
    const contentType = response.headers.get("content-type") || "";
    let payload = null;
    if (contentType.includes("application/json")) {
      try { payload = await response.json(); } catch (_) { payload = null; }
    } else {
      try { payload = { detail: await response.text() }; } catch (_) { payload = null; }
    }
    if (!response.ok) {
      const detail = payload?.detail;
      const message = typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((item) => item.msg || "validation error").join("; ")
          : `Request failed with HTTP ${response.status}`;
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  async function submitQuestion(question) {
    if (state.busy) return;
    if (!state.workspaceId) {
      showToast("Choose a workspace", "Create or select a workspace before asking a question.", "error");
      openWorkspaceModal();
      return;
    }
    const trimmed = question.trim();
    if (!trimmed) return;

    renderCitations([]);
    appendMessage({ role: "user", text: trimmed });
    els.questionInput.value = "";
    autoResizeTextarea();
    setBusy(true);
    const pending = appendMessage({ role: "assistant", loading: true });

    try {
      const response = await fetch("/answers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace_id: state.workspaceId, question: trimmed }),
      });
      const payload = await parseResponse(response);
      pending.remove();
      renderCitations(Array.isArray(payload.citations) ? payload.citations : []);
      appendMessage({
        role: "assistant",
        text: payload.answer || "No answer text was returned.",
        refused: Boolean(payload.refused),
        sourceCount: state.citations.length,
      });
    } catch (error) {
      pending.remove();
      renderCitations([]);
      appendMessage({ role: "assistant", text: friendlyError(error), error: true });
      showToast("Answer request failed", friendlyError(error), "error");
    } finally {
      setBusy(false);
      els.questionInput.focus();
    }
  }

  function friendlyError(error) {
    if (error?.status === 404) return "The selected workspace no longer exists. Choose or create another workspace.";
    if (error?.status === 409) return error.message || "This action conflicts with the current workspace state.";
    if (error?.status === 422) return `The request was rejected by validation: ${error.message}`;
    if (error instanceof TypeError) return "The API could not be reached. Confirm the TechPilot service is running.";
    return error?.message || "An unexpected request error occurred.";
  }

  function renderCitations(citations) {
    state.citations = citations;
    els.citationList.replaceChildren();

    citations.forEach((citation, index) => {
      const card = document.createElement("article");
      card.className = "citation-card";

      const head = document.createElement("div");
      head.className = "citation-card-head";
      const number = document.createElement("span");
      number.className = "citation-number";
      number.textContent = String(index + 1).padStart(2, "0");
      const title = document.createElement("div");
      title.className = "citation-title";
      title.textContent = citation.document_name || "Untitled source";
      head.append(number, title);
      card.append(head);

      const locatorParts = [];
      if (citation.section) locatorParts.push(citation.section);
      if (citation.page_start != null) {
        const pageLabel = citation.page_end && citation.page_end !== citation.page_start
          ? `pp. ${citation.page_start}–${citation.page_end}`
          : `p. ${citation.page_start}`;
        locatorParts.push(pageLabel);
      }
      if (locatorParts.length) {
        const meta = document.createElement("div");
        meta.className = "citation-meta";
        meta.textContent = locatorParts.join(" · ");
        card.append(meta);
      }

      if (citation.quote) {
        const quote = document.createElement("div");
        quote.className = "citation-quote";
        quote.textContent = citation.quote;
        card.append(quote);
      }

      els.citationList.append(card);
    });
    syncContextPanel();
  }

  function humanizeDependencyName(name) {
    return String(name).replace(/[_-]+/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
  }

  function normalizeDependencyState(raw) {
    const status = String(raw?.status ?? raw ?? "unknown").toLowerCase();
    if (["ok", "healthy", "up"].includes(status)) return { label: "healthy", className: "ok" };
    if (["degraded", "warning"].includes(status)) return { label: status, className: "degraded" };
    if (["error", "down", "unhealthy", "failed"].includes(status)) return { label: status, className: "down" };
    return { label: status || "unknown", className: "checking" };
  }

  async function refreshHealth() {
    els.statusDot.className = "status-dot checking";
    els.systemStatus.textContent = "Checking services";
    els.healthList.replaceChildren(createHealthRow("API", "checking", "checking"));

    try {
      const response = await fetch("/health/dependencies", { cache: "no-store" });
      let payload = null;
      try { payload = await response.json(); } catch (_) { payload = null; }
      const dependencies = payload?.dependencies && typeof payload.dependencies === "object" ? payload.dependencies : {};
      const overall = String(payload?.status || (response.ok ? "ok" : "degraded")).toLowerCase();
      const overallClass = overall === "ok" ? "ok" : "degraded";

      els.statusDot.className = `status-dot ${overallClass}`;
      els.systemStatus.textContent = overall === "ok" ? "Systems operational" : "Services degraded";
      const names = Object.keys(dependencies);
      els.systemDetail.textContent = names.length ? names.map(humanizeDependencyName).join(" · ") : "FastAPI service reachable";

      els.healthList.replaceChildren(createHealthRow("API", "healthy", "ok"));
      Object.entries(dependencies).forEach(([name, raw]) => {
        const depState = normalizeDependencyState(raw);
        els.healthList.append(createHealthRow(humanizeDependencyName(name), depState.label, depState.className));
      });
    } catch (_) {
      els.statusDot.className = "status-dot down";
      els.systemStatus.textContent = "API unavailable";
      els.systemDetail.textContent = "Start the FastAPI service to connect";
      els.healthList.replaceChildren(createHealthRow("API", "unreachable", "down"));
    }
  }

  function createHealthRow(name, label, className) {
    const row = document.createElement("div");
    row.className = "health-row";
    const left = document.createElement("span");
    left.textContent = name;
    const right = document.createElement("span");
    right.className = `health-state ${className}`;
    right.textContent = label;
    row.append(left, right);
    return row;
  }

  async function uploadDocument(file) {
    if (!file) return;
    if (!state.workspaceId) {
      showToast("Choose a workspace", "Create or select a workspace before adding sources.", "error");
      openWorkspaceModal();
      return;
    }
    const lowerName = file.name.toLowerCase();
    if (!(lowerName.endsWith(".pdf") || lowerName.endsWith(".md"))) {
      showToast("Unsupported file", "Use a PDF or Markdown document.", "error");
      return;
    }

    els.uploadFilename.textContent = file.name;
    els.uploadProgress.classList.remove("hidden");
    els.dropzone.disabled = true;

    try {
      const form = new FormData();
      form.append("workspace_id", String(state.workspaceId));
      form.append("file", file, file.name);
      const response = await fetch("/documents/upload", { method: "POST", body: form });
      const payload = await parseResponse(response);
      state.uploads.unshift(payload);
      state.selectedUploadId = payload.document_id;
      renderUploads();
      showToast("Source indexed", `${payload.filename} · ${payload.chunk_count} chunks`);
    } catch (error) {
      showToast("Upload failed", friendlyError(error), "error");
    } finally {
      els.uploadProgress.classList.add("hidden");
      els.dropzone.disabled = false;
      els.fileInput.value = "";
    }
  }

  function renderUploads() {
    els.uploadList.replaceChildren();
    const totalChunks = state.uploads.reduce((sum, upload) => sum + (Number(upload.chunk_count) || 0), 0);
    els.uploadedCount.textContent = String(state.uploads.length);
    els.chunkCount.textContent = String(totalChunks);
    els.contextSourceCount.textContent = String(state.uploads.length);
    els.contextChunkCount.textContent = String(totalChunks);
    els.emptyUploads.classList.toggle("hidden", state.uploads.length > 0);

    state.uploads.forEach((upload) => {
      const row = document.createElement("article");
      row.className = `upload-row${upload.document_id === state.selectedUploadId ? " selected" : ""}`;
      row.tabIndex = 0;
      row.setAttribute("role", "button");
      row.setAttribute("aria-label", `Inspect ${upload.filename || "source"}`);

      const icon = document.createElement("span");
      icon.className = "file-icon";
      icon.textContent = upload.file_type ? String(upload.file_type).toUpperCase().slice(0, 3) : "DOC";

      const copy = document.createElement("div");
      copy.className = "upload-file";
      const name = document.createElement("strong");
      name.textContent = upload.filename || `Document ${upload.document_id}`;
      const meta = document.createElement("span");
      meta.textContent = `ID ${upload.document_id} · ${upload.chunk_count} chunks · ${shortChecksum(upload.checksum)}`;
      copy.append(name, meta);

      const status = document.createElement("span");
      status.className = "upload-status";
      status.textContent = upload.status || "indexed";

      const del = document.createElement("button");
      del.className = "delete-upload";
      del.type = "button";
      del.setAttribute("aria-label", `Delete ${upload.filename || "document"}`);
      del.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13"/></svg>';
      del.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteDocument(upload);
      });

      const select = () => selectUpload(upload.document_id);
      row.addEventListener("click", select);
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          select();
        }
      });

      row.append(icon, copy, status, del);
      els.uploadList.append(row);
    });

    renderSelectedUpload();
  }

  function selectUpload(documentId) {
    state.selectedUploadId = documentId;
    renderUploads();
  }

  function renderSelectedUpload() {
    const selected = state.uploads.find((item) => item.document_id === state.selectedUploadId) || null;
    els.sourceDetailEmpty.classList.toggle("hidden", Boolean(selected));
    els.sourceDetailCard.classList.toggle("hidden", !selected);
    els.sourceDetailStatus.textContent = selected ? "Indexed" : "None selected";
    if (!selected) return;

    els.detailType.textContent = selected.file_type ? String(selected.file_type).toUpperCase().slice(0, 3) : "DOC";
    els.detailName.textContent = selected.filename || `Document ${selected.document_id}`;
    els.detailId.textContent = String(selected.document_id ?? "—");
    els.detailChunks.textContent = String(selected.chunk_count ?? "—");
    els.detailStatus.textContent = String(selected.status || "indexed");
    els.detailChecksum.textContent = shortChecksum(selected.checksum);
  }

  function shortChecksum(value) {
    const text = String(value || "");
    return text ? `${text.slice(0, 10)}…` : "unavailable";
  }

  async function deleteDocument(upload) {
    const buttonLabel = upload.filename || `Document ${upload.document_id}`;
    try {
      const response = await fetch(`/documents/${encodeURIComponent(upload.document_id)}?workspace_id=${encodeURIComponent(state.workspaceId)}`, { method: "DELETE" });
      if (!response.ok) await parseResponse(response);
      state.uploads = state.uploads.filter((item) => item.document_id !== upload.document_id);
      if (state.selectedUploadId === upload.document_id) {
        state.selectedUploadId = state.uploads[0]?.document_id ?? null;
      }
      renderUploads();
      showToast("Source removed", buttonLabel);
    } catch (error) {
      showToast("Delete failed", friendlyError(error), "error");
    }
  }

  function showToast(title, detail, type = "success") {
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
    els.toastRegion.append(toast);
    window.setTimeout(() => toast.remove(), 4200);
  }

  function resetConversation() {
    els.conversation.replaceChildren();
    els.welcomeState.classList.remove("hidden");
    renderCitations([]);
    els.chatScroll.scrollTop = 0;
  }

  function bindEvents() {
    els.navItems.forEach((item) => item.addEventListener("click", () => switchView(item.dataset.view)));
    $$('[data-prompt]').forEach((button) => button.addEventListener("click", () => {
      els.questionInput.value = button.dataset.prompt || "";
      autoResizeTextarea();
      els.questionInput.focus();
      els.sendButton.disabled = !els.questionInput.value.trim();
    }));

    els.askForm.addEventListener("submit", (event) => {
      event.preventDefault();
      submitQuestion(els.questionInput.value);
    });
    els.questionInput.addEventListener("input", () => {
      autoResizeTextarea();
      els.sendButton.disabled = state.busy || !els.questionInput.value.trim();
    });
    els.questionInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        els.askForm.requestSubmit();
      }
    });
    els.clearConversation.addEventListener("click", resetConversation);

    [els.workspaceButton, els.researchWorkspaceButton, els.knowledgeWorkspaceButton, els.composerWorkspaceButton]
      .filter(Boolean)
      .forEach((button) => button.addEventListener("click", openWorkspaceModal));
    els.closeWorkspaceModal.addEventListener("click", closeWorkspaceModal);
    els.workspaceModal.addEventListener("click", (event) => {
      if (event.target === els.workspaceModal) closeWorkspaceModal();
    });
    els.showCreateWorkspace.addEventListener("click", showCreateWorkspaceForm);
    els.cancelCreateWorkspace.addEventListener("click", hideCreateWorkspaceForm);
    els.createWorkspaceForm.addEventListener("submit", (event) => {
      event.preventDefault();
      createWorkspace(els.workspaceNameInput.value);
    });
    els.cancelDeleteWorkspace.addEventListener("click", hideDeleteWorkspaceConfirm);
    els.confirmDeleteWorkspace.addEventListener("click", deleteWorkspace);

    els.refreshHealth.addEventListener("click", refreshHealth);
    els.dropzone.addEventListener("click", () => els.fileInput.click());
    els.fileInput.addEventListener("change", () => uploadDocument(els.fileInput.files?.[0]));
    ["dragenter", "dragover"].forEach((eventName) => els.dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      els.dropzone.classList.add("dragging");
    }));
    ["dragleave", "drop"].forEach((eventName) => els.dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      els.dropzone.classList.remove("dragging");
    }));
    els.dropzone.addEventListener("drop", (event) => uploadDocument(event.dataTransfer?.files?.[0]));

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !els.workspaceModal.classList.contains("hidden")) closeWorkspaceModal();
    });
  }

  async function init() {
    bindEvents();
    autoResizeTextarea();
    els.sendButton.disabled = true;
    updateWorkspaceDisplay();
    renderUploads();
    syncContextPanel();
    await loadWorkspaces();
    refreshHealth();
  }

  init();
})();
