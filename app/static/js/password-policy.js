/** Live password policy meter + generate (Settings → Security). */
(function (global) {
  function readPolicy() {
    if (global.PIHERDER_PASSWORD_POLICY) return global.PIHERDER_PASSWORD_POLICY;
    var el = document.getElementById('piherder-password-policy');
    if (el && el.textContent) {
      try {
        global.PIHERDER_PASSWORD_POLICY = JSON.parse(el.textContent);
      } catch (e) {
        global.PIHERDER_PASSWORD_POLICY = null;
      }
    }
    return global.PIHERDER_PASSWORD_POLICY || {
      min_length: 10,
      max_length: 72,
      require_upper: true,
      require_lower: true,
      require_digit: true,
      require_special: false,
    };
  }

  function missing(password) {
    var p = readPolicy();
    var pwd = password || '';
    var need = [];
    if (!pwd) return ['a password'];
    var bytes = 0;
    try {
      bytes = new TextEncoder().encode(pwd).length;
    } catch (e) {
      bytes = pwd.length;
    }
    if (bytes > (p.max_length || 72)) need.push('at most ' + p.max_length + ' characters');
    if (pwd.length < (p.min_length || 10)) need.push('min ' + p.min_length + ' characters');
    if (p.require_upper && !/[A-Z]/.test(pwd)) need.push('uppercase');
    if (p.require_lower && !/[a-z]/.test(pwd)) need.push('lowercase');
    if (p.require_digit && !/[0-9]/.test(pwd)) need.push('digit');
    if (p.require_special && !/[^A-Za-z0-9]/.test(pwd)) need.push('special character');
    return need;
  }

  function score(password) {
    var p = readPolicy();
    var pwd = password || '';
    var labels = ['very weak', 'weak', 'fair', 'good', 'strong'];
    if (!pwd) return { score: 0, label: 'empty', ok: false, missing: missing(pwd) };
    var s = 0;
    var minL = p.min_length || 10;
    if (pwd.length >= minL) s += 1;
    if (pwd.length >= Math.max(14, minL + 4)) s += 1;
    var c = 0;
    if (/[a-z]/.test(pwd)) c++;
    if (/[A-Z]/.test(pwd)) c++;
    if (/[0-9]/.test(pwd)) c++;
    if (/[^A-Za-z0-9]/.test(pwd)) c++;
    if (c >= 3) s += 1;
    if (c >= 4 && pwd.length >= minL) s += 1;
    s = Math.min(4, s);
    var need = missing(pwd);
    var ok = need.length === 0;
    if (!ok && s > 2) s = 2;
    return { score: s, label: labels[s] || 'weak', ok: ok, missing: need };
  }

  function generate(len) {
    var p = readPolicy();
    var minL = p.min_length || 10;
    var cap = Math.min(p.max_length || 48, 48);
    len = Math.max(minL, Math.min(len || Math.max(16, minL), cap));
    var lower = 'abcdefghjkmnpqrstuvwxyz';
    var upper = 'ABCDEFGHJKLMNPQRSTUVWXYZ';
    var digits = '23456789';
    var specials = '!@#$%^&*_-+=?';
    var all = lower + upper + digits + specials;
    function rnd(max) {
      if (global.crypto && crypto.getRandomValues) {
        var buf = new Uint32Array(1);
        crypto.getRandomValues(buf);
        return buf[0] % max;
      }
      return Math.floor(Math.random() * max);
    }
    var arr = [
      lower[rnd(lower.length)],
      upper[rnd(upper.length)],
      digits[rnd(digits.length)],
      specials[rnd(specials.length)],
    ];
    if (len < arr.length) arr = arr.slice(0, len);
    while (arr.length < len) arr.push(all[rnd(all.length)]);
    for (var i = arr.length - 1; i > 0; i--) {
      var j = rnd(i + 1);
      var t = arr[i];
      arr[i] = arr[j];
      arr[j] = t;
    }
    return arr.join('');
  }

  function meterFor(input) {
    if (!input) return null;
    var id = input.id;
    if (id) {
      var named = document.querySelector('[data-pw-meter][data-pw-for="' + id + '"]');
      if (named) return named;
    }
    var root = input.closest('[data-pw-field]') || input.parentElement;
    if (root) return root.querySelector('[data-pw-meter]');
    return null;
  }

  function paint(input) {
    var meter = meterFor(input);
    if (!meter) return;
    var fill = meter.querySelector('[data-pw-fill]') || meter.querySelector('.pw-strength-fill');
    var label = meter.querySelector('[data-pw-label]');
    var r = score(input.value || '');
    if (fill) {
      fill.style.width = (r.score * 25) + '%';
      fill.dataset.score = String(r.score);
    }
    if (label) {
      if (!input.value) {
        label.textContent = 'Enter or generate a password';
      } else if (r.ok) {
        label.textContent = 'Strength: ' + r.label;
      } else {
        label.textContent = 'Strength: ' + r.label + ' — needs ' + r.missing.join(', ');
      }
    }
  }

  function bindInput(input) {
    if (!input || input.dataset.pwBound === '1') return;
    input.dataset.pwBound = '1';
    var p = readPolicy();
    if (p.min_length) input.setAttribute('minlength', String(p.min_length));
    if (p.max_length) input.setAttribute('maxlength', String(p.max_length));
    input.addEventListener('input', function () { paint(input); });
    paint(input);
  }

  function bindGenerate(btn) {
    if (!btn || btn.dataset.pwBound === '1') return;
    btn.dataset.pwBound = '1';
    btn.addEventListener('click', function () {
      var id = btn.getAttribute('data-pw-generate');
      var input = id ? document.getElementById(id) : null;
      if (!input) {
        var form = btn.closest('form');
        input = form ? form.querySelector('[data-pw-input]') : null;
      }
      if (!input) return;
      input.value = generate();
      if (input.type === 'password') input.type = 'text';
      paint(input);
      input.focus();
      if (typeof input.select === 'function') input.select();
    });
  }

  function bindForm(form) {
    if (!form || form.dataset.pwBound === '1') return;
    form.dataset.pwBound = '1';
    form.addEventListener('submit', function (e) {
      var input = form.querySelector('[data-pw-input]');
      if (!input) return;
      var r = score(input.value || '');
      if (!r.ok) {
        e.preventDefault();
        paint(input);
        input.focus();
      }
    });
  }

  function bind(root) {
    var scope = root || document;
    scope.querySelectorAll('[data-pw-input]').forEach(bindInput);
    scope.querySelectorAll('[data-pw-generate]').forEach(bindGenerate);
    scope.querySelectorAll('form[data-pw-form]').forEach(bindForm);
  }

  global.PiHerderPassword = {
    policy: readPolicy,
    missing: missing,
    score: score,
    generate: generate,
    bind: bind,
    paint: paint,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { bind(); });
  } else {
    bind();
  }
})(window);
