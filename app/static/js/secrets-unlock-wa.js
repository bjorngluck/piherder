/**
 * Secrets step-up via WebAuthn (passkey).
 * Buttons: class="js-secrets-unlock-passkey" data-return-to="..."
 * Optional error target: element with class "js-secrets-unlock-wa-error" in the same panel.
 */
(function () {
  if (window.__piSecretsUnlockWaBound) return;
  window.__piSecretsUnlockWaBound = true;

  function b64urlToBuf(b64url) {
    var pad = "=".repeat((4 - (b64url.length % 4)) % 4);
    var b64 = (b64url + pad).replace(/-/g, "+").replace(/_/g, "/");
    var str = atob(b64);
    var buf = new ArrayBuffer(str.length);
    var view = new Uint8Array(buf);
    for (var i = 0; i < str.length; i++) view[i] = str.charCodeAt(i);
    return buf;
  }

  function bufToB64url(buf) {
    var bytes = new Uint8Array(buf);
    var str = "";
    for (var i = 0; i < bytes.length; i++) str += String.fromCharCode(bytes[i]);
    return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function prepareRequestOptions(publicKey) {
    var o = Object.assign({}, publicKey);
    o.challenge = b64urlToBuf(publicKey.challenge);
    if (publicKey.allowCredentials) {
      o.allowCredentials = publicKey.allowCredentials.map(function (c) {
        return Object.assign({}, c, { id: b64urlToBuf(c.id) });
      });
    }
    return o;
  }

  function credentialToJSON(cred) {
    var r = cred.response;
    return {
      id: cred.id,
      rawId: bufToB64url(cred.rawId),
      type: cred.type,
      clientExtensionResults: cred.clientExtensionResults || {},
      authenticatorAttachment: cred.authenticatorAttachment || null,
      response: {
        clientDataJSON: bufToB64url(r.clientDataJSON),
        authenticatorData: bufToB64url(r.authenticatorData),
        signature: bufToB64url(r.signature),
        userHandle: r.userHandle ? bufToB64url(r.userHandle) : null,
      },
    };
  }

  function findErrorEl(btn) {
    var panel = btn.closest(".js-secrets-unlock-panel") || btn.parentElement;
    if (!panel) return null;
    return panel.querySelector(".js-secrets-unlock-wa-error");
  }

  function showErr(el, msg) {
    if (!el) return;
    el.textContent = msg || "Passkey failed";
    el.classList.remove("hidden");
  }

  document.addEventListener("click", function (ev) {
    var btn = ev.target && ev.target.closest && ev.target.closest(".js-secrets-unlock-passkey");
    if (!btn) return;
    ev.preventDefault();

    var errEl = findErrorEl(btn);
    if (errEl) {
      errEl.classList.add("hidden");
      errEl.textContent = "";
    }

    if (!window.PublicKeyCredential) {
      showErr(errEl, "Passkeys not supported in this browser.");
      return;
    }

    var returnTo = btn.getAttribute("data-return-to") || window.location.pathname + window.location.search;
    var prev = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Waiting for authenticator…";

    (async function () {
      try {
        var optRes = await fetch("/templates/secrets/webauthn/options", {
          method: "POST",
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        var optBody = await optRes.json();
        if (!optBody.ok) throw new Error(optBody.error || "Could not start passkey unlock");
        if (!optBody.publicKey) throw new Error("Passkey options missing from server");

        var assertion = await navigator.credentials.get({
          publicKey: prepareRequestOptions(optBody.publicKey),
        });
        if (!assertion) throw new Error("Passkey cancelled");

        var verRes = await fetch("/templates/secrets/webauthn/verify", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({
            credential: credentialToJSON(assertion),
            return_to: returnTo,
          }),
        });
        var verBody = await verRes.json();
        if (!verBody.ok) throw new Error(verBody.error || "Passkey verification failed");
        window.location.href = verBody.redirect || returnTo;
      } catch (e) {
        var msg = String((e && e.message) || e || "Passkey failed");
        if (msg.indexOf("NotAllowedError") >= 0 || /cancel|not allowed/i.test(msg)) {
          msg = "Passkey cancelled or not allowed in this window.";
        }
        showErr(errEl, msg);
        btn.disabled = false;
        btn.textContent = prev;
      }
    })();
  });
})();
