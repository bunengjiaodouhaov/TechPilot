(() => {
  "use strict";

  const SESSION_KEY = "techpilot_demo_session";
  const LOCALE_KEY = "techpilot_locale";
  const DEFAULT_LOCALE = "zh-CN";

  const translations = new Map([
    ["Evidence-first intelligence", "证据优先的技术智能"],
    ["Workspace", "工作区"],
    ["Ask workspace", "工作区问答"],
    ["Knowledge base", "知识库"],
    ["P5 pipeline", "P5 实验"],
    ["JD evidence", "岗位证据"],
    ["Soon", "已冻结"],
    ["Checking services", "正在检查服务"],
    ["Active workspace", "当前工作区"],
    ["Evidence-grounded Q&A", "证据约束问答"],
    ["Ask your technical workspace", "向技术工作区提问"],
    ["API docs", "API 文档"],
    ["TECHPILOT / WORKSPACE INTELLIGENCE", "TECHPILOT / 工作区智能"],
    ["Ask the system.Inspect the evidence.", "向系统提问。检查证据。"],
    ["Reason over your own technical material. TechPilot answers from indexed workspace sources and keeps the supporting evidence visible when it exists.", "基于你自己的技术资料进行推理。TechPilot 只依据已索引的工作区来源回答，并在存在支持证据时保持证据可见。"],
    ["Map the architecture", "梳理系统架构"],
    ["Summarize the system from evidence", "基于证据总结系统"],
    ["Inspect reliability", "检查可靠性"],
    ["Trace failures and safety boundaries", "追踪失败与安全边界"],
    ["Find evidence", "查找证据"],
    ["Locate source-backed implementation facts", "定位有来源支持的实现事实"],
    ["Enter to send · Shift + Enter for newline", "Enter 发送 · Shift + Enter 换行"],
    ["Grounded answers may stay incomplete when authoritative workspace evidence is missing.", "当缺少权威工作区证据时，回答会明确保持不完整，而不是强行补全。"],
    ["Workspace knowledge", "工作区知识"],
    ["SOURCE LAYER", "来源层"],
    ["Give TechPilot material it can actually ground answers in.", "给 TechPilot 可以真正作为回答依据的材料。"],
    ["PDF and Markdown files move through the existing parser, chunker, persistence, and indexing pipeline. This page only shows source state the current API can prove.", "PDF 和 Markdown 文件会经过现有解析、分块、持久化和索引链路。本页只展示当前 API 能够证明的来源状态。"],
    ["Sources this session", "本次会话来源"],
    ["browser-session uploads", "当前浏览器会话上传"],
    ["Indexed chunks", "已索引分块"],
    ["from uploads shown below", "来自下方上传文件"],
    ["Source scope", "来源范围"],
    ["persistent listing API not exposed", "尚未开放持久来源列表 API"],
    ["ADD SOURCE", "添加来源"],
    ["Index authoritative material", "索引权威材料"],
    ["Drop one supported file into the active workspace. TechPilot will parse, chunk, persist, and index it through the real ingestion API.", "向当前工作区添加一个支持的文件。TechPilot 会通过真实摄取 API 完成解析、分块、持久化和索引。"],
    ["Workspace-scoped", "工作区隔离"],
    ["Drop a file here", "将文件拖到这里"],
    ["or click to browse", "或点击浏览"],
    ["Choose source", "选择来源"],
    ["Ingesting into workspace", "正在摄取到工作区"],
    ["SOURCE LIBRARY", "来源库"],
    ["Indexed this session", "本次会话已索引"],
    ["Select a source to inspect its index details.", "选择来源查看索引详情。"],
    ["No session sources yet", "本次会话还没有来源"],
    ["Upload a PDF or Markdown file above. Persistent workspace listing will remain explicit until the backend exposes it.", "请在上方上传 PDF 或 Markdown 文件。在后端开放持久来源列表前，界面不会伪造历史来源。"],
    ["CONTEXT", "上下文"],
    ["Evidence", "证据"],
    ["Answer sources", "回答来源"],
    ["Grounded in workspace evidence.Sources shown here support the latest answer returned by the backend.", "基于工作区证据。此处来源用于支持后端返回的最新回答。"],
    ["Active scope", "当前范围"],
    ["Session sources", "会话来源"],
    ["Session chunks", "会话分块"]
  ]);

  const originalText = new WeakMap();
  const originalAttrs = new WeakMap();

  function normalize(text) {
    return (text || "").replace(/\s+/g, " ").trim();
  }

  function translateTextNodes(locale) {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const parent = node.parentElement;
      if (!parent || parent.closest("#tpCloseoutChrome")) continue;
      if (!originalText.has(node)) originalText.set(node, node.nodeValue);
      const source = originalText.get(node);
      if (locale === "en") {
        node.nodeValue = source;
        continue;
      }
      const key = normalize(source);
      const translated = translations.get(key);
      if (translated) node.nodeValue = source.replace(key, translated);
    }
  }

  function translateAttributes(locale) {
    const zh = {
      "Ask a technical question about this workspace…": "向当前工作区提出技术问题…",
      "Question": "问题",
      "Clear conversation": "清空对话",
      "Send question": "发送问题",
      "Primary navigation": "主导航",
      "Product principles": "产品原则",
      "Current workspace context": "当前工作区上下文"
    };
    for (const el of document.querySelectorAll("[placeholder], [aria-label], [title]")) {
      if (!originalAttrs.has(el)) {
        originalAttrs.set(el, {
          placeholder: el.getAttribute("placeholder"),
          ariaLabel: el.getAttribute("aria-label"),
          title: el.getAttribute("title")
        });
      }
      const base = originalAttrs.get(el);
      for (const [attr, value] of [["placeholder", base.placeholder], ["aria-label", base.ariaLabel], ["title", base.title]]) {
        if (value == null) continue;
        el.setAttribute(attr, locale === "zh-CN" && zh[value] ? zh[value] : value);
      }
    }
  }

  function setLocale(locale) {
    const next = locale === "en" ? "en" : "zh-CN";
    localStorage.setItem(LOCALE_KEY, next);
    document.documentElement.lang = next;
    translateTextNodes(next);
    translateAttributes(next);
    const langButton = document.getElementById("tpLangButton");
    if (langButton) langButton.textContent = next === "zh-CN" ? "EN" : "中文";
    const label = document.getElementById("tpDemoLabel");
    if (label) label.textContent = "Portfolio Demo";
  }

  function createChrome() {
    if (document.getElementById("tpCloseoutChrome")) return;
    const chrome = document.createElement("div");
    chrome.id = "tpCloseoutChrome";
    chrome.className = "tp-closeout-chrome";
    chrome.innerHTML = `
      <span class="tp-demo-label" id="tpDemoLabel">Portfolio Demo</span>
      <button class="tp-chrome-button" id="tpLangButton" type="button">EN</button>
      <button class="tp-chrome-button" id="tpLogoutButton" type="button">退出</button>
    `;
    document.body.appendChild(chrome);
    document.getElementById("tpLangButton").addEventListener("click", () => {
      const current = localStorage.getItem(LOCALE_KEY) || DEFAULT_LOCALE;
      setLocale(current === "zh-CN" ? "en" : "zh-CN");
    });
    document.getElementById("tpLogoutButton").addEventListener("click", async () => {
      try {
        await fetch("/auth/logout", {method: "POST"});
      } finally {
        sessionStorage.removeItem(SESSION_KEY);
        window.location.reload();
      }
    });
  }

  function createLogin() {
    if (document.getElementById("tpLoginOverlay")) return;
    const overlay = document.createElement("div");
    overlay.id = "tpLoginOverlay";
    overlay.className = "tp-login-overlay";
    overlay.innerHTML = `
      <section class="tp-login-card" aria-labelledby="tpLoginTitle">
        <div class="tp-login-brand">TechPilot</div>
        <p class="tp-login-kicker">EVIDENCE-GROUNDED AI ENGINEERING</p>
        <h1 id="tpLoginTitle">进入 TechPilot</h1>
        <p class="tp-login-copy">后端身份认证已启用。演示账号仅用于作品集环境，生产环境可关闭。</p>
        <form id="tpLoginForm">
          <label>用户名<input id="tpLoginUser" autocomplete="username" value="demo" /></label>
          <label>密码<input id="tpLoginPassword" type="password" autocomplete="current-password" value="techpilot" /></label>
          <p class="tp-login-hint">Demo：demo / techpilot</p>
          <p class="tp-login-error" id="tpLoginError" aria-live="polite"></p>
          <button type="submit">进入工作台</button>
        </form>
        <button class="tp-login-language" id="tpLoginLanguage" type="button">English</button>
      </section>
    `;
    document.body.appendChild(overlay);
    document.body.classList.add("tp-login-locked");

    const form = document.getElementById("tpLoginForm");
    const error = document.getElementById("tpLoginError");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const user = document.getElementById("tpLoginUser").value.trim();
      const password = document.getElementById("tpLoginPassword").value;
      const submit = form.querySelector("button[type=submit]");
      submit.disabled = true;
      error.textContent = "";
      try {
        const response = await fetch("/auth/token", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({identifier: user, password})
        });
        if (!response.ok) {
          error.textContent = "演示账号或密码不正确。";
          return;
        }
        sessionStorage.setItem(SESSION_KEY, "authenticated");
        window.location.reload();
      } catch (_err) {
        error.textContent = "认证服务暂时不可用。";
      } finally {
        submit.disabled = false;
      }
    });

    document.getElementById("tpLoginLanguage").addEventListener("click", () => {
      const english = overlay.dataset.locale === "en";
      overlay.dataset.locale = english ? "zh-CN" : "en";
      document.getElementById("tpLoginTitle").textContent = english ? "进入 TechPilot" : "Enter TechPilot";
      overlay.querySelector(".tp-login-copy").textContent = english
        ? "后端身份认证已启用。演示账号仅用于作品集环境，生产环境可关闭。"
        : "Backend authentication is enabled. The demo account is portfolio-only and can be disabled in production.";
      const labels = overlay.querySelectorAll("label");
      labels[0].childNodes[0].nodeValue = english ? "用户名" : "Username";
      labels[1].childNodes[0].nodeValue = english ? "密码" : "Password";
      overlay.querySelector(".tp-login-hint").textContent = "Demo: demo / techpilot";
      overlay.querySelector("button[type=submit]").textContent = english ? "进入工作台" : "Enter workspace";
      document.getElementById("tpLoginLanguage").textContent = english ? "English" : "中文";
      error.textContent = "";
    });
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const locale = localStorage.getItem(LOCALE_KEY) || DEFAULT_LOCALE;
    if (sessionStorage.getItem(SESSION_KEY)) {
      try {
        const response = await fetch("/auth/me", {headers: {"Accept": "application/json"}});
        if (response.ok) {
          createChrome();
          setLocale(locale);
          return;
        }
      } catch (_err) {
        // Fall through to the login overlay.
      }
      sessionStorage.removeItem(SESSION_KEY);
    }
    createLogin();
    setLocale(locale);
  });
})();
