(() => {
  "use strict";

  const SESSION_KEY = "techpilot_demo_session";
  const LOCALE_KEY = "techpilot_locale";

  function locale() {
    return localStorage.getItem(LOCALE_KEY) === "en" ? "en" : "zh-CN";
  }

  function copy(zh, en) {
    return locale() === "en" ? en : zh;
  }

  function describeRegistrationError(status, payload) {
    if (status === 409) {
      return copy("这个邮箱已经注册，请直接登录。", "This email is already registered. Sign in instead.");
    }
    if (status === 422) {
      return copy("请检查邮箱格式和密码长度。", "Check the email format and password length.");
    }
    if (payload && typeof payload.detail === "string") {
      return payload.detail;
    }
    return copy("注册失败，请稍后重试。", "Registration failed. Try again.");
  }

  function installRegistration(overlay) {
    if (!overlay || overlay.dataset.registrationInstalled === "true") return;

    const card = overlay.querySelector(".tp-login-card");
    const loginForm = overlay.querySelector("#tpLoginForm");
    const title = overlay.querySelector("#tpLoginTitle");
    const loginCopy = overlay.querySelector(".tp-login-copy");
    const languageButton = overlay.querySelector("#tpLoginLanguage");
    if (!card || !loginForm || !title || !loginCopy) return;

    overlay.dataset.registrationInstalled = "true";
    overlay.dataset.authMode = "login";

    const switcher = document.createElement("div");
    switcher.className = "tp-auth-switch";
    switcher.setAttribute("role", "tablist");
    switcher.setAttribute("aria-label", "Authentication mode");
    switcher.innerHTML = `
      <button id="tpLoginMode" class="active" type="button" role="tab" aria-selected="true">登录</button>
      <button id="tpRegisterMode" type="button" role="tab" aria-selected="false">注册</button>
    `;
    card.insertBefore(switcher, loginForm);

    const registerForm = document.createElement("form");
    registerForm.id = "tpRegisterForm";
    registerForm.className = "tp-register-form";
    registerForm.hidden = true;
    registerForm.innerHTML = `
      <label><span data-register-label="email">邮箱</span><input id="tpRegisterEmail" type="email" autocomplete="email" maxlength="320" required /></label>
      <label><span data-register-label="password">密码</span><input id="tpRegisterPassword" type="password" autocomplete="new-password" minlength="8" maxlength="128" required /></label>
      <label><span data-register-label="confirm">确认密码</span><input id="tpRegisterConfirm" type="password" autocomplete="new-password" minlength="8" maxlength="128" required /></label>
      <p class="tp-login-hint" id="tpRegisterHint">使用邮箱注册，密码至少 8 位。</p>
      <p class="tp-login-error" id="tpRegisterError" aria-live="polite"></p>
      <button type="submit" id="tpRegisterSubmit">创建账号并进入</button>
    `;
    loginForm.insertAdjacentElement("afterend", registerForm);

    const loginMode = switcher.querySelector("#tpLoginMode");
    const registerMode = switcher.querySelector("#tpRegisterMode");
    const registerError = registerForm.querySelector("#tpRegisterError");

    function renderMode(mode) {
      const isRegister = mode === "register";
      overlay.dataset.authMode = isRegister ? "register" : "login";
      loginForm.hidden = isRegister;
      registerForm.hidden = !isRegister;
      loginMode.classList.toggle("active", !isRegister);
      registerMode.classList.toggle("active", isRegister);
      loginMode.setAttribute("aria-selected", String(!isRegister));
      registerMode.setAttribute("aria-selected", String(isRegister));

      const english = locale() === "en";
      loginMode.textContent = english ? "Sign in" : "登录";
      registerMode.textContent = english ? "Create account" : "注册";

      if (isRegister) {
        title.textContent = english ? "Create your TechPilot account" : "创建 TechPilot 账号";
        loginCopy.textContent = english
          ? "Create a separate account to verify user and workspace isolation. Registration signs you in immediately."
          : "创建独立账号，用于真实验证用户与工作区隔离。注册成功后会自动登录。";
        registerForm.querySelector('[data-register-label="email"]').textContent = english ? "Email" : "邮箱";
        registerForm.querySelector('[data-register-label="password"]').textContent = english ? "Password" : "密码";
        registerForm.querySelector('[data-register-label="confirm"]').textContent = english ? "Confirm password" : "确认密码";
        registerForm.querySelector("#tpRegisterHint").textContent = english
          ? "Register with email. Password must be at least 8 characters."
          : "使用邮箱注册，密码至少 8 位。";
        registerForm.querySelector("#tpRegisterSubmit").textContent = english
          ? "Create account and enter"
          : "创建账号并进入";
      }
    }

    loginMode.addEventListener("click", () => {
      registerError.textContent = "";
      renderMode("login");
    });

    registerMode.addEventListener("click", () => {
      const loginError = overlay.querySelector("#tpLoginError");
      if (loginError) loginError.textContent = "";
      renderMode("register");
      registerForm.querySelector("#tpRegisterEmail").focus();
    });

    registerForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      registerError.textContent = "";

      const email = registerForm.querySelector("#tpRegisterEmail").value.trim();
      const password = registerForm.querySelector("#tpRegisterPassword").value;
      const confirm = registerForm.querySelector("#tpRegisterConfirm").value;
      const submit = registerForm.querySelector("#tpRegisterSubmit");

      if (password.length < 8) {
        registerError.textContent = copy("密码至少需要 8 位。", "Password must be at least 8 characters.");
        return;
      }
      if (password !== confirm) {
        registerError.textContent = copy("两次输入的密码不一致。", "The passwords do not match.");
        return;
      }

      submit.disabled = true;
      try {
        const response = await fetch("/auth/register", {
          method: "POST",
          credentials: "same-origin",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({email, password})
        });
        let payload = null;
        try {
          payload = await response.json();
        } catch (_err) {
          payload = null;
        }
        if (!response.ok) {
          registerError.textContent = describeRegistrationError(response.status, payload);
          return;
        }
        sessionStorage.setItem(SESSION_KEY, "authenticated");
        window.location.reload();
      } catch (_err) {
        registerError.textContent = copy("认证服务暂时不可用。", "Authentication service is temporarily unavailable.");
      } finally {
        submit.disabled = false;
      }
    });

    if (languageButton) {
      languageButton.addEventListener("click", () => {
        window.setTimeout(() => renderMode(overlay.dataset.authMode || "login"), 0);
      });
    }

    renderMode("login");
  }

  function watchForLoginOverlay() {
    installRegistration(document.getElementById("tpLoginOverlay"));
    const observer = new MutationObserver(() => {
      const overlay = document.getElementById("tpLoginOverlay");
      if (overlay) installRegistration(overlay);
    });
    observer.observe(document.body, {childList: true, subtree: true});
  }

  async function restoreCookieSession() {
    if (sessionStorage.getItem(SESSION_KEY)) return;
    try {
      const response = await fetch("/auth/me", {
        credentials: "same-origin",
        headers: {"Accept": "application/json"}
      });
      if (!response.ok) return;
      sessionStorage.setItem(SESSION_KEY, "authenticated");
      window.location.reload();
    } catch (_err) {
      // The normal login overlay remains available.
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    watchForLoginOverlay();
    void restoreCookieSession();
  });
})();
