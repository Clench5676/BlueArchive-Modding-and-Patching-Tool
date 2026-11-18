// Runs before styles and the module graph. Recovery needs only the built-in document and local RPC.
(function () {
  // biome-ignore lint/suspicious/noRedundantUseStrict: the page injects this file inline as a classic script
  "use strict";
  const config = window.BAMT_CONFIG || {},
    strings = config.strings || {};
  const T = (key) => strings[key] || key;
  const began = Date.now(),
    pending = new Set(),
    requests = new Set(),
    timings = {},
    seen = new Map(),
    sent = [];
  let phase = "modules",
    finished = false,
    failed = false,
    panel = null,
    status = null,
    reporting = null,
    presenter = null;
  let webgl = { supported: null, renderer: null, disabled: !!config.safeMode };
  let importList = () => null;
  const context = () => ({
    page: location.hash || "#/student-list",
    phase,
    timings,
    language: document.documentElement?.lang || config.language,
    batchImport: importList(),
    userAgent: navigator.userAgent,
    screen: { width: screen.width, height: screen.height, scale: devicePixelRatio },
    webgl,
  });
  async function rpc(method, params = {}) {
    const controller = new AbortController(),
      timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch("/api/rpc", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ method, params }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = await response.json();
      if (body.error) throw new Error(body.error.code);
      return body.result;
    } finally {
      clearTimeout(timer);
    }
  }
  function forward(error, kind = "error", source = "") {
    const event = {
      kind,
      message: String(error?.message || error).slice(0, 8000),
      stack: String(error?.stack || "").slice(0, 8000),
      source: String(source).slice(0, 2000),
      page: location.hash,
      phase,
    };
    const key = JSON.stringify(event),
      now = Date.now();
    for (const [k, time] of seen) if (now - time >= 60000) seen.delete(k);
    while (sent.length && now - sent[0] >= 60000) sent.shift();
    if (seen.has(key) || sent.length >= 10) return false;
    seen.set(key, now);
    sent.push(now);
    rpc("app.frontendError", { event, context: context() }).catch(() => {});
    return true;
  }
  const el = (tag, text) => {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    return node;
  };
  function button(parent, key, action) {
    const b = el("button", T(key));
    b.type = "button";
    b.onclick = async () => {
      b.disabled = true;
      try {
        await action();
      } catch (error) {
        status.textContent = T("recovery.actionFailed");
        forward(error, "recovery");
      } finally {
        b.disabled = false;
      }
    };
    parent.appendChild(b);
  }
  function shell(title, hint) {
    if (!document.body) {
      document.addEventListener("DOMContentLoaded", () => shell(title, hint), { once: true });
      return false;
    }
    panel?.remove();
    if (failed || config.safeMode) {
      const stage = document.getElementById?.("stage");
      if (stage) {
        stage.inert = true;
        stage.hidden = true;
      }
    }
    panel = el("section");
    panel.id = "bamt-recovery";
    panel.setAttribute("role", "alert");
    panel.append(el("h1", title), el("p", hint));
    status = el("p");
    status.setAttribute("role", "status");
    panel.appendChild(status);
    document.body.appendChild(panel);
    return true;
  }
  // An upgrade under way: its step lines, the spinner and the percentage. Each poll updates these nodes in place, so
  // the spinner keeps turning and the alert is not announced again.
  let busy = null;
  const percentText = (view) => `${Math.round(view.progress?.percent || 0)}%`;
  function stepItem(item, line) {
    item.className = line.state;
    item.textContent = line.text;
    return item;
  }
  function updateBusy(view) {
    const lines = view.lines || [];
    if (!busy || busy.panel !== panel || !view.busy || view.actions?.length || busy.title !== view.title) return false;
    if (busy.items.length !== lines.length || !lines.every((line) => line.state)) return false;
    busy.hint.textContent = view.hint || "";
    lines.forEach((line, i) => {
      stepItem(busy.items[i], line);
    });
    busy.percent.textContent = percentText(view);
    return true;
  }
  // The data upgrade notice (data-upgrade.js) in the same overlay: its title and hint, its lines (a bulleted list of
  // steps when they carry a state), the spinner of an upgrade under way, and one button per action, each answering
  // choose(id) once.
  function notice(view, choose) {
    if (updateBusy(view)) return true;
    busy = null;
    if (!shell(view.title, view.hint || "")) return false;
    const stage = document.getElementById?.("stage");
    if (stage) stage.inert = true;
    const lines = view.lines || [];
    let items = [];
    if (lines.some((line) => line.state)) {
      const list = el("ul");
      items = lines.map((line) => list.appendChild(stepItem(el("li"), { state: "", ...line })));
      panel.insertBefore(list, status);
    } else for (const line of lines) panel.insertBefore(el("p", line.text), status);
    if (view.busy) {
      const row = el("div"),
        spinner = el("span"),
        percent = el("span", percentText(view));
      row.className = "upgrade-busy";
      spinner.className = "spinner";
      spinner.setAttribute("aria-hidden", "true");
      percent.className = "percent";
      row.append(spinner, percent);
      panel.insertBefore(row, status);
      busy = { panel, title: view.title, hint: panel.children[1], items, percent }; // children: shell's title, hint
    }
    for (const action of view.actions || []) {
      const b = el("button", action.label);
      b.type = "button";
      b.dataset.action = action.id;
      b.onclick = () => {
        for (const other of panel.querySelectorAll("button")) other.disabled = true;
        choose(action.id);
      };
      panel.appendChild(b);
    }
    return true;
  }
  function closeNotice() {
    busy = null;
    panel?.remove();
    panel = null;
    status = null;
    const stage = document.getElementById?.("stage");
    if (stage) stage.inert = false;
  }
  function actions() {
    button(panel, "recovery.report", report);
    button(panel, "recovery.logs", () => rpc("app.logFolder"));
  }
  function showFailure() {
    if (!failed) return;
    if (!document.body) {
      document.addEventListener("DOMContentLoaded", showFailure, { once: true });
      return;
    }
    const separator = /^(zh|ja)/.test(config.language) ? "、" : ", ";
    const step = phase
      .split(", ")
      .map((p) => T(`recovery.phases.${p}`))
      .join(separator);
    shell(T("recovery.failed"), `${T("recovery.step")}: ${step}`);
    actions();
    if (config.canRestartSafe) button(panel, "recovery.safeRestart", () => rpc("app.restartSafe"));
    button(panel, "recovery.retry", () => location.reload());
  }
  function fail(error, kind = "boot", source = "") {
    forward(error, kind, source);
    if (finished || failed) return;
    failed = true;
    clearTimeout(watchdog);
    showFailure();
  }
  async function report(event) {
    if (reporting) return reporting;
    reporting = rpc("app.report", { context: context(), event })
      .then(async (result) => {
        if (presenter && finished && !failed && !config.safeMode) {
          await presenter(result);
          return result;
        }
        if (!panel) shell(T("recovery.report"), "");
        status.textContent = `${T("recovery.saved")}\n${result.path}\n${T(result.copied ? "recovery.copied" : "recovery.copyFailed")}`;
        if (!result.copied) status.textContent += `\n${result.summary}`;
        if (!failed && !config.safeMode)
          button(panel, "picker.ok", () => {
            panel.remove();
            panel = null;
          });
        return result;
      })
      .catch(async (error) => {
        if (presenter && finished && !failed && !config.safeMode) {
          forward(error, "report");
          await presenter(null);
          return;
        }
        if (!panel) shell(T("recovery.report"), "");
        status.textContent = T("recovery.actionFailed");
        if (!failed && !config.safeMode)
          button(panel, "picker.ok", () => {
            panel.remove();
            panel = null;
          });
        forward(error, "report");
      })
      .finally(() => {
        reporting = null;
      });
    return reporting;
  }
  function safeSettings() {
    shell(T("settings.button"), T("recovery.safeHint"));
    actions();
    button(panel, "settings.log.open", async () => {
      const result = await rpc("app.log");
      panel.querySelector("pre")?.remove();
      panel.appendChild(el("pre", result.text));
    });
    button(panel, "picker.ok", safeStart);
  }
  function safeStart() {
    shell(T("recovery.safeOn"), T("recovery.safeHint"));
    actions();
    button(panel, "settings.button", safeSettings);
    location.hash = "#/settings";
    finished = true;
    phase = "ready";
    clearTimeout(watchdog);
    rpc("app.ready", { timings: { boot: Date.now() - began } }).catch(() => {});
  }
  function probe() {
    if (config.safeMode) return;
    try {
      const canvas = document.createElement("canvas"),
        gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
      webgl.supported = !!gl;
      if (!gl) return;
      const debug = gl.getExtension("WEBGL_debug_renderer_info");
      webgl.renderer = gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : gl.RENDERER);
      gl.getExtension("WEBGL_lose_context")?.loseContext();
    } catch (error) {
      webgl = { supported: false, renderer: null, error: String(error) };
    }
  }
  // Held time (hold) is the user's: a question waiting for an answer and the work it starts under its own progress.
  // The watchdog stops for it, and the boot deadline counts only the App's own start-up work.
  let watchdog,
    holds = 0,
    held = 0,
    heldSince = 0;
  const elapsed = () => Date.now() - began - held - (holds ? Date.now() - heldSince : 0);
  function progress() {
    clearTimeout(watchdog);
    if (finished || failed || holds) return;
    const remaining = 180000 - elapsed();
    watchdog = setTimeout(
      () => {
        // A live page awaiting backend work gets grace, but no activity can move the boot deadline.
        if (requests.size && elapsed() < 180000) progress();
        else fail(new Error(`Boot timeout: ${phase}`), "watchdog");
      },
      Math.max(0, Math.min(45000, remaining)),
    );
  }
  progress();
  window.BAMTRecovery = {
    activity() {
      if (elapsed() < 180000) progress();
    },
    hold() {
      if (finished || failed) return () => {};
      if (!holds++) heldSince = Date.now();
      clearTimeout(watchdog);
      let released = false;
      return () => {
        if (released) return;
        released = true;
        if (!--holds) held += Date.now() - heldSince;
        progress();
      };
    },
    request() {
      if (finished || failed) return () => {};
      const token = {};
      requests.add(token);
      progress();
      return () => {
        if (requests.delete(token)) progress();
      };
    },
    importList(read) {
      importList = read;
    },
    report,
    forward,
    fail,
    context,
    notice,
    closeNotice,
    presentReport(fn) {
      presenter = fn;
    },
    isBooting: () => !finished,
    failed: () => failed,
    begin(name) {
      if (finished) return;
      pending.add(name);
      if (name !== "boot") phase = name;
      progress();
    },
    end(name, elapsed) {
      timings[name] = elapsed;
      pending.delete(name);
      progress();
      if (!finished && !failed) phase = [...pending].filter((p) => p !== "boot").join(", ") || name;
    },
    ready() {
      if (failed) {
        panel?.remove();
        panel = null;
        status = null;
        failed = false;
        const stage = document.getElementById?.("stage");
        if (stage) {
          stage.inert = false;
          stage.hidden = false;
        }
      }
      finished = true;
      phase = "ready";
      clearTimeout(watchdog);
      probe();
      rpc("app.frontendError", { context: context() }).catch(() => {});
      return true;
    },
  };
  window.addEventListener(
    "error",
    (event) => {
      const asset = event.target && event.target !== window;
      if (asset && event.target.rel === "icon") return;
      const source = asset ? event.target.src || event.target.href : event.filename;
      const error = event.error || new Error(event.message || `Asset load failed: ${source || "unknown"}`);
      if (!finished && (!asset || !["IMG", "VIDEO", "AUDIO"].includes(event.target.tagName)))
        fail(error, asset ? "asset" : "error", source);
      else forward(error, asset ? "asset" : "error", source);
    },
    true,
  );
  window.addEventListener("unhandledrejection", (event) => {
    if (!finished) fail(event.reason, "rejection");
    else forward(event.reason, "rejection");
  });
  if (config.safeMode) document.addEventListener("DOMContentLoaded", safeStart, { once: true });
})();
