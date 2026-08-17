/**
 * Console soft-Tab / IME helpers (v13).
 *
 * Mobile IMEs often hold the current token in the xterm helper textarea
 * until Space/Enter. Soft Tab then either completes against a short line
 * or the IME re-appends the fragment after bash expands a path
 * (``cd do`` → ``cd docker/do``).
 *
 * Pure functions — loaded by server_console.html and unit-tested from Node.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.PhConsoleIme = api;
  }
})(typeof window !== "undefined" ? window : globalThis, function () {
  var TOKEN_RE = /([A-Za-z0-9_./-]+)$/;
  var TLD_DOT_RE = /^\.[A-Za-z]{2,3}$/;
  var HIDDEN_KEEP = {
    ".env": 1,
    ".git": 1,
    ".ssh": 1,
    ".config": 1,
    ".local": 1,
    ".cache": 1,
  };

  function lastToken(raw) {
    var s = String(raw == null ? "" : raw).replace(/\u200b/g, "");
    var m = s.match(TOKEN_RE);
    return m ? m[1] : "";
  }

  /** Token we should flush to the PTY before Tab (strip Gboard ``.do`` TLDs). */
  function imeCompletionToken(raw) {
    var tok = lastToken(raw);
    if (!tok) return "";
    if (HIDDEN_KEEP[tok.toLowerCase()]) return tok;
    if (TLD_DOT_RE.test(tok)) return tok.slice(1);
    return tok;
  }

  /**
   * Android often turns ``cd do`` into ``cd.do`` / ``cd .do`` / ``cd./do``
   * when the IME treats the token as a TLD.
   */
  function normalizeMobileLine(buf) {
    var s = String(buf == null ? "" : buf);
    var m = s.match(/^(cd)\.([^\s./].*)$/i);
    if (m) return m[1] + " " + m[2];
    m = s.match(/^(cd) \.([A-Za-z0-9][^\s]*)$/i);
    if (m) return m[1] + " " + m[2];
    m = s.match(/^(cd)\.\/([^\s].*)$/i);
    if (m) return m[1] + " " + m[2];
    return s;
  }

  function lineAlreadyHasToken(buf, word) {
    if (!word) return true;
    var s = String(buf || "");
    return s.length >= word.length && s.slice(-word.length) === word;
  }

  /**
   * Consume an IME echo after soft Tab.
   * ``state.skip`` is mutated as characters are eaten.
   * Returns true when ``data`` must not go to the PTY.
   */
  function consumeImeEcho(state, data) {
    if (!state || !state.skip) return false;
    var skip = String(state.skip);
    var chunk = String(data == null ? "" : data);
    if (!chunk) return false;

    if (
      chunk === skip ||
      chunk === skip + " " ||
      chunk === "." + skip ||
      chunk === "." + skip + " "
    ) {
      return true;
    }
    var stripped = chunk.replace(/^\.+/, "");
    if (stripped === skip && chunk.length <= skip.length + 4) {
      return true;
    }
    if (skip.indexOf(chunk) === 0) {
      state.skip = skip.slice(chunk.length);
      return true;
    }
    return false;
  }

  /** Text after the last shell prompt marker on a terminal row. */
  function typedFromScreenLine(raw) {
    var s = String(raw == null ? "" : raw).replace(/[\s\u00a0]+$/, "");
    var marks = ["$ ", "# ", "% ", "> "];
    var cut = -1;
    for (var i = 0; i < marks.length; i++) {
      var idx = s.lastIndexOf(marks[i]);
      if (idx >= 0) {
        var at = idx + marks[i].length;
        if (at > cut) cut = at;
      }
    }
    if (cut >= 0) return s.slice(cut);
    return s;
  }

  function hardenTextarea(ta) {
    if (!ta) return;
    try {
      // Kill browser / IME suggestion bars (Gboard, iOS QuickType, Chrome
      // writing-suggestions). A terminal wants raw keystrokes, not completions.
      ta.setAttribute("autocomplete", "off");
      ta.setAttribute("autocorrect", "off");
      ta.setAttribute("autocapitalize", "off");
      ta.setAttribute("spellcheck", "false");
      ta.spellcheck = false;
      ta.setAttribute("writingsuggestions", "false");
      ta.setAttribute("aria-autocomplete", "none");
      ta.setAttribute("inputmode", "text");
      ta.setAttribute("enterkeyhint", "enter");
      ta.setAttribute("data-gramm", "false");
      ta.setAttribute("data-lpignore", "true");
      ta.setAttribute("data-1p-ignore", "true");
      ta.setAttribute("data-form-type", "other");
      ta.setAttribute("name", "ph-console-pty");
      // No linguistic content — reduces IME prediction / language model.
      ta.setAttribute("lang", "zxx");
    } catch (e) {}
  }

  /**
   * xterm paints IME text in ``.composition-view`` at the cursor. After Tab,
   * bash has already moved the cursor (``docker/``) so the leftover
   * composition looks like ``docker/doc`` even when the PTY line is correct.
   */
  function hideCompositionView(term) {
    var root = term && (term.element || term);
    if (!root || !root.querySelectorAll) return;
    var nodes = root.querySelectorAll(".composition-view");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      el.classList.remove("active");
      el.textContent = "";
      try {
        el.style.display = "none";
      } catch (e) {}
    }
  }

  /** True when IME overlay text is the fragment we already sent / completed. */
  function compositionLooksLikeSkip(skip, data) {
    if (!skip) return false;
    var chunk = String(data == null ? "" : data);
    if (!chunk) return true;
    return (
      chunk === skip ||
      skip.indexOf(chunk) === 0 ||
      chunk.indexOf(skip) !== -1
    );
  }

  return {
    lastToken: lastToken,
    imeCompletionToken: imeCompletionToken,
    normalizeMobileLine: normalizeMobileLine,
    lineAlreadyHasToken: lineAlreadyHasToken,
    consumeImeEcho: consumeImeEcho,
    typedFromScreenLine: typedFromScreenLine,
    hardenTextarea: hardenTextarea,
    hideCompositionView: hideCompositionView,
    compositionLooksLikeSkip: compositionLooksLikeSkip,
    VERSION: 14,
  };
});
