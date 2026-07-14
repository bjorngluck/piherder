/**
 * DNS fabric mesh: path focus, mobile graph toggle, filters,
 * pinch/wheel zoom + drag pan, copy path.
 * No external deps — works with server-rendered SVG + list markup.
 */
(function () {
  'use strict';

  function pathIdsFromEl(el) {
    if (!el) return [];
    var multi = el.getAttribute('data-path-ids');
    if (multi) {
      return multi.split(/[\s,]+/).map(function (s) { return s.trim(); }).filter(Boolean);
    }
    var one = el.getAttribute('data-path-id');
    return one ? [String(one)] : [];
  }

  function nodeIdFromEl(el) {
    if (!el) return '';
    return (el.getAttribute('data-node-id') || '').trim();
  }

  /** Focus keys: path ids are plain; node keys are "n:<node_id>". */
  function isNodeFocusId(id) {
    return String(id || '').indexOf('n:') === 0;
  }

  function nodeFocusKey(nodeId) {
    return nodeId ? 'n:' + nodeId : '';
  }

  function elMatchesPath(el, pathId) {
    if (pathId == null || pathId === '') return false;
    var ids = pathIdsFromEl(el);
    var want = String(pathId);
    for (var i = 0; i < ids.length; i++) {
      if (ids[i] === want) return true;
    }
    return false;
  }

  function elMatchesFocus(el, focusId) {
    if (focusId == null || focusId === '') return false;
    var want = String(focusId);
    if (isNodeFocusId(want)) {
      var nid = nodeIdFromEl(el);
      return nid && want === nodeFocusKey(nid);
    }
    return elMatchesPath(el, want);
  }

  function focusableIn(root) {
    return root.querySelectorAll(
      '[data-path-id], [data-path-ids], [data-node-id], .fabric-mesh-edge, .fabric-mesh-node, ' +
      '.fabric-path-card, .fabric-flow, .fabric-rack, .fabric-app-chip'
    );
  }

  function clearFocus(root) {
    root.classList.remove('is-focusing');
    root._fabricFocusId = null;
    focusableIn(root).forEach(function (el) {
      el.classList.remove('ph-focus-active', 'ph-focus-dim');
    });
    var callout = root.querySelector('[data-fabric-callout]');
    if (callout) {
      callout.textContent = callout.getAttribute('data-fabric-callout-empty') || '';
      callout.classList.add('is-empty');
    }
    var clearBtn = root.querySelector('[data-fabric-clear-focus]');
    if (clearBtn) clearBtn.classList.add('hidden');
    var copyCallout = root.querySelector('[data-fabric-copy-callout]');
    if (copyCallout) {
      copyCallout.classList.add('hidden');
      copyCallout.removeAttribute('data-copy-text');
    }
    var openLink = root.querySelector('[data-fabric-open-link]');
    if (openLink) {
      openLink.href = '#';
      openLink.classList.add('hidden');
      openLink.setAttribute('hidden', '');
      openLink.textContent = openLink.getAttribute('data-default-label') || 'Open host';
    }
  }

  function setCallout(root, text, openHref, openLabel) {
    var callout = root.querySelector('[data-fabric-callout]');
    var copyCallout = root.querySelector('[data-fabric-copy-callout]');
    var openLink = root.querySelector('[data-fabric-open-link]');
    if (callout) {
      if (text) {
        callout.textContent = text;
        callout.classList.remove('is-empty');
      } else {
        callout.textContent = callout.getAttribute('data-fabric-callout-empty') || '';
        callout.classList.add('is-empty');
      }
    }
    if (copyCallout) {
      if (text) {
        copyCallout.classList.remove('hidden');
        copyCallout.setAttribute('data-copy-text', text);
      } else {
        copyCallout.classList.add('hidden');
        copyCallout.removeAttribute('data-copy-text');
      }
    }
    if (openLink) {
      if (!openLink.getAttribute('data-default-label')) {
        openLink.setAttribute('data-default-label', openLink.textContent || 'Open host');
      }
      if (openHref && openHref !== '#' && openHref.indexOf('javascript:') !== 0) {
        openLink.href = openHref;
        openLink.textContent = openLabel || openLink.getAttribute('data-default-label') || 'Open host';
        // External (Kuma) → new tab; same-origin host pages → same tab
        if (/^https?:\/\//i.test(openHref)) {
          openLink.setAttribute('target', '_blank');
          openLink.setAttribute('rel', 'noopener');
        } else {
          openLink.removeAttribute('target');
          openLink.removeAttribute('rel');
        }
        openLink.classList.remove('hidden');
        openLink.removeAttribute('hidden');
      } else {
        openLink.href = '#';
        openLink.classList.add('hidden');
        openLink.setAttribute('hidden', '');
        openLink.removeAttribute('target');
        openLink.textContent = openLink.getAttribute('data-default-label') || 'Open host';
      }
    }
  }

  function openHrefForFocus(root, focusId) {
    if (focusId == null || focusId === '') return { href: '', label: '' };
    var want = String(focusId);
    if (isNodeFocusId(want)) {
      var nid = want.slice(2);
      var node = root.querySelector('[data-node-id="' + nid.replace(/"/g, '') + '"]');
      if (!node) return { href: '', label: '' };
      var nh =
        node.getAttribute('data-open-href') ||
        (function () {
          var a = node.closest('a[href]');
          return a ? a.getAttribute('href') : '';
        })() ||
        '';
      return {
        href: nh && nh !== '#' ? nh : '',
        label: node.getAttribute('data-open-label') || '',
      };
    }
    var hostHref = '';
    var anyHref = '';
    var openLabel = '';
    root.querySelectorAll('[data-path-id], [data-path-ids]').forEach(function (el) {
      if (!elMatchesPath(el, want)) return;
      var href =
        el.getAttribute('data-open-href') ||
        (function () {
          var a = el.closest('a[href]');
          return a ? a.getAttribute('href') : '';
        })() ||
        '';
      if (!href || href === '#') return;
      if (
        el.classList.contains('fabric-mesh-node--host') ||
        (el.closest && el.closest('.fabric-mesh-node--host'))
      ) {
        hostHref = href;
        openLabel = el.getAttribute('data-open-label') || 'Open host';
      } else if (!anyHref) {
        anyHref = href;
        openLabel = el.getAttribute('data-open-label') || 'Open host';
      }
    });
    return { href: hostHref || anyHref || '', label: openLabel || 'Open host' };
  }

  // Back-compat alias
  function openHrefForPath(root, pathId) {
    return openHrefForFocus(root, pathId).href;
  }

  function focusPath(root, pathId, chainText, force) {
    if (pathId == null || pathId === '') {
      clearFocus(root);
      return;
    }
    var idStr = String(pathId);
    var openMeta = openHrefForFocus(root, pathId);
    // Skip full DOM pass when already focused on this path (stops hover flicker)
    if (
      !force &&
      root._fabricFocusId === idStr &&
      root.classList.contains('is-focusing')
    ) {
      if (chainText) setCallout(root, chainText, openMeta.href, openMeta.label);
      return;
    }

    root.classList.add('is-focusing');
    root._fabricFocusId = idStr;
    var found = false;
    focusableIn(root).forEach(function (el) {
      var hasPath =
        el.hasAttribute('data-path-id') || el.hasAttribute('data-path-ids');
      var hasNode = el.hasAttribute('data-node-id');
      if (!hasPath && !hasNode) {
        if (el.classList.contains('fabric-mesh-edge') || el.classList.contains('fabric-mesh-node')) {
          el.classList.remove('ph-focus-active');
          el.classList.add('ph-focus-dim');
        }
        return;
      }
      var on = elMatchesFocus(el, pathId);
      el.classList.toggle('ph-focus-active', on);
      el.classList.toggle('ph-focus-dim', !on);
      if (on) {
        found = true;
        if (!chainText) {
          chainText =
            el.getAttribute('data-path-chain') ||
            el.getAttribute('data-path-title') ||
            chainText;
        }
      }
    });
    if (!found) {
      clearFocus(root);
      return;
    }
    setCallout(
      root,
      chainText || (isNodeFocusId(idStr) ? idStr.slice(2) : 'Path #' + pathId),
      openMeta.href,
      openMeta.label
    );
    var clearBtn = root.querySelector('[data-fabric-clear-focus]');
    if (clearBtn) clearBtn.classList.remove('hidden');
  }

  function copyText(text, btn) {
    if (!text) return;
    function done(ok) {
      if (!btn) return;
      var prev = btn.getAttribute('data-label') || btn.textContent;
      if (!btn.getAttribute('data-label')) btn.setAttribute('data-label', prev);
      btn.textContent = ok ? 'Copied' : 'Failed';
      setTimeout(function () {
        btn.textContent = btn.getAttribute('data-label') || 'Copy path';
      }, 1400);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, function () {
        fallbackCopy(text, done);
      });
    } else {
      fallbackCopy(text, done);
    }
  }

  function fallbackCopy(text, done) {
    try {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(ta);
      done(ok);
    } catch (err) {
      done(false);
    }
  }

  function initCopy(root) {
    root.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-fabric-copy-path], [data-fabric-copy-callout]');
      if (!btn || !root.contains(btn)) return;
      e.preventDefault();
      e.stopPropagation();
      var text =
        btn.getAttribute('data-copy-text') ||
        (function () {
          var card = btn.closest('[data-path-chain]');
          return card ? card.getAttribute('data-path-chain') : '';
        })();
      copyText((text || '').trim(), btn);
    });
  }

  function isInMesh(el) {
    return !!(el && el.closest && el.closest(
      '[data-fabric-mesh], [data-fabric-viewport], .fabric-mesh-svg, .fabric-mesh-stage'
    ));
  }

  function pathTargetFrom(el) {
    if (!el || !el.closest) return null;
    return el.closest('[data-path-id], [data-path-ids], [data-node-id]');
  }

  /**
   * Resolve what to focus under a click/hover target.
   * - App / path chips → path id (service mapping)
   * - Host / Router / LAN / Internet → node id (always, even with zero apps)
   * Hosts prefer node focus so Nomad etc. work without mapped services.
   */
  function focusKeyFrom(el) {
    if (!el) return null;
    var isApp =
      el.classList.contains('fabric-mesh-node--app') ||
      el.classList.contains('fabric-app-chip') ||
      el.classList.contains('fabric-path-card') ||
      el.classList.contains('fabric-flow') ||
      (el.closest && (
        el.closest('.fabric-mesh-node--app') ||
        el.closest('.fabric-app-chip') ||
        el.closest('.fabric-path-card') ||
        el.closest('.fabric-flow')
      ));
    if (isApp) {
      var pids = pathIdsFromEl(el);
      if (pids.length) {
        return { id: pids[0], chain: chainOfEl(el) };
      }
    }
    var nid = nodeIdFromEl(el);
    if (nid) {
      return { id: nodeFocusKey(nid), chain: chainOfEl(el) };
    }
    var ids = pathIdsFromEl(el);
    if (ids.length) {
      return { id: ids[0], chain: chainOfEl(el) };
    }
    return null;
  }

  function chainOfEl(el) {
    return (
      (el && (el.getAttribute('data-path-chain') || el.getAttribute('data-path-title'))) ||
      ''
    );
  }

  function initFocusRoot(root) {
    if (!root || root.dataset.fabricFocusInit === '1') return;
    root.dataset.fabricFocusInit = '1';

    var locked = null; // string focus id when tap-locked
    var hoverId = null; // string focus id under fine pointer
    // Touch: pointer capture retargets events to the viewport — remember the target under the finger
    var touchDown = null; // { id, chain, pointerId, inMesh, focusClick }
    var lastLockAt = 0;
    // After a finger touch, browsers fire synthetic mouse events — ignore them for a bit
    var ignoreMouseUntil = 0;

    function chainOf(el) {
      return chainOfEl(el);
    }

    function markTouchActivity() {
      ignoreMouseUntil = Date.now() + 1000;
    }

    function isHoverCapable(e) {
      // Never hover-preview for finger; also ignore ghost mouse after touch
      if (e.pointerType === 'touch') return false;
      if (Date.now() < ignoreMouseUntil) return false;
      return e.pointerType === 'mouse' || e.pointerType === 'pen' || e.pointerType === '';
    }

    function lockPath(pathId, chain, opts) {
      opts = opts || {};
      if (pathId == null || pathId === '') {
        locked = null;
        hoverId = null;
        clearFocus(root);
        return;
      }
      var id = String(pathId);
      if (locked === id) {
        // Second intentional tap clears — but ignore double-fire within 700ms
        // (ghost click after touch would otherwise unlock immediately)
        if (!opts.forceToggle && Date.now() - lastLockAt < 700) {
          return;
        }
        locked = null;
        hoverId = null;
        clearFocus(root);
        return;
      }
      locked = id;
      hoverId = id;
      lastLockAt = Date.now();
      focusPath(root, id, chain, true);
    }

    function hoverPath(pathId, chain) {
      if (locked) return; // locked wins until clear
      if (pathId == null || pathId === '') {
        if (hoverId != null) {
          hoverId = null;
          clearFocus(root);
        }
        return;
      }
      var id = String(pathId);
      if (hoverId === id && root._fabricFocusId === id) return;
      hoverId = id;
      focusPath(root, id, chain, false);
    }

    // --- Hover preview (real mouse / stylus only) ---
    root.addEventListener('pointerover', function (e) {
      if (!isHoverCapable(e)) return;
      if (locked) return;
      var t = pathTargetFrom(e.target);
      if (!t || !root.contains(t)) return;
      var key = focusKeyFrom(t);
      if (!key) return;
      if (hoverId === key.id) return;
      hoverPath(key.id, key.chain);
    });

    root.addEventListener('pointerout', function (e) {
      if (!isHoverCapable(e)) return;
      if (locked) return;
      var from = pathTargetFrom(e.target);
      if (!from) return;
      var related = e.relatedTarget;
      if (related && root.contains(related)) {
        var to = pathTargetFrom(related);
        if (to) {
          var toKey = focusKeyFrom(to);
          var fromKey = focusKeyFrom(from);
          if (toKey && fromKey && toKey.id === fromKey.id) return;
          return;
        }
        hoverPath(null, null);
        return;
      }
      hoverPath(null, null);
    });

    // Capture-phase click: map path/node focus. Never steal chrome controls.
    root.addEventListener(
      'click',
      function (e) {
        // Explicit chrome — leave alone (View full map, zoom, copy, Open host, forms…)
        if (
          e.target.closest(
            '[data-fabric-open-graph], [data-fabric-close-graph], ' +
            '[data-fabric-zoom-in], [data-fabric-zoom-out], [data-fabric-zoom-reset], ' +
            '[data-fabric-copy-path], [data-fabric-copy-callout], [data-fabric-open-link], ' +
            'input, select, textarea, summary, label'
          )
        ) {
          return;
        }

        var clear = e.target.closest('[data-fabric-clear-focus]');
        if (clear) {
          e.preventDefault();
          e.stopPropagation();
          locked = null;
          hoverId = null;
          lastLockAt = 0;
          clearFocus(root);
          return;
        }

        var vp = e.target.closest('[data-fabric-viewport]');
        if (vp && (vp.dataset.fabricSuppressClick === '1' || vp.dataset.fabricTouchMoved === '1')) {
          e.preventDefault();
          e.stopPropagation();
          return;
        }

        // Ghost click after touch: only suppress map path interactions
        if (root._fabricTouchHandled) {
          if (isInMesh(e.target) || pathTargetFrom(e.target)) {
            e.preventDefault();
            e.stopPropagation();
          }
          return;
        }
        if (Date.now() < ignoreMouseUntil && isInMesh(e.target)) {
          e.preventDefault();
          e.stopPropagation();
          return;
        }

        var t = pathTargetFrom(e.target);
        if (!t || !root.contains(t)) return;

        var key = focusKeyFrom(t);
        if (!key) return;

        // SVG map: focus path/node, never navigate node links
        if (isInMesh(e.target)) {
          e.preventDefault();
          e.stopPropagation();
          lockPath(key.id, key.chain);
          return;
        }

        if (t.hasAttribute('data-fabric-focus-click')) {
          var nav = e.target.closest('a[href]');
          if (
            nav &&
            nav !== t &&
            !nav.hasAttribute('data-path-id') &&
            !nav.hasAttribute('data-path-ids') &&
            !nav.hasAttribute('data-node-id') &&
            !nav.hasAttribute('data-fabric-focus-click')
          ) {
            return;
          }
          e.preventDefault();
          lockPath(key.id, key.chain);
          return;
        }

        if (!e.target.closest('a[href]')) {
          lockPath(key.id, key.chain);
        }
      },
      true
    );

    // Record path/node under finger before pan-zoom setPointerCapture retargets events
    root.addEventListener(
      'pointerdown',
      function (e) {
        if (e.pointerType !== 'touch') return;
        markTouchActivity();
        var t = pathTargetFrom(e.target);
        if (t && root.contains(t)) {
          var key = focusKeyFrom(t);
          if (key) {
            touchDown = {
              id: key.id,
              chain: key.chain,
              pointerId: e.pointerId,
              inMesh: isInMesh(e.target),
              focusClick: t.hasAttribute('data-fabric-focus-click'),
            };
            return;
          }
        }
        touchDown = null;
      },
      true
    );

    // Touch: lock focus on short tap. Ghost mouse/click cannot unlock for ~1s.
    root.addEventListener(
      'pointerup',
      function (e) {
        if (e.pointerType !== 'touch') return;
        if (e.button != null && e.button !== 0) return;
        markTouchActivity();

        var vp = e.target.closest('[data-fabric-viewport]');
        if (!vp) {
          vp = root.querySelector('[data-fabric-viewport]');
        }
        if (vp && (vp.dataset.fabricSuppressClick === '1' || vp.dataset.fabricTouchMoved === '1')) {
          touchDown = null;
          return;
        }

        var id = null;
        var chain = '';
        var allow = false;
        var t = pathTargetFrom(e.target);
        if (t && root.contains(t)) {
          var key = focusKeyFrom(t);
          if (key) {
            id = key.id;
            chain = key.chain;
            allow = isInMesh(e.target) || t.hasAttribute('data-fabric-focus-click');
          }
        }
        if ((!id || !allow) && touchDown && touchDown.pointerId === e.pointerId) {
          id = touchDown.id;
          chain = touchDown.chain;
          allow = touchDown.inMesh || touchDown.focusClick;
        }
        touchDown = null;
        if (!allow || !id) return;

        root._fabricTouchHandled = id;
        setTimeout(function () {
          if (root._fabricTouchHandled === id) root._fabricTouchHandled = null;
        }, 1000);

        e.preventDefault();
        if (locked === String(id)) {
          // Ghost double-fire right after lock — keep focus
          if (Date.now() - lastLockAt < 700) {
            focusPath(root, id, chain, true);
            return;
          }
          // Intentional second tap — clear
          locked = null;
          hoverId = null;
          clearFocus(root);
          return;
        }
        lockPath(id, chain);
      },
      true
    );

    root.addEventListener(
      'pointercancel',
      function (e) {
        if (e.pointerType === 'touch') {
          touchDown = null;
          markTouchActivity();
        }
      },
      true
    );

    // Mesh node links must never navigate
    root.addEventListener(
      'click',
      function (e) {
        if (!isInMesh(e.target)) return;
        var a = e.target.closest('a[href]');
        if (!a) return;
        e.preventDefault();
        e.stopPropagation();
      },
      true
    );

    var initial =
      root.getAttribute('data-fabric-initial-focus') ||
      (function () {
        try {
          var u = new URL(window.location.href);
          return u.searchParams.get('focus') || '';
        } catch (err) {
          return '';
        }
      })();
    if (initial) {
      locked = String(initial);
      lastLockAt = Date.now();
      focusPath(root, locked, null, true);
    }
  }

  function initGraphToggle(root) {
    var graph = root.querySelector('[data-fabric-graph]');
    if (!graph) return;
    var btn = root.querySelector('[data-fabric-open-graph]');
    var closeBtn = root.querySelector('[data-fabric-close-graph]');

    function open() {
      graph.classList.add('is-open');
      if (btn) btn.setAttribute('aria-expanded', 'true');
    }
    function close() {
      graph.classList.remove('is-open');
      if (btn) btn.setAttribute('aria-expanded', 'false');
    }

    // Capture phase so focus/ghost handlers never steal this control
    if (btn) {
      btn.addEventListener(
        'click',
        function (e) {
          e.preventDefault();
          e.stopPropagation();
          if (graph.classList.contains('is-open')) {
            graph.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            return;
          }
          open();
          // next frame so display:block applies before scroll
          requestAnimationFrame(function () {
            graph.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          });
        },
        true
      );
    }
    if (closeBtn) {
      closeBtn.addEventListener(
        'click',
        function (e) {
          e.preventDefault();
          e.stopPropagation();
          close();
        },
        true
      );
    }
  }

  function initFilters(root) {
    var bar = root.querySelector('[data-fabric-filters]');
    if (!bar) return;
    var search = bar.querySelector('[data-fabric-filter-q]');
    var mode = bar.querySelector('[data-fabric-filter-mode]');
    var host = bar.querySelector('[data-fabric-filter-host]');
    var items = root.querySelectorAll('[data-fabric-filter-item]');
    if (!items.length) return;

    function apply() {
      var q = (search && search.value ? search.value : '').trim().toLowerCase();
      var m = mode && mode.value ? mode.value : 'all';
      var h = host && host.value ? host.value : '';
      var visible = 0;
      items.forEach(function (el) {
        var pk = el.getAttribute('data-path-kind') || '';
        var via = el.getAttribute('data-via-npm') === '1';
        var hosts = (el.getAttribute('data-hosts') || '').split(',').filter(Boolean);
        var text = (el.getAttribute('data-filter-text') || el.textContent || '').toLowerCase();
        var ok = true;
        if (m === 'npm' && !via) ok = false;
        if (m === 'direct' && via) ok = false;
        if (m === 'identity' && pk !== 'host_identity' && pk !== 'host_app') ok = false;
        if (h && hosts.indexOf(h) < 0) ok = false;
        if (q && text.indexOf(q) < 0) ok = false;
        el.classList.toggle('hidden', !ok);
        if (ok) visible += 1;
      });
      var empty = root.querySelector('[data-fabric-filter-empty]');
      if (empty) empty.classList.toggle('hidden', visible > 0);
      var count = root.querySelector('[data-fabric-filter-count]');
      if (count) count.textContent = String(visible);
    }

    ['input', 'change'].forEach(function (ev) {
      if (search) search.addEventListener(ev, apply);
      if (mode) mode.addEventListener(ev, apply);
      if (host) host.addEventListener(ev, apply);
    });
    apply();
  }

  /**
   * Pinch / wheel zoom + drag pan via SVG viewBox (vector-crisp — no CSS transform).
   */
  function initPanZoom(root) {
    var viewports = root.querySelectorAll('[data-fabric-viewport]');
    if (!viewports.length) {
      root.querySelectorAll('.fabric-mesh-scroll').forEach(function (scroll) {
        if (scroll.querySelector('[data-fabric-viewport]')) return;
        var svg0 = scroll.querySelector('svg.fabric-mesh-svg');
        if (!svg0) return;
        var vp = document.createElement('div');
        vp.className = 'fabric-mesh-viewport';
        vp.setAttribute('data-fabric-viewport', '');
        scroll.insertBefore(vp, svg0);
        vp.appendChild(svg0);
      });
      viewports = root.querySelectorAll('[data-fabric-viewport]');
    }

    viewports.forEach(function (viewport) {
      if (viewport.dataset.fabricZoomInit === '1') return;
      viewport.dataset.fabricZoomInit = '1';

      var svg = viewport.querySelector('svg.fabric-mesh-svg');
      if (!svg) return;

      // Flatten legacy stage wrapper if present
      var stage = viewport.querySelector('[data-fabric-stage], .fabric-mesh-stage');
      if (stage && svg.parentNode === stage) {
        viewport.appendChild(svg);
        if (stage.parentNode) stage.parentNode.removeChild(stage);
      }

      // Clear any leftover CSS transform blur
      svg.style.transform = '';
      svg.style.webkitTransform = '';

      var vbAttr = (svg.getAttribute('viewBox') || '0 0 960 640').trim().split(/[\s,]+/);
      var base = {
        x: parseFloat(vbAttr[0]) || 0,
        y: parseFloat(vbAttr[1]) || 0,
        w: parseFloat(vbAttr[2]) || 960,
        h: parseFloat(vbAttr[3]) || 640,
      };
      var scale = 1; // 1 = 100%, 5 = 500%
      var cx = base.x + base.w / 2; // view center in SVG units
      var cy = base.y + base.h / 2;
      var minScale = 0.35;
      var maxScale = 5;
      var pointers = new Map();
      var pinchStartDist = 0;
      var pinchStartScale = 1;
      var pinchStartCx = cx;
      var pinchStartCy = cy;
      var panLastX = 0;
      var panLastY = 0;
      var panning = false;
      var moved = false;
      var downX = 0;
      var downY = 0;
      var moveThreshold = 10;

      var graph = viewport.closest('[data-fabric-graph]') || root;
      var btnIn = graph.querySelector('[data-fabric-zoom-in]');
      var btnOut = graph.querySelector('[data-fabric-zoom-out]');
      var btnReset = graph.querySelector('[data-fabric-zoom-reset]');
      var label = graph.querySelector('[data-fabric-zoom-label]');

      function clamp(v, a, b) {
        return Math.max(a, Math.min(b, v));
      }

      function viewSize() {
        return { w: base.w / scale, h: base.h / scale };
      }

      function applyView() {
        var vs = viewSize();
        var x = cx - vs.w / 2;
        var y = cy - vs.h / 2;
        svg.setAttribute(
          'viewBox',
          x + ' ' + y + ' ' + vs.w + ' ' + vs.h
        );
        if (label) label.textContent = Math.round(scale * 100) + '%';
        viewport.classList.toggle('is-zoomed', Math.abs(scale - 1) > 0.01);
      }

      function clientToSvg(clientX, clientY) {
        var rect = svg.getBoundingClientRect();
        var vs = viewSize();
        var x0 = cx - vs.w / 2;
        var y0 = cy - vs.h / 2;
        var rw = rect.width || 1;
        var rh = rect.height || 1;
        return {
          x: x0 + ((clientX - rect.left) / rw) * vs.w,
          y: y0 + ((clientY - rect.top) / rh) * vs.h,
        };
      }

      function zoomAt(clientX, clientY, nextScale) {
        nextScale = clamp(nextScale, minScale, maxScale);
        var pt = clientToSvg(clientX, clientY);
        var rect = svg.getBoundingClientRect();
        var fracX = (clientX - rect.left) / (rect.width || 1);
        var fracY = (clientY - rect.top) / (rect.height || 1);
        scale = nextScale;
        var vs = viewSize();
        // Keep the SVG point under the cursor
        var x0 = pt.x - fracX * vs.w;
        var y0 = pt.y - fracY * vs.h;
        cx = x0 + vs.w / 2;
        cy = y0 + vs.h / 2;
        applyView();
      }

      function panByScreen(dx, dy) {
        var rect = svg.getBoundingClientRect();
        var vs = viewSize();
        // Drag right → content follows finger → viewBox moves left
        cx -= (dx / (rect.width || 1)) * vs.w;
        cy -= (dy / (rect.height || 1)) * vs.h;
        applyView();
      }

      function reset() {
        scale = 1;
        cx = base.x + base.w / 2;
        cy = base.y + base.h / 2;
        applyView();
      }

      function dist(a, b) {
        var dx = a.x - b.x;
        var dy = a.y - b.y;
        return Math.sqrt(dx * dx + dy * dy);
      }

      function midpoint(a, b) {
        return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      }

      viewport.addEventListener(
        'wheel',
        function (e) {
          e.preventDefault();
          var factor = e.deltaY > 0 ? 0.9 : 1.11;
          if (e.deltaMode === 1) factor = e.deltaY > 0 ? 0.85 : 1.18;
          zoomAt(e.clientX, e.clientY, scale * factor);
        },
        { passive: false }
      );

      viewport.addEventListener('pointerdown', function (e) {
        if (e.button !== 0 && e.pointerType === 'mouse') return;
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        try {
          viewport.setPointerCapture(e.pointerId);
        } catch (err) { /* ignore */ }
        moved = false;
        downX = e.clientX;
        downY = e.clientY;
        delete viewport.dataset.fabricTouchMoved;
        delete viewport.dataset.fabricSuppressClick;

        if (pointers.size === 2) {
          var pts = Array.from(pointers.values());
          pinchStartDist = dist(pts[0], pts[1]) || 1;
          pinchStartScale = scale;
          pinchStartCx = cx;
          pinchStartCy = cy;
          panning = false;
          moved = true;
          viewport.dataset.fabricTouchMoved = '1';
        } else if (pointers.size === 1) {
          panLastX = e.clientX;
          panLastY = e.clientY;
          panning = true;
          viewport.classList.add('is-panning');
        }
      });

      viewport.addEventListener('pointermove', function (e) {
        if (!pointers.has(e.pointerId)) return;
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

        if (pointers.size === 2) {
          var pts = Array.from(pointers.values());
          var d = dist(pts[0], pts[1]) || 1;
          var mid = midpoint(pts[0], pts[1]);
          var next = pinchStartScale * (d / pinchStartDist);
          zoomAt(mid.x, mid.y, next);
          moved = true;
          viewport.dataset.fabricTouchMoved = '1';
          return;
        }

        if (panning && pointers.size === 1) {
          var dx = e.clientX - panLastX;
          var dy = e.clientY - panLastY;
          var totalDx = e.clientX - downX;
          var totalDy = e.clientY - downY;
          if (Math.abs(totalDx) > moveThreshold || Math.abs(totalDy) > moveThreshold) {
            moved = true;
            viewport.dataset.fabricTouchMoved = '1';
          }
          panLastX = e.clientX;
          panLastY = e.clientY;
          if (moved) {
            panByScreen(dx, dy);
          }
        }
      });

      function endPointer(e) {
        if (pointers.has(e.pointerId)) {
          pointers.delete(e.pointerId);
        }
        try {
          viewport.releasePointerCapture(e.pointerId);
        } catch (err) { /* ignore */ }
        if (pointers.size < 2) {
          pinchStartDist = 0;
        }
        if (pointers.size === 0) {
          panning = false;
          viewport.classList.remove('is-panning');
          if (moved) {
            viewport.dataset.fabricSuppressClick = '1';
            viewport.dataset.fabricTouchMoved = '1';
            setTimeout(function () {
              delete viewport.dataset.fabricSuppressClick;
              delete viewport.dataset.fabricTouchMoved;
            }, 120);
          }
        } else if (pointers.size === 1) {
          var only = Array.from(pointers.values())[0];
          panLastX = only.x;
          panLastY = only.y;
          panning = true;
        }
      }

      viewport.addEventListener('pointerup', endPointer);
      viewport.addEventListener('pointercancel', endPointer);

      viewport.addEventListener(
        'click',
        function (e) {
          if (viewport.dataset.fabricSuppressClick === '1') {
            e.preventDefault();
            e.stopPropagation();
          }
        },
        true
      );

      viewport.addEventListener('dblclick', function (e) {
        e.preventDefault();
        reset();
      });

      if (btnIn) {
        btnIn.addEventListener('click', function (e) {
          e.preventDefault();
          e.stopPropagation();
          var r = svg.getBoundingClientRect();
          zoomAt(r.left + r.width / 2, r.top + r.height / 2, scale * 1.25);
        });
      }
      if (btnOut) {
        btnOut.addEventListener('click', function (e) {
          e.preventDefault();
          e.stopPropagation();
          var r = svg.getBoundingClientRect();
          zoomAt(r.left + r.width / 2, r.top + r.height / 2, scale / 1.25);
        });
      }
      if (btnReset) {
        btnReset.addEventListener('click', function (e) {
          e.preventDefault();
          e.stopPropagation();
          reset();
        });
      }

      applyView();
    });
  }

  function boot() {
    document.querySelectorAll('[data-fabric-root]').forEach(function (root) {
      initFocusRoot(root);
      initGraphToggle(root);
      initFilters(root);
      initCopy(root);
      initPanZoom(root);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  window.PiHerderFabric = {
    focusPath: focusPath,
    clearFocus: clearFocus,
    boot: boot,
  };
})();
