/**
 * Runtime stack panel — themed modal (same family as job-hold / wait modal).
 */
(function () {
  'use strict';

  function drawer() {
    return document.getElementById('fabric-stack-drawer');
  }
  function bodyEl() {
    return document.getElementById('fabric-stack-panel-body');
  }

  var lastOpenAt = 0;
  var lastOpenUrl = '';

  function loadingHtml() {
    return (
      '<div class="fabric-stack-loading">' +
      '<div class="fabric-stack-loading-spin" aria-hidden="true"></div>' +
      '<p class="fabric-stack-loading-title">Loading stack…</p>' +
      '<p class="fabric-stack-loading-sub">Reading inventory and links</p>' +
      '</div>'
    );
  }

  function errorHtml(msg, url) {
    var safeUrl = (url || '').replace(/"/g, '');
    return (
      '<div class="banner-error border rounded p-3 text-sm">' +
      '<p class="mb-1 font-medium">' +
      (msg || 'Failed to load stack.') +
      '</p>' +
      (safeUrl
        ? '<a class="text-accent hover:underline text-xs" href="' +
          safeUrl +
          '">Open raw response</a>'
        : '') +
      '</div>'
    );
  }

  function openDrawer() {
    var d = drawer();
    if (!d) return;
    d.hidden = false;
    d.removeAttribute('hidden');
    d.classList.add('is-open');
    d.setAttribute('aria-hidden', 'false');
    d.style.display = 'flex';
    d.style.pointerEvents = 'auto';
    document.body.classList.add('fabric-stack-open');
    document.body.style.overflow = 'hidden';
  }

  function closeDrawer() {
    var d = drawer();
    if (!d) return;
    d.classList.remove('is-open');
    d.hidden = true;
    d.setAttribute('hidden', '');
    d.setAttribute('aria-hidden', 'true');
    d.style.display = 'none';
    d.style.pointerEvents = 'none';
    document.body.classList.remove('fabric-stack-open');
    document.body.style.overflow = '';
  }

  function initSortable(root) {
    var list = (root || bodyEl() || document).querySelector('[data-stack-sortable]');
    if (!list || list.dataset.sortableInit === '1') return;
    if (list.getAttribute('data-can-reorder') !== '1') return;
    list.dataset.sortableInit = '1';

    var dragEl = null;
    var longPressTimer = null;
    var longPressReady = false;
    var touchActive = false;
    var pointerId = null;
    var startY = 0;
    var startX = 0;
    var orderDirty = false;
    var suppressClickUntil = 0;
    var saving = false;
    var lastOrder = collectOrderSnapshot();

    function items() {
      return Array.prototype.slice.call(
        list.querySelectorAll(':scope > li[data-stack-container]')
      );
    }

    function collectOrderSnapshot() {
      return items()
        .map(function (li) {
          return li.getAttribute('data-stack-container') || '';
        })
        .filter(Boolean);
    }

    function clearDragOver() {
      items().forEach(function (li) {
        li.classList.remove('is-drag-over');
      });
    }

    function renumberBadges() {
      items().forEach(function (li, i) {
        var badge = li.querySelector('[data-stack-order-badge]');
        if (badge) badge.textContent = String(i + 1);
      });
    }

    /**
     * Y-based insert (not elementFromPoint).
     * On touch, the finger always sits on dragEl — elementFromPoint never hits
     * neighbours, so DOM order never changed and save bailed as "unchanged".
     */
    function moveDragToClientY(clientY) {
      if (!dragEl || !list.contains(dragEl)) return false;
      var others = items().filter(function (li) {
        return li !== dragEl;
      });
      if (!others.length) return false;

      var insertBeforeEl = null;
      for (var i = 0; i < others.length; i++) {
        var rect = others[i].getBoundingClientRect();
        if (clientY < rect.top + rect.height / 2) {
          insertBeforeEl = others[i];
          break;
        }
      }

      var before = collectOrderSnapshot().join('\0');
      if (insertBeforeEl) {
        if (dragEl.nextSibling !== insertBeforeEl) {
          list.insertBefore(dragEl, insertBeforeEl);
        }
      } else {
        var last = others[others.length - 1];
        if (last && dragEl.previousSibling !== last) {
          list.insertBefore(dragEl, last.nextSibling);
        }
      }
      var after = collectOrderSnapshot().join('\0');
      if (before !== after) {
        orderDirty = true;
        renumberBadges();
        return true;
      }
      return false;
    }

    function flashSaved() {
      list.classList.add('is-order-saved');
      setTimeout(function () {
        list.classList.remove('is-order-saved');
      }, 900);
    }

    function afterSaveOk() {
      flashSaved();
      var sid = list.getAttribute('data-service-id');
      var vs = list.getAttribute('data-visual-stack') || 'all';
      if (window.PiHerderStackExpand && window.PiHerderStackExpand.invalidate) {
        window.PiHerderStackExpand.invalidate(sid || null);
        if (sid && window.PiHerderStackExpand.show) {
          try {
            window.PiHerderStackExpand.show(sid, vs);
          } catch (err) {}
        }
      }
      // Soft reload panel so "custom order on" + server order stick
      var url = panelUrlFromList(list);
      setTimeout(function () {
        loadStack(url);
      }, 120);
    }

    function panelUrlFromList(el) {
      var q = el.getAttribute('data-service-id');
      var vs = el.getAttribute('data-visual-stack') || 'all';
      var url = q
        ? '/dns/stack-panel?service_id=' + encodeURIComponent(q)
        : '/dns/stack-panel?server_id=' +
          encodeURIComponent(el.getAttribute('data-server-id') || '') +
          '&project=' +
          encodeURIComponent(el.getAttribute('data-project') || '');
      if (vs && vs !== 'all') url += '&visual_stack=' + encodeURIComponent(vs);
      return url;
    }

    function buildOrderForm(order) {
      var fd = new FormData();
      fd.append('server_id', list.getAttribute('data-server-id') || '');
      fd.append('project', list.getAttribute('data-project') || '');
      fd.append('order', JSON.stringify(order));
      fd.append('service_id', list.getAttribute('data-service-id') || '');
      fd.append('next', list.getAttribute('data-next') || '/dns');
      return fd;
    }

    function saveOrder() {
      var order = collectOrderSnapshot();
      if (!order.length) return;
      if (!orderDirty && order.join('\0') === lastOrder.join('\0')) return;
      if (saving) return;
      saving = true;
      orderDirty = false;
      var prevOrder = lastOrder.slice();
      lastOrder = order.slice();

      fetch('/dns/stack-order', {
        method: 'POST',
        body: buildOrderForm(order),
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      })
        .then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          // Prefer JSON body when present
          var ct = (r.headers.get('content-type') || '').toLowerCase();
          if (ct.indexOf('application/json') >= 0) {
            return r.json().then(function (data) {
              if (data && data.ok === false) throw new Error(data.error || 'save failed');
            });
          }
        })
        .then(function () {
          afterSaveOk();
        })
        .catch(function () {
          lastOrder = prevOrder;
          orderDirty = true;
          list.classList.add('is-order-error');
          setTimeout(function () {
            list.classList.remove('is-order-error');
          }, 1200);
        })
        .then(function () {
          saving = false;
        });
    }

    function endTouchDrag(shouldSave) {
      if (longPressTimer) {
        clearTimeout(longPressTimer);
        longPressTimer = null;
      }
      var wasReady = longPressReady;
      var dirty = orderDirty;
      if (dragEl) {
        dragEl.classList.remove('is-dragging');
        clearDragOver();
      }
      dragEl = null;
      longPressReady = false;
      pointerId = null;
      touchActive = false;
      list.classList.remove('is-reordering');
      if (wasReady) {
        // swallow the synthetic click that would open <details>
        suppressClickUntil = Date.now() + 450;
      }
      if (shouldSave && wasReady && dirty) {
        saveOrder();
      } else if (shouldSave && wasReady) {
        // Long-press engaged but no move — still nothing to save
        renumberBadges();
      }
    }

    // Desktop HTML5: drag from ⋮⋮ handle only
    list.addEventListener('dragstart', function (e) {
      // Avoid fighting touch long-press on hybrid devices
      if (touchActive) {
        e.preventDefault();
        return;
      }
      var handle = e.target.closest('[data-stack-drag-handle]');
      var li = e.target.closest('li[data-stack-container]');
      if (!li || !list.contains(li) || !handle) {
        e.preventDefault();
        return;
      }
      dragEl = li;
      longPressReady = true;
      orderDirty = false;
      li.classList.add('is-dragging');
      list.classList.add('is-reordering');
      try {
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', li.getAttribute('data-stack-container') || '');
        e.dataTransfer.setDragImage(li, 24, 20);
      } catch (err) {}
    });

    list.addEventListener('dragover', function (e) {
      if (!dragEl || touchActive) return;
      e.preventDefault();
      try {
        e.dataTransfer.dropEffect = 'move';
      } catch (err) {}
      moveDragToClientY(e.clientY);
    });

    list.addEventListener('dragend', function () {
      if (touchActive) return;
      if (dragEl) {
        dragEl.classList.remove('is-dragging');
        clearDragOver();
      }
      dragEl = null;
      longPressReady = false;
      list.classList.remove('is-reordering');
      if (orderDirty) saveOrder();
    });

    list.addEventListener('drop', function (e) {
      e.preventDefault();
    });

    // Block summary toggle immediately after a long-press gesture
    list.addEventListener(
      'click',
      function (e) {
        if (Date.now() < suppressClickUntil) {
          e.preventDefault();
          e.stopPropagation();
        }
      },
      true
    );

    // Touch: long-press row (or handle), then drag by Y
    list.addEventListener(
      'touchstart',
      function (e) {
        // Don't start reorder from pure links/buttons inside expanded detail
        if (e.target.closest('a, button, input, select, textarea')) return;
        var li = e.target.closest('li[data-stack-container]');
        if (!li || !list.contains(li)) return;
        var t = e.changedTouches[0];
        if (!t) return;
        touchActive = true;
        pointerId = t.identifier;
        startY = t.clientY;
        startX = t.clientX;
        longPressReady = false;
        orderDirty = false;
        if (longPressTimer) clearTimeout(longPressTimer);
        longPressTimer = setTimeout(function () {
          longPressReady = true;
          dragEl = li;
          // Disable native HTML5 drag while touch-reordering
          items().forEach(function (row) {
            row.setAttribute('draggable', 'false');
          });
          li.classList.add('is-dragging');
          list.classList.add('is-reordering');
          if (navigator.vibrate) {
            try {
              navigator.vibrate(12);
            } catch (err) {}
          }
        }, 320);
      },
      { passive: true }
    );

    list.addEventListener(
      'touchmove',
      function (e) {
        var t = null;
        for (var i = 0; i < e.changedTouches.length; i++) {
          if (e.changedTouches[i].identifier === pointerId) {
            t = e.changedTouches[i];
            break;
          }
        }
        if (!t && e.touches.length) t = e.touches[0];
        if (!t) return;

        if (!longPressReady || !dragEl) {
          // Cancel pending long-press if the finger clearly scrolled
          if (longPressTimer) {
            if (
              Math.abs(t.clientY - startY) > 12 ||
              Math.abs(t.clientX - startX) > 12
            ) {
              clearTimeout(longPressTimer);
              longPressTimer = null;
              touchActive = false;
            }
          }
          return;
        }
        // Once armed, own the gesture — no page/drawer scroll
        e.preventDefault();
        moveDragToClientY(t.clientY);
      },
      { passive: false }
    );

    function onTouchEnd(e) {
      // Only end our gesture
      if (pointerId != null && e.changedTouches) {
        var ours = false;
        for (var i = 0; i < e.changedTouches.length; i++) {
          if (e.changedTouches[i].identifier === pointerId) {
            ours = true;
            break;
          }
        }
        if (!ours && longPressReady) return;
      }
      var shouldSave = e.type === 'touchend';
      endTouchDrag(shouldSave);
      // Restore HTML5 drag for desktop after touch sequence
      items().forEach(function (row) {
        row.setAttribute('draggable', 'true');
      });
    }

    list.addEventListener('touchend', onTouchEnd);
    list.addEventListener('touchcancel', onTouchEnd);
  }

  function loadStack(url) {
    if (!url) return;
    var body = bodyEl();
    if (!body) {
      window.location.href = url;
      return;
    }

    var now = Date.now();
    if (url === lastOpenUrl && now - lastOpenAt < 350) return;
    lastOpenUrl = url;
    lastOpenAt = now;

    openDrawer();
    body.innerHTML = loadingHtml();

    fetch(url, {
      credentials: 'same-origin',
      headers: { Accept: 'text/html', 'HX-Request': 'true' },
    })
      .then(function (r) {
        if (r.status === 401 || r.status === 403) {
          throw new Error('Not signed in (HTTP ' + r.status + ')');
        }
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      })
      .then(function (html) {
        body.innerHTML =
          html ||
          '<p class="text-sm text-muted mb-0">Empty response.</p>';
        initSortable(body);
        initAjaxForms(body);
        syncMapFromPanel(body);
        // Panel is injected via fetch, not HTMX swap — rebind hx-* if present
        if (window.htmx && typeof window.htmx.process === 'function') {
          try {
            window.htmx.process(body);
          } catch (err) {
            /* ignore */
          }
        }
        lastOpenUrl = '';
        lastOpenAt = 0;
      })
      .catch(function (e) {
        body.innerHTML = errorHtml(
          e && e.message ? e.message : 'Failed to load stack.',
          url
        );
      });
  }

  function stackUrlFromButton(btn) {
    if (!btn) return '';
    var url = (btn.getAttribute('data-stack-url') || '').trim();
    if (url) return url;
    if (btn.hasAttribute('data-fabric-stack-from-focus')) {
      var root = document.querySelector('[data-fabric-root]');
      var fid =
        root && root._fabricFocusId != null ? String(root._fabricFocusId) : '';
      if (fid && fid.indexOf('n:') !== 0 && /^\d+$/.test(fid)) {
        return '/dns/stack-panel?service_id=' + encodeURIComponent(fid);
      }
    }
    return '';
  }

  function handleEvent(e) {
    var t = e.target;
    if (!t || !t.closest) return false;

    var closeBtn = t.closest('[data-fabric-stack-close]');
    if (closeBtn) {
      e.preventDefault();
      e.stopPropagation();
      closeDrawer();
      return true;
    }

    // View-group switches inside the loaded panel (not HTMX-bound)
    var body = bodyEl();
    var panelNav = t.closest('[data-stack-url]');
    if (
      panelNav &&
      body &&
      body.contains(panelNav) &&
      !panelNav.hasAttribute('data-fabric-stack-open')
    ) {
      e.preventDefault();
      e.stopPropagation();
      var navUrl = (panelNav.getAttribute('data-stack-url') || '').trim();
      if (navUrl) {
        loadStack(navUrl);
        // Map follows the same view-group filter (All = multi-fan when assigned)
        try {
          var u = new URL(navUrl, window.location.origin);
          var sid =
            u.searchParams.get('service_id') ||
            (body.querySelector('[data-service-id]') &&
              body.querySelector('[data-service-id]').getAttribute('data-service-id'));
          var vs = u.searchParams.get('visual_stack') || 'all';
          if (sid && window.PiHerderStackExpand) {
            window.PiHerderStackExpand.invalidate(sid);
            window.PiHerderStackExpand.show(sid, vs);
          }
        } catch (err) {}
      }
      return true;
    }

    var openBtn = t.closest('[data-fabric-stack-open]');
    if (openBtn) {
      e.preventDefault();
      e.stopPropagation();
      var url = stackUrlFromButton(openBtn);
      if (url) loadStack(url);
      return true;
    }
    return false;
  }

  document.addEventListener(
    'click',
    function (e) {
      handleEvent(e);
    },
    true
  );

  document.addEventListener('keydown', function (e) {
    if (
      e.key === 'Escape' &&
      drawer() &&
      drawer().classList.contains('is-open')
    ) {
      closeDrawer();
    }
  });

  closeDrawer();

  function bootFromQuery() {
    try {
      var params = new URLSearchParams(window.location.search || '');
      var sid = params.get('stack');
      var srv = params.get('stack_server');
      var proj = params.get('stack_project');
      if (sid) {
        loadStack('/dns/stack-panel?service_id=' + encodeURIComponent(sid));
      } else if (srv) {
        var u = '/dns/stack-panel?server_id=' + encodeURIComponent(srv);
        if (proj) u += '&project=' + encodeURIComponent(proj);
        loadStack(u);
      }
    } catch (err) {
      /* ignore */
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootFromQuery);
  } else {
    bootFromQuery();
  }

  function syncMapFromPanel(root) {
    try {
      var list =
        (root || bodyEl() || document).querySelector('[data-stack-sortable]') ||
        (root || bodyEl() || document).querySelector('.fabric-stack-viewgroups');
      if (!list) return;
      var sid =
        list.getAttribute('data-service-id') ||
        (root &&
          root.querySelector &&
          root.querySelector('[data-service-id]') &&
          root.querySelector('[data-service-id]').getAttribute('data-service-id'));
      var vs =
        list.getAttribute('data-visual-stack') ||
        list.getAttribute('data-active-visual') ||
        'all';
      if (sid && window.PiHerderStackExpand) {
        window.PiHerderStackExpand.invalidate(sid);
        window.PiHerderStackExpand.show(sid, vs);
      }
    } catch (err) {}
  }

  function initAjaxForms(root) {
    var scope = root || bodyEl() || document;
    scope.querySelectorAll('form[data-stack-ajax-form="1"]').forEach(function (form) {
      if (form.dataset.ajaxBound === '1') return;
      form.dataset.ajaxBound = '1';
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        e.stopPropagation();
        var fd = new FormData(form);
        var action = form.getAttribute('action') || '';
        fetch(action, {
          method: 'POST',
          body: fd,
          credentials: 'same-origin',
          headers: { Accept: 'application/json' },
        })
          .then(function (r) {
            return r.json().then(function (data) {
              if (!r.ok || (data && data.ok === false)) {
                throw new Error((data && data.error) || 'Save failed');
              }
              return data;
            });
          })
          .then(function () {
            var list = scope.querySelector('[data-stack-sortable]') || form;
            var sid =
              (scope.querySelector('[data-service-id]') &&
                scope.querySelector('[data-service-id]').getAttribute('data-service-id')) ||
              '';
            var vs =
              (scope.querySelector('[data-visual-stack]') &&
                scope.querySelector('[data-visual-stack]').getAttribute('data-visual-stack')) ||
              (scope.querySelector('[data-active-visual]') &&
                scope
                  .querySelector('[data-active-visual]')
                  .getAttribute('data-active-visual')) ||
              'all';
            var url;
            if (sid) {
              url =
                '/dns/stack-panel?service_id=' +
                encodeURIComponent(sid) +
                (vs && vs !== 'all' ? '&visual_stack=' + encodeURIComponent(vs) : '');
            } else {
              var srv =
                fd.get('server_id') ||
                (list && list.getAttribute('data-server-id')) ||
                '';
              var proj =
                fd.get('project') ||
                (list && list.getAttribute('data-project')) ||
                '';
              url =
                '/dns/stack-panel?server_id=' +
                encodeURIComponent(srv) +
                '&project=' +
                encodeURIComponent(proj) +
                (vs && vs !== 'all' ? '&visual_stack=' + encodeURIComponent(vs) : '');
            }
            if (sid && window.PiHerderStackExpand) {
              window.PiHerderStackExpand.invalidate(sid);
            }
            loadStack(url);
          })
          .catch(function (err) {
            alert(err && err.message ? err.message : 'Save failed');
          });
      });
    });
  }

  window.PiHerderStackPanel = {
    open: loadStack,
    close: closeDrawer,
  };
})();
