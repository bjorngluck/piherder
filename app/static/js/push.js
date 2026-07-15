/**
 * PiHerder Web Push client — register SW, subscribe, surface status on Account.
 * Safe no-op when push is not configured or browser lacks support.
 *
 * iOS: Push API works only for Home Screen web apps (iOS 16.4+), not Safari tabs.
 */
(function () {
  "use strict";

  function urlBase64ToUint8Array(base64String) {
    var padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    var base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    var raw = atob(base64);
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  function isIOS() {
    var ua = navigator.userAgent || "";
    if (/iphone|ipad|ipod/i.test(ua)) return true;
    // iPadOS 13+ may report as Mac with touch
    return navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1;
  }

  function isStandalone() {
    try {
      if (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) {
        return true;
      }
    } catch (e) {}
    // iOS Safari legacy
    return Boolean(window.navigator.standalone);
  }

  function supported() {
    return (
      "serviceWorker" in navigator &&
      "PushManager" in window &&
      "Notification" in window
    );
  }

  function iosInstallRequired() {
    return isIOS() && !isStandalone();
  }

  async function ensureSw() {
    var reg = await navigator.serviceWorker.getRegistration();
    if (!reg) {
      reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
    }
    return reg;
  }

  async function getPublicKey() {
    var r = await fetch("/api/push/vapid-public-key", { credentials: "same-origin" });
    if (!r.ok) return null;
    var data = await r.json();
    return data.publicKey || null;
  }

  async function subscribe() {
    if (iosInstallRequired()) {
      throw new Error(
        "On iPhone/iPad: open PiHerder from the Home Screen icon first " +
          "(Safari → Share → Add to Home Screen), then try again."
      );
    }
    if (!supported()) {
      if (isIOS()) {
        throw new Error(
          "Push needs iOS 16.4+ and the app installed to the Home Screen (not a Safari tab)."
        );
      }
      throw new Error("Push is not supported in this browser");
    }
    var key = await getPublicKey();
    if (!key) throw new Error("Web Push is not configured on the server");
    var perm = await Notification.requestPermission();
    if (perm !== "granted") throw new Error("Notification permission denied");
    var reg = await ensureSw();
    var sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(key),
    });
    var json = sub.toJSON();
    var body = {
      endpoint: json.endpoint,
      keys: json.keys,
    };
    var r = await fetch("/api/push/subscribe", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      var err = await r.json().catch(function () { return {}; });
      throw new Error(err.detail || "Subscribe failed");
    }
    return true;
  }

  async function unsubscribe() {
    if (!supported()) return false;
    var reg = await ensureSw();
    var sub = await reg.pushManager.getSubscription();
    var endpoint = sub ? sub.endpoint : null;
    if (sub) await sub.unsubscribe();
    await fetch("/api/push/unsubscribe", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint: endpoint }),
    });
    return true;
  }

  window.PiHerderPush = {
    supported: supported,
    subscribe: subscribe,
    unsubscribe: unsubscribe,
    ensureSw: ensureSw,
    isIOS: isIOS,
    isStandalone: isStandalone,
    iosInstallRequired: iosInstallRequired,
  };

  // Always try to register SW for installability (even without push config).
  // Call update() so a new sw.js (cache strategy / shell version) activates promptly.
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker
        .register("/sw.js", { scope: "/" })
        .then(function (reg) {
          try {
            reg.update();
          } catch (e) {}
        })
        .catch(function () {});
    });
  }

  // Account page wire-up
  document.addEventListener("DOMContentLoaded", function () {
    var enableBtn = document.getElementById("push-enable-btn");
    var disableBtn = document.getElementById("push-disable-btn");
    var statusEl = document.getElementById("push-client-status");
    var iosHint = document.getElementById("push-ios-hint");
    if (!enableBtn && !disableBtn) return;

    function setStatus(msg, isErr) {
      if (!statusEl) return;
      statusEl.textContent = msg || "";
      statusEl.className = "text-xs mt-2 " + (isErr ? "text-danger" : "text-muted");
    }

    if (iosHint) {
      if (iosInstallRequired()) {
        iosHint.classList.remove("hidden");
      } else if (isIOS() && isStandalone()) {
        iosHint.classList.add("hidden");
      }
    }

    if (enableBtn) {
      enableBtn.addEventListener("click", function () {
        enableBtn.disabled = true;
        setStatus("Requesting permission…");
        subscribe()
          .then(function () {
            setStatus("This device is subscribed. Reload or save preferences if needed.");
            window.location.href = "/auth/account?msg=push_enabled";
          })
          .catch(function (e) {
            setStatus(e.message || String(e), true);
            enableBtn.disabled = false;
          });
      });
    }
    if (disableBtn) {
      disableBtn.addEventListener("click", function () {
        disableBtn.disabled = true;
        unsubscribe()
          .then(function () {
            window.location.href = "/auth/account?msg=push_disabled";
          })
          .catch(function (e) {
            setStatus(e.message || String(e), true);
            disableBtn.disabled = false;
          });
      });
    }
  });
})();
