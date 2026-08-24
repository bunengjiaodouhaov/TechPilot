(() => {
  "use strict";

  const workspaceId = () => {
    const value = Number.parseInt(localStorage.getItem("techpilot.workspaceId") || "", 10);
    return Number.isInteger(value) && value > 0 ? value : null;
  };
  const key = (wid) => `techpilot.conversationId.${wid}`;
  const currentId = () => {
    const wid = workspaceId();
    if (!wid) return null;
    const value = Number.parseInt(localStorage.getItem(key(wid)) || "", 10);
    return Number.isInteger(value) && value > 0 ? value : null;
  };

  async function parse(response) {
    let body = null;
    try { body = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(body?.detail || `HTTP ${response.status}`);
    return body;
  }

  function notify() {
    window.dispatchEvent(new CustomEvent("techpilot:conversation-changed"));
  }

  async function mount() {
    if (document.querySelector("#conversationControls")) return;
    const host = document.querySelector("#researchView .topbar .topbar-actions");
    if (!host) return;

    const wrap = document.createElement("div");
    wrap.id = "conversationControls";
    wrap.className = "conversation-controls";
    wrap.innerHTML = `
      <select id="conversationSelect" class="conversation-select"></select>
      <button id="newConversationButton" class="ghost-button" type="button">+ 新对话</button>
      <button id="deleteConversationButton" class="ghost-button" type="button">删除</button>
    `;
    host.prepend(wrap);

    const select = wrap.querySelector("#conversationSelect");
    const create = wrap.querySelector("#newConversationButton");
    const remove = wrap.querySelector("#deleteConversationButton");

    async function refresh(preferred = null) {
      const wid = workspaceId();
      if (!wid) return;
      let items = await parse(await fetch(`/workspaces/${wid}/conversations`, { cache: "no-store" }));
      if (!items.length) {
        const created = await parse(await fetch(`/workspaces/${wid}/conversations`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: "新对话" }),
        }));
        items = [created];
      }
      const wanted = Number(preferred || currentId());
      const active = items.find((item) => item.id === wanted) || items[0];
      select.replaceChildren();
      items.forEach((item) => {
        const option = document.createElement("option");
        option.value = String(item.id);
        option.textContent = `${item.title} · ${item.turn_count || 0} 条`;
        select.append(option);
      });
      select.value = String(active.id);
      localStorage.setItem(key(wid), String(active.id));
    }

    select.addEventListener("change", () => {
      const wid = workspaceId();
      if (!wid) return;
      localStorage.setItem(key(wid), select.value);
      notify();
    });

    create.addEventListener("click", async () => {
      const wid = workspaceId();
      if (!wid) return;
      const created = await parse(await fetch(`/workspaces/${wid}/conversations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "新对话" }),
      }));
      await refresh(created.id);
      notify();
    });

    remove.addEventListener("click", async () => {
      const wid = workspaceId();
      const cid = currentId();
      if (!wid || !cid) return;
      const response = await fetch(`/conversations/${cid}`, { method: "DELETE" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      localStorage.removeItem(key(wid));
      await refresh();
      notify();
    });

    window.addEventListener("techpilot:workspace-ready", async () => {
      await refresh();
      notify();
    });
    window.addEventListener("techpilot:conversation-updated", () => refresh().catch(() => {}));

    await refresh();
    notify();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => mount().catch(() => {}));
  } else {
    mount().catch(() => {});
  }
})();
