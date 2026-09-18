/**
 * Expired session: never leave FastAPI's JSON 401 on screen.
 * Server already 303s HTML navigations and sends HX-Redirect for HTMX.
 * This covers fetch() / HTMX error swaps that would otherwise paint
 * {"detail":"Please log in to continue"}.
 */
(function () {
  "use strict";

  var LOGIN = "/auth/login";
  var DETAIL = "Please log in to continue";
  var going = false;

  function onLoginPage() {
    try {
      return (window.location.pathname || "").indexOf("/auth/login") === 0;
    } catch (e) {
      return false;
    }
  }

  function goLogin() {
    if (going || onLoginPage()) return;
    going = true;
    window.location.assign(LOGIN);
  }

  function isLoginDetail(body) {
    return !!(body && typeof body.detail === "string" && body.detail === DETAIL);
  }

  document.addEventListener("htmx:beforeSwap", function (evt) {
    var xhr = evt.detail && evt.detail.xhr;
    if (!xhr || xhr.status !== 401) return;
    if (evt.detail) evt.detail.shouldSwap = false;
    goLogin();
  });

  document.addEventListener("htmx:responseError", function (evt) {
    var xhr = evt.detail && evt.detail.xhr;
    if (xhr && xhr.status === 401) goLogin();
  });

  if (!window.fetch) return;
  var orig = window.fetch;
  window.fetch = function () {
    return orig.apply(this, arguments).then(function (res) {
      if (res.status !== 401) return res;
      if (res.headers.get("HX-Redirect")) {
        goLogin();
        return res;
      }
      var ct = (res.headers.get("content-type") || "").toLowerCase();
      if (ct.indexOf("application/json") === -1) return res;
      res
        .clone()
        .json()
        .then(function (body) {
          if (isLoginDetail(body)) goLogin();
        })
        .catch(function () {});
      return res;
    });
  };
})();
