/* CSP Slice 2: delegated stand-in for inline event handlers.
   Actions are data-ph-* attributes. Names are an allowlist. No eval. */
(function () {
  var ALLOW = {
    showEditServerModal: 1,
    showSshAccessModal: 1,
    openPiHerderConsolePopup: 1,
    showSystemInfoModal: 1,
    openBackupLog: 1,
    stopCurrentBackup: 1,
    showOsPatchModal: 1,
    showJobDetailsModal: 1,
    hideJobDetailsModal: 1,
    hideSshAccessModal: 1,
    copyTextFrom: 1,
    downloadTextAsFile: 1,
    showBackupConfigModal: 1,
    hideBackupConfigModal: 1,
    showRemoveBackupModal: 1,
    hideRemoveBackupModal: 1,
    confirmRemoveBackupSource: 1,
    hideEditServerModal: 1,
    hideOsPatchModal: 1,
    updateCronBuilder: 1,
    hideOsPatchProgressModal: 1,
    hideBackupDetailsModal: 1,
    refreshSystemInfo: 1,
    hideSystemInfoModal: 1,
    hideCleanupModal: 1,
    loadUnused: 1,
    hideBuildModal: 1,
    startBuildFromModal: 1,
    hideTemplateEditGate: 1,
    hideQuickEditModal: 1,
    switchQuickEditTab: 1,
    goToFullEditor: 1,
    quickSaveDraft: 1,
    quickSaveDeploy: 1,
    hideUndeployConfirm: 1,
    hideRemoveProjectConfirm: 1,
    hideDeployConfirm: 1,
    hideLifecycleConfirm: 1,
    hideHostLockModal: 1,
    loadLogsForModal: 1,
    expandLogsToFullScreen: 1,
    closeLogsModal: 1,
    hideServiceSelectModal: 1,
    showCleanupModal: 1,
    startLiveStream: 1,
    stopLiveStream: 1,
    clearLogs: 1,
    toggleAutoScroll: 1,
    openDnsRecordsModal: 1,
    openDnsHubModal: 1,
    closeDnsHubModal: 1,
    showBackupCodesModal: 1,
    hideBackupCodesModal: 1,
    show2faSetupModal: 1,
    hide2faSetupModal: 1,
    copyTwofaSecret: 1,
    wizardCopyTextFrom: 1,
    toggleWizardAuth: 1,
    toggleAuthFields: 1,
    showAuditDetailsModal: 1,
    hideAuditDetailsModal: 1
  };
  var LISTS = { JOBS: 1, AUDIT_LOGS: 1 };
  var SET_KEYS = { activePollJobId: 1, activePollSource: 1 };
  var ACTION_ATTRS = [
    "data-ph-click",
    "data-ph-seq",
    "data-ph-toggle",
    "data-ph-reload",
    "data-ph-href",
    "data-ph-show",
    "data-ph-hide",
    "data-ph-backdrop",
    "data-ph-backdrop-call",
    "data-ph-backdrop-href",
    "data-ph-select",
    "data-ph-copy-attr",
    "data-ph-scroll",
    "data-ph-stack",
    "data-ph-set",
    "data-ph-then-scroll"
  ];
  var ID_RE = /^[A-Za-z][A-Za-z0-9_-]*$/;
  var armedStops = typeof WeakSet === "function" ? new WeakSet() : null;

  function isAction(el) {
    for (var i = 0; i < ACTION_ATTRS.length; i++) {
      if (el.hasAttribute(ACTION_ATTRS[i])) return true;
    }
    return false;
  }

  function elementFrom(ev) {
    var t = ev.target;
    if (!t) return null;
    if (t.nodeType === 1) return t;
    return t.parentElement || null;
  }

  function actionHost(start) {
    var n = start;
    while (n && n !== document) {
      if (n.hasAttribute("data-ph-stop") && !isAction(n)) return null;
      if (isAction(n)) return n;
      n = n.parentElement;
    }
    return null;
  }

  function callFn(name, args) {
    if (!ALLOW[name]) return undefined;
    var fn = window[name];
    if (typeof fn !== "function") return undefined;
    return fn.apply(window, args || []);
  }

  function mapArgs(raw, el, ev) {
    var out = [];
    for (var i = 0; i < raw.length; i++) {
      if (raw[i] === "$el") out.push(el);
      else if (raw[i] === "$event") out.push(ev);
      else out.push(raw[i]);
    }
    return out;
  }

  function readArgs(el, ev) {
    var raw = el.getAttribute("data-ph-args");
    if (!raw) return [];
    try {
      var parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return mapArgs(parsed, el, ev);
    } catch (e) {
      return [];
    }
  }

  function applySet(el) {
    var raw = el.getAttribute("data-ph-set");
    if (!raw) return;
    try {
      var obj = JSON.parse(raw);
      if (!obj || typeof obj !== "object") return;
      Object.keys(obj).forEach(function (k) {
        if (SET_KEYS[k]) window[k] = obj[k];
      });
    } catch (e) {}
  }

  function runSeq(el, ev) {
    var raw = el.getAttribute("data-ph-seq");
    if (!raw) return;
    try {
      var steps = JSON.parse(raw);
      if (!Array.isArray(steps)) return;
      steps.forEach(function (step) {
        if (!step || !step.fn) return;
        callFn(step.fn, mapArgs(step.args || [], el, ev));
      });
    } catch (e) {}
  }

  function readList(name) {
    // Top-level const is not a property of window. These two pages declare
    // JOBS and AUDIT_LOGS that way. Name them here. No eval.
    if (!LISTS[name]) return null;
    if (name === "JOBS") return typeof JOBS !== "undefined" ? JOBS : null;
    if (name === "AUDIT_LOGS") return typeof AUDIT_LOGS !== "undefined" ? AUDIT_LOGS : null;
    return null;
  }

  function runClick(el, ev) {
    if (el.hasAttribute("data-ph-click")) {
      var name = el.getAttribute("data-ph-click");
      var args;
      if (el.hasAttribute("data-ph-pass-event")) args = [ev];
      else if (el.hasAttribute("data-ph-pass-el")) args = [el];
      else if (el.hasAttribute("data-ph-list")) {
        var listName = el.getAttribute("data-ph-list");
        var idx = parseInt(el.getAttribute("data-ph-index") || "", 10);
        var list = readList(listName);
        if (!list || !isFinite(idx) || idx < 0 || idx >= list.length) return;
        args = [list[idx]];
      } else args = readArgs(el, ev);
      var result = callFn(name, args);
      if (el.hasAttribute("data-ph-pass-event") && result === false) ev.preventDefault();
    }
  }

  function hideShow(el, attr, hide) {
    var id = el.getAttribute(attr);
    if (!ID_RE.test(id || "")) return;
    var node = document.getElementById(id);
    if (!node) return;
    if (hide) {
      node.classList.add("hidden");
      node.classList.remove("flex");
    } else {
      node.classList.remove("hidden");
      node.classList.add("flex");
    }
  }

  function safeHref(url) {
    if (!url || url.charAt(0) !== "?") return false;
    if (url.indexOf(":") !== -1 || url.indexOf("//") !== -1) return false;
    return true;
  }

  function thenScroll(el) {
    var id = el.getAttribute("data-ph-then-scroll");
    if (!id || !ID_RE.test(id)) return;
    setTimeout(function () {
      var node = document.getElementById(id);
      if (node) node.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 50);
  }

  function copyAttr(el) {
    var t = el.getAttribute("data-copy-text") || "";
    if (!t) return;
    function done(ok) {
      el.textContent = ok ? "Copied" : "Copy";
      setTimeout(function () { el.textContent = "Copy"; }, 1200);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(t).then(function () { done(true); }).catch(function () { done(false); });
    } else {
      done(false);
    }
  }

  function openStack(el, ev) {
    ev.preventDefault();
    ev.stopPropagation();
    var attr = el.getAttribute("data-ph-stack") || "data-stack-url";
    if (attr !== "data-stack-url" && attr !== "data-host-ports-url") return;
    var u = el.getAttribute(attr);
    if (u && window.PiHerderStackPanel && typeof window.PiHerderStackPanel.open === "function") {
      window.PiHerderStackPanel.open(u);
    }
  }

  function handleClick(el, ev) {
    applySet(el);
    runSeq(el, ev);
    runClick(el, ev);
    if (el.hasAttribute("data-ph-toggle")) {
      var tid = el.getAttribute("data-ph-toggle");
      if (ID_RE.test(tid || "")) {
        var panel = document.getElementById(tid);
        if (panel) panel.classList.toggle("hidden");
      }
    }
    if (el.hasAttribute("data-ph-show")) hideShow(el, "data-ph-show", false);
    if (el.hasAttribute("data-ph-hide")) hideShow(el, "data-ph-hide", true);
    if (el.hasAttribute("data-ph-reload")) window.location.reload();
    if (el.hasAttribute("data-ph-href")) {
      var href = el.getAttribute("data-ph-href");
      if (safeHref(href)) window.location.href = href;
    }
    if (ev.target === el) {
      if (el.hasAttribute("data-ph-backdrop")) {
        el.classList.add("hidden");
        el.classList.remove("flex");
      }
      if (el.hasAttribute("data-ph-backdrop-call")) {
        callFn(el.getAttribute("data-ph-backdrop-call"), readArgs(el, ev));
      }
      if (el.hasAttribute("data-ph-backdrop-href")) {
        var bh = el.getAttribute("data-ph-backdrop-href");
        if (safeHref(bh)) window.location.href = bh;
      }
    }
    if (el.hasAttribute("data-ph-select") && typeof el.select === "function") el.select();
    if (el.hasAttribute("data-ph-copy-attr")) copyAttr(el);
    if (el.hasAttribute("data-ph-scroll")) {
      var sel = el.getAttribute("data-ph-scroll") || "";
      if (/^\[[A-Za-z0-9_-]+=[A-Za-z0-9_-]+\]$/.test(sel)) {
        var box = document.querySelector(sel);
        if (box) box.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }
    if (el.hasAttribute("data-ph-stack")) openStack(el, ev);
    if (el.hasAttribute("data-ph-then-scroll")) thenScroll(el);
  }

  document.addEventListener("click", function (ev) {
    var start = elementFrom(ev);
    if (!start) return;
    var host = actionHost(start);
    if (!host) return;
    handleClick(host, ev);
  }, true);

  function armStop(el) {
    if (!el || el.nodeType !== 1 || !el.hasAttribute("data-ph-stop")) return;
    if (armedStops) {
      if (armedStops.has(el)) return;
      armedStops.add(el);
    } else if (el.getAttribute("data-ph-stop-armed") === "1") {
      return;
    } else {
      el.setAttribute("data-ph-stop-armed", "1");
    }
    el.addEventListener("click", function (e) { e.stopPropagation(); });
  }

  function scanStops(root) {
    if (!root || !root.querySelectorAll) return;
    if (root.nodeType === 1) armStop(root);
    var list = root.querySelectorAll("[data-ph-stop]");
    for (var i = 0; i < list.length; i++) armStop(list[i]);
  }

  function fillNamed(el) {
    var name = el.getAttribute("data-ph-fill");
    if (!name || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) || !el.value || !el.form) return;
    var target = el.form.querySelector('[name="' + name + '"]');
    if (target) target.value = el.value;
  }

  function fillById(el) {
    var id = el.getAttribute("data-ph-fill-id");
    if (!id || !ID_RE.test(id) || !el.value) return;
    var target = document.getElementById(id);
    if (!target) return;
    target.value = el.value;
    if (el.hasAttribute("data-ph-fill-dispatch")) {
      target.dispatchEvent(new Event("input"));
    }
  }

  document.addEventListener("change", function (ev) {
    var el = ev.target;
    if (!el || !el.getAttribute) return;
    if (el.hasAttribute("data-ph-change")) {
      var name = el.getAttribute("data-ph-change");
      if (el.getAttribute("data-ph-change-arg") === "int") {
        callFn(name, [parseInt(el.value, 10)]);
      } else {
        callFn(name, []);
      }
    }
    if (el.hasAttribute("data-ph-fill")) fillNamed(el);
    if (el.hasAttribute("data-ph-fill-id")) fillById(el);
    if (el.hasAttribute("data-ph-change-submit") && el.form) el.form.submit();
    if (el.hasAttribute("data-ph-change-request") && el.form && el.form.requestSubmit) el.form.requestSubmit();
    if (el.hasAttribute("data-ph-query")) {
      var key = el.getAttribute("data-ph-query");
      if (key === "refresh") {
        var u = new URL(window.location.href);
        u.searchParams.set("refresh", el.value);
        window.location.href = u.toString();
      }
    }
    if (el.hasAttribute("data-ph-disable-checked")) {
      var did = el.getAttribute("data-ph-disable-checked");
      var node = ID_RE.test(did || "") ? document.getElementById(did) : null;
      if (node) node.disabled = !!el.checked;
    }
    if (el.hasAttribute("data-ph-clear-flag") && el.form) {
      var flag = el.getAttribute("data-ph-clear-flag");
      if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(flag || "")) {
        var hidden = el.form.querySelector('[name="' + flag + '"]');
        if (hidden) hidden.value = el.value ? "0" : "1";
      }
    }
    if (el.hasAttribute("data-ph-vol-hint")) {
      var wrap = el.closest ? el.closest("div") : null;
      var hint = wrap && wrap.parentElement ? wrap.parentElement.querySelector("[data-vol-hint]") : null;
      if (hint) {
        hint.textContent = el.value === "named"
          ? "Docker named volume"
          : el.value === "bind_relative"
            ? "Folder under the project (./…)"
            : "Absolute path on the host";
      }
    }
  }, false);

  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (!form || !form.getAttribute) return;
    if (form.hasAttribute("data-ph-nosubmit")) {
      ev.preventDefault();
      return;
    }
    if (form.hasAttribute("data-ph-confirm")) {
      var msg = form.getAttribute("data-ph-confirm") || "";
      if (!window.confirm(msg)) ev.preventDefault();
    }
  }, true);

  window.addEventListener("error", function (ev) {
    var el = ev.target;
    if (!el || el === window || !el.getAttribute) return;
    if (!el.hasAttribute("data-ph-img-fallback")) return;
    el.style.display = "none";
    if (el.nextElementSibling) el.nextElementSibling.style.display = "flex";
  }, true);

  scanStops(document);
  document.addEventListener("htmx:afterSwap", function (ev) {
    if (ev.target) scanStops(ev.target);
  });
  if (window.MutationObserver && document.documentElement) {
    var mo = new MutationObserver(function (recs) {
      for (var i = 0; i < recs.length; i++) {
        var nodes = recs[i].addedNodes;
        for (var j = 0; j < nodes.length; j++) scanStops(nodes[j]);
      }
    });
    mo.observe(document.documentElement, { childList: true, subtree: true });
  }
})();
