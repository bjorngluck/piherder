/**
 * DNS fabric mesh: path focus, mobile graph toggle, map full-screen,
 * filters, pinch/wheel zoom + drag pan, copy path.
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

  function npmPathIdsFromEl(el) {
    if (!el) return [];
    var multi = el.getAttribute('data-npm-path-ids');
    if (!multi) return [];
    return multi.split(/[\s,]+/).map(function (s) { return s.trim(); }).filter(Boolean);
  }

  function parseIdList(attr) {
    if (!attr) return [];
    return String(attr).split(/[\s,]+/).map(function (s) { return s.trim(); }).filter(Boolean);
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

  /**
   * Host focus is role-aware for service edges:
   *  - land edge only for services this host *backends*
   *  - npm dashed only for services this host *NPM-edges*
   * Never light the far-side host / the other edge type for that service.
   */
  function elMatchesActiveSet(el, active) {
    if (!el || !active) return false;
    var nodes = active.nodes || {};
    var paths = active.paths || {};
    var backendPaths = active.backendPaths || {};
    var npmPaths = active.npmPaths || {};
    var hostNid = active.hostNid || '';
    var mode = active.mode || 'path';

    if (el.classList && el.classList.contains('fabric-mesh-edge')) {
      var edgePaths = pathIdsFromEl(el);
      var fn = (el.getAttribute('data-from-node') || '').trim();
      var tn = (el.getAttribute('data-to-node') || '').trim();
      var isLand = el.classList.contains('fabric-mesh-edge--land');
      var isNpm = el.classList.contains('fabric-mesh-edge--npm');

      if (mode === 'host' && hostNid) {
        // Pure topology (host ↔ LAN / Internet) — no path id
        if (!edgePaths.length && fn && tn &&
            nodes[nodeFocusKey(fn)] && nodes[nodeFocusKey(tn)]) {
          return true;
        }
        for (var i = 0; i < edgePaths.length; i++) {
          var ep = String(edgePaths[i]);
          // Backend service: only land edge touching this host
          if (isLand && backendPaths[ep] && (tn === hostNid || fn === hostNid)) {
            return true;
          }
          // NPM-edge service: only dashed npm edge touching this host
          if (isNpm && npmPaths[ep] && (tn === hostNid || fn === hostNid)) {
            return true;
          }
        }
        return false;
      }

      // Path / app focus: any edge for that path (land + npm)
      for (var j = 0; j < edgePaths.length; j++) {
        if (paths[String(edgePaths[j])]) return true;
      }
      if (fn && tn && nodes[nodeFocusKey(fn)] && nodes[nodeFocusKey(tn)] && !edgePaths.length) {
        return true;
      }
      return false;
    }

    // Host / infra / any node-id: ONLY explicit membership in the node set.
    // Never match another server because it shares a service path id (that was
    // lighting rpi5-4 when selecting rpi5-3 as NPM edge for the same service).
    var nid = nodeIdFromEl(el);
    if (nid) {
      return !!nodes[nodeFocusKey(nid)];
    }

    // App / service cards (path-id only, no node-id)
    var pids = pathIdsFromEl(el);
    if (mode === 'host') {
      for (var k = 0; k < pids.length; k++) {
        var pp = String(pids[k]);
        if (backendPaths[pp] || npmPaths[pp]) return true;
      }
      return false;
    }
    for (var m = 0; m < pids.length; m++) {
      if (paths[String(pids[m])]) return true;
    }
    return false;
  }

  /**
   * Selecting a host (e.g. rpi5-3):
   *  1) host + LAN/Internet + topo lines
   *  2) services this host backends + land edges only (not npm dashed to other hosts)
   *  3) services this host NPM-edges + npm dashed only (not land edge / not backend host)
   */
  function buildActiveSet(root, focusId) {
    var nodes = {};
    var paths = {};
    var backendPaths = {};
    var npmPaths = {};
    var hostNid = '';
    var mode = 'path';

    if (focusId == null || focusId === '') {
      return {
        mode: mode,
        nodes: nodes,
        paths: paths,
        backendPaths: backendPaths,
        npmPaths: npmPaths,
        hostNid: hostNid,
      };
    }

    function markNode(nid) {
      if (!nid) return;
      nodes[nodeFocusKey(String(nid).trim())] = true;
    }

    var primary = String(focusId);

    if (isNodeFocusId(primary)) {
      mode = 'host';
      hostNid = primary.slice(2);
      markNode(hostNid);

      // Topo one-hop only (edges without service path ids)
      root.querySelectorAll('.fabric-mesh-edge').forEach(function (edge) {
        var eps = pathIdsFromEl(edge);
        if (eps.length) return;
        var fn = (edge.getAttribute('data-from-node') || '').trim();
        var tn = (edge.getAttribute('data-to-node') || '').trim();
        if (fn === hostNid || tn === hostNid) {
          markNode(fn);
          markNode(tn);
        }
      });

      var nodeEl = root.querySelector('[data-node-id="' + hostNid.replace(/"/g, '') + '"]');
      if (nodeEl) {
        pathIdsFromEl(nodeEl).forEach(function (p) {
          backendPaths[String(p)] = true;
        });
        npmPathIdsFromEl(nodeEl).forEach(function (p) {
          npmPaths[String(p)] = true;
        });
      }
      // Also discover from edges (in case attributes lag)
      root.querySelectorAll('.fabric-mesh-edge').forEach(function (edge) {
        var tn = (edge.getAttribute('data-to-node') || '').trim();
        var fn = (edge.getAttribute('data-from-node') || '').trim();
        if (tn !== hostNid && fn !== hostNid) return;
        var isLand = edge.classList.contains('fabric-mesh-edge--land');
        var isNpm = edge.classList.contains('fabric-mesh-edge--npm');
        pathIdsFromEl(edge).forEach(function (p) {
          if (isLand) backendPaths[String(p)] = true;
          if (isNpm) npmPaths[String(p)] = true;
        });
      });
      // Do not mark other hosts for these services
    } else {
      mode = 'path';
      paths[primary] = true;
      root.querySelectorAll('.fabric-mesh-edge').forEach(function (edge) {
        if (!elMatchesPath(edge, primary)) return;
        markNode(edge.getAttribute('data-from-node'));
        markNode(edge.getAttribute('data-to-node'));
      });
      root.querySelectorAll('[data-path-id], [data-path-ids]').forEach(function (el) {
        if (!elMatchesPath(el, primary)) return;
        var n = nodeIdFromEl(el);
        if (n) markNode(n);
      });
    }

    return {
      mode: mode,
      nodes: nodes,
      paths: paths,
      backendPaths: backendPaths,
      npmPaths: npmPaths,
      hostNid: hostNid,
    };
  }

  function focusableIn(root) {
    return root.querySelectorAll(
      '[data-path-id], [data-path-ids], [data-node-id], [data-from-node], [data-to-node], ' +
      '.fabric-mesh-edge, .fabric-mesh-node, ' +
      '.fabric-path-card, .fabric-flow, .fabric-rack, .fabric-app-chip'
    );
  }

  /** Raise focused cards/edges so overlapping neighbours don't hide them. */
  function raiseActiveToFront(root) {
    var svg = root.querySelector('svg.fabric-mesh-svg');
    if (!svg) return;
    var edges = [];
    var nodes = [];
    svg.querySelectorAll('.ph-focus-active').forEach(function (el) {
      var top = el;
      while (top.parentNode && top.parentNode !== svg) top = top.parentNode;
      if (top.parentNode !== svg) return;
      if (top.classList && top.classList.contains('fabric-mesh-edge')) edges.push(top);
      else nodes.push(top);
    });
    // Active edges under active nodes (both above dimmed content)
    edges.forEach(function (n) {
      try {
        svg.appendChild(n);
      } catch (err) {}
    });
    nodes.forEach(function (n) {
      try {
        svg.appendChild(n);
      } catch (err) {}
    });
  }

  function clearFocus(root) {
    root.classList.remove('is-focusing');
    root._fabricFocusId = null;
    root._fabricActiveSet = null;
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
    var stackFocus = root.querySelector('[data-fabric-stack-from-focus]');
    if (stackFocus) {
      stackFocus.classList.add('hidden');
      stackFocus.removeAttribute('data-stack-url');
    }
    var openLink = root.querySelector('[data-fabric-open-link]');
    if (openLink) {
      openLink.href = '#';
      openLink.classList.add('hidden');
      openLink.setAttribute('hidden', '');
      openLink.textContent = openLink.getAttribute('data-default-label') || 'Open host';
    }
    try {
      if (window.PiHerderStackExpand && typeof window.PiHerderStackExpand.clear === 'function') {
        window.PiHerderStackExpand.clear();
      }
    } catch (err) { /* optional */ }
  }

  function setStackFocusButton(root, focusId) {
    var stackFocus = root.querySelector('[data-fabric-stack-from-focus]');
    if (!stackFocus) return;
    var fid = focusId != null ? String(focusId) : '';
    // Only service path ids (numeric) — not host node focus "n:…"
    if (fid && fid.indexOf('n:') !== 0 && /^\d+$/.test(fid)) {
      stackFocus.setAttribute(
        'data-stack-url',
        '/dns/stack-panel?service_id=' + encodeURIComponent(fid)
      );
      stackFocus.classList.remove('hidden');
    } else {
      stackFocus.classList.add('hidden');
      stackFocus.removeAttribute('data-stack-url');
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
    // Stack only when focus is a mapped service path (set in focusPath)
    setStackFocusButton(root, root._fabricFocusId);
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

    var activeSet = buildActiveSet(root, idStr);
    root.classList.add('is-focusing');
    root._fabricFocusId = idStr;
    root._fabricActiveSet = activeSet;
    setStackFocusButton(root, idStr);
    var found = false;
    focusableIn(root).forEach(function (el) {
      var hasPath =
        el.hasAttribute('data-path-id') || el.hasAttribute('data-path-ids');
      var hasNode = el.hasAttribute('data-node-id');
      var hasTopo =
        el.hasAttribute('data-from-node') || el.hasAttribute('data-to-node');
      if (!hasPath && !hasNode && !hasTopo) {
        if (el.classList.contains('fabric-mesh-edge') || el.classList.contains('fabric-mesh-node')) {
          el.classList.remove('ph-focus-active');
          el.classList.add('ph-focus-dim');
        }
        return;
      }
      var on = elMatchesActiveSet(el, activeSet);
      el.classList.toggle('ph-focus-active', on);
      el.classList.toggle('ph-focus-dim', !on);
      if (on) {
        found = true;
        if (!chainText && (hasNode || hasPath)) {
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
    raiseActiveToFront(root);
    // P4 — path map stack expand (containers + confirmed edges)
    try {
      if (window.PiHerderStackExpand && typeof window.PiHerderStackExpand.show === 'function') {
        window.PiHerderStackExpand.show(idStr);
      }
    } catch (err) { /* optional */ }
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

  /**
   * Card/list controls only — NOT map nodes.
   * Map nodes are wrapped in <a href="…">; treating those as chrome
   * broke all path/host focus (regression).
   */
  function isFabricChrome(el) {
    if (!el || !el.closest) return false;
    // Never classify SVG mesh content as chrome (even when inside <a>)
    if (isInMesh(el)) return false;
    return !!el.closest(
      'button, input, select, textarea, summary, label, form, ' +
      '[data-fabric-stack-open], [data-fabric-copy-path], [data-fabric-copy-callout], ' +
      '[data-fabric-open-graph], [data-fabric-close-graph], ' +
      '[data-fabric-fullscreen], [data-fabric-zoom-in], [data-fabric-zoom-out], ' +
      '[data-fabric-zoom-reset], [data-fabric-clear-focus], [data-fabric-stack-close]'
    );
  }

  function pathTargetFrom(el) {
    if (!el || !el.closest) return null;
    // Buttons on path cards / flow rows: do not resolve to the card for focus
    // (pointerup preventDefault would kill the following click on mobile).
    if (isFabricChrome(el)) return null;
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
        // Explicit chrome — leave alone (zoom, stack, copy, forms…).
        // Also leave mesh <a> alone for the special mesh click handler below;
        // list/card chrome uses isFabricChrome (never matches mesh).
        if (
          isFabricChrome(e.target) ||
          e.target.closest(
            '[data-fabric-open-graph], [data-fabric-close-graph], [data-fabric-fullscreen], ' +
            '[data-fabric-zoom-in], [data-fabric-zoom-out], [data-fabric-zoom-reset], ' +
            '[data-fabric-copy-path], [data-fabric-copy-callout], [data-fabric-open-link], ' +
            '[data-fabric-stack-open], [data-fabric-stack-close], ' +
            'input, select, textarea, summary, label, button'
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
        // Buttons/links on path cards must keep their own click (mobile)
        if (isFabricChrome(e.target)) {
          touchDown = null;
          return;
        }
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

        // Never preventDefault on chrome — that kills the following click on iOS/Android
        if (isFabricChrome(e.target)) {
          touchDown = null;
          return;
        }

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

  function preferMapOnLoad() {
    try {
      var u = new URL(window.location.href);
      if ((u.hash || '').replace(/^#/, '') === 'map') return true;
      if (u.searchParams.get('map') === '1') return true;
      // Deep-linked focus (host or path) → open the SVG, not list-first chrome
      if ((u.searchParams.get('focus') || '').trim()) return true;
    } catch (err) {}
    return false;
  }

  function scrollMapIntoView(graph) {
    if (!graph) return;
    try {
      graph.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err2) {
      try {
        graph.scrollIntoView(true);
      } catch (err3) {}
    }
  }

  function initGraphToggle(root) {
    var graph = root.querySelector('[data-fabric-graph]');
    if (!graph) return;
    // Anchor target for #map deep links
    if (!graph.id) graph.id = 'map';
    var btn = root.querySelector('[data-fabric-open-graph]');
    var closeBtn = root.querySelector('[data-fabric-close-graph]');

    function open() {
      graph.classList.add('is-open');
      if (btn) btn.setAttribute('aria-expanded', 'true');
    }
    function close() {
      // Leaving list-first density: exit fullscreen chrome fully first
      if (graph.classList.contains('is-map-fullscreen')) {
        exitMapFullscreenAll();
      }
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
            scrollMapIntoView(graph);
            return;
          }
          open();
          // next frame so display:block applies before scroll
          requestAnimationFrame(function () {
            scrollMapIntoView(graph);
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

    // Map links from dashboard / server / docker land on the SVG panel
    if (preferMapOnLoad()) {
      open();
      requestAnimationFrame(function () {
        scrollMapIntoView(graph);
        // Second tick: layout after mobile display:block
        requestAnimationFrame(function () {
          scrollMapIntoView(graph);
        });
      });
    }
  }

  /**
   * Registry of per-root fullscreen controllers so the hamburger (and other
   * chrome) can fully tear down label/aria/listeners — not only CSS classes.
   */
  var _mapFullscreenControllers = [];
  /** Pan/zoom controllers — reflow after portrait↔landscape without remount. */
  var _panZoomControllers = [];

  /** Expand hosts/path map to full viewport (width + height). Esc or button to exit. */
  function initMapFullscreen(root) {
    var graph = root.querySelector('[data-fabric-graph]');
    if (!graph) return;
    var btn = root.querySelector('[data-fabric-fullscreen]');
    if (!btn) return;
    var onVv = null;

    function setLabel(on) {
      btn.textContent = on ? 'Exit full' : 'Full screen';
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      btn.title = on ? 'Exit full screen (Esc)' : 'Expand map to full screen';
    }

    /** Pin height to visual viewport (fixes mobile browser chrome / only-widens bug). */
    function applyViewportSize() {
      if (!graph.classList.contains('is-map-fullscreen')) return;
      var h = window.innerHeight || document.documentElement.clientHeight || 0;
      try {
        if (window.visualViewport && window.visualViewport.height) {
          h = window.visualViewport.height;
        }
      } catch (err) {}
      if (h > 0) {
        graph.style.height = h + 'px';
        graph.style.maxHeight = h + 'px';
        graph.style.minHeight = h + 'px';
      }
      var scroll = graph.querySelector('.fabric-mesh-scroll');
      var vp = graph.querySelector('[data-fabric-viewport]');
      if (scroll && vp) {
        // Fill remaining space below toolbar / callout
        var used = 0;
        Array.prototype.forEach.call(graph.children, function (ch) {
          if (ch === scroll) return;
          used += ch.getBoundingClientRect().height || 0;
        });
        var pad = 16;
        var avail = Math.max(160, h - used - pad);
        scroll.style.flex = '1 1 auto';
        scroll.style.height = avail + 'px';
        scroll.style.minHeight = avail + 'px';
        vp.style.height = avail + 'px';
        vp.style.minHeight = avail + 'px';
        vp.style.maxHeight = 'none';
      }
    }

    function clearInlineSizes() {
      graph.style.height = '';
      graph.style.maxHeight = '';
      graph.style.minHeight = '';
      var scroll = graph.querySelector('.fabric-mesh-scroll');
      var vp = graph.querySelector('[data-fabric-viewport]');
      if (scroll) {
        scroll.style.flex = '';
        scroll.style.height = '';
        scroll.style.minHeight = '';
      }
      if (vp) {
        vp.style.height = '';
        vp.style.minHeight = '';
        vp.style.maxHeight = '';
      }
    }

    function detachViewportListeners() {
      if (!onVv) return;
      window.removeEventListener('resize', onVv);
      try {
        if (window.visualViewport) {
          window.visualViewport.removeEventListener('resize', onVv);
          window.visualViewport.removeEventListener('scroll', onVv);
        }
      } catch (err) {}
      onVv = null;
    }

    function enter() {
      graph.classList.add('is-map-fullscreen', 'is-open');
      document.body.classList.add('fabric-map-fullscreen');
      setLabel(true);
      var openBtn = root.querySelector('[data-fabric-open-graph]');
      if (openBtn) openBtn.setAttribute('aria-expanded', 'true');
      applyViewportSize();
      detachViewportListeners();
      onVv = function () {
        applyViewportSize();
      };
      window.addEventListener('resize', onVv);
      try {
        if (window.visualViewport) {
          window.visualViewport.addEventListener('resize', onVv);
          window.visualViewport.addEventListener('scroll', onVv);
        }
      } catch (err) {}
      requestAnimationFrame(function () {
        applyViewportSize();
        try {
          window.dispatchEvent(new Event('resize'));
        } catch (e2) {}
      });
    }

    function exit() {
      if (!graph.classList.contains('is-map-fullscreen') && !document.body.classList.contains('fabric-map-fullscreen')) {
        // Still reset chrome if a partial teardown left labels stale
        setLabel(false);
        clearInlineSizes();
        detachViewportListeners();
        return;
      }
      graph.classList.remove('is-map-fullscreen');
      document.body.classList.remove('fabric-map-fullscreen');
      setLabel(false);
      clearInlineSizes();
      detachViewportListeners();
    }

    btn.addEventListener(
      'click',
      function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (graph.classList.contains('is-map-fullscreen')) exit();
        else enter();
      },
      true
    );

    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      if (!graph.classList.contains('is-map-fullscreen')) return;
      e.preventDefault();
      exit();
    });

    window.addEventListener('pagehide', exit);

    _mapFullscreenControllers.push({
      root: root,
      graph: graph,
      exit: exit,
      setLabel: setLabel,
      applyViewportSize: applyViewportSize,
    });
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

      _panZoomControllers.push({
        viewport: viewport,
        svg: svg,
        reset: reset,
        applyView: applyView,
      });

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
      initMapFullscreen(root);
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

  function exitMapFullscreenAll() {
    // Prefer registered controllers (full label/aria/listener teardown)
    if (_mapFullscreenControllers.length) {
      _mapFullscreenControllers.forEach(function (c) {
        try {
          c.exit();
        } catch (err) {}
      });
    } else {
      // Fallback if boot never ran (still clear classes for hamburger)
      document.querySelectorAll('[data-fabric-graph].is-map-fullscreen').forEach(function (graph) {
        graph.classList.remove('is-map-fullscreen');
        graph.style.height = '';
        graph.style.maxHeight = '';
        graph.style.minHeight = '';
        var scroll = graph.querySelector('.fabric-mesh-scroll');
        var vp = graph.querySelector('[data-fabric-viewport]');
        if (scroll) {
          scroll.style.flex = '';
          scroll.style.height = '';
          scroll.style.minHeight = '';
        }
        if (vp) {
          vp.style.height = '';
          vp.style.minHeight = '';
          vp.style.maxHeight = '';
        }
        var btn = graph.querySelector('[data-fabric-fullscreen]');
        if (btn) {
          btn.textContent = 'Full screen';
          btn.setAttribute('aria-pressed', 'false');
          btn.title = 'Expand map to full screen';
        }
      });
    }
    document.body.classList.remove('fabric-map-fullscreen');
  }

  /**
   * Network maps keep wide SVG viewBoxes + vh heights that iOS often leaves
   * stale after portrait↔landscape. Called from the global orientation
   * reflow and from fabric's own listeners.
   */
  function refreshLayout(opts) {
    opts = opts || {};
    var resetZoom = opts.resetZoom !== false; // default true on orient

    try {
      document.documentElement.scrollLeft = 0;
      document.body.scrollLeft = 0;
      document.querySelectorAll(
        '[data-fabric-root], .fabric-topology, .fabric-mesh-scroll, .fabric-mesh-viewport, main'
      ).forEach(function (el) {
        try {
          el.scrollLeft = 0;
        } catch (err) {}
      });
    } catch (e) {}

    // Clear non-fullscreen inline sizes left from a previous landscape pass
    document.querySelectorAll('[data-fabric-graph]').forEach(function (graph) {
      if (graph.classList.contains('is-map-fullscreen')) return;
      graph.style.height = '';
      graph.style.maxHeight = '';
      graph.style.minHeight = '';
      var scroll = graph.querySelector('.fabric-mesh-scroll');
      var vp = graph.querySelector('[data-fabric-viewport]');
      if (scroll) {
        scroll.style.flex = '';
        scroll.style.height = '';
        scroll.style.minHeight = '';
        scroll.style.width = '';
      }
      if (vp) {
        vp.style.height = '';
        vp.style.minHeight = '';
        vp.style.maxHeight = '';
        vp.style.width = '';
      }
    });

    // Fullscreen maps: re-pin to the new visual viewport
    _mapFullscreenControllers.forEach(function (c) {
      try {
        if (
          c.graph &&
          c.graph.classList.contains('is-map-fullscreen') &&
          typeof c.applyViewportSize === 'function'
        ) {
          c.applyViewportSize();
        }
      } catch (err) {}
    });

    // Reset / re-apply pan-zoom so the SVG fits the new aspect ratio
    _panZoomControllers.forEach(function (c) {
      try {
        if (resetZoom && typeof c.reset === 'function') c.reset();
        else if (typeof c.applyView === 'function') c.applyView();
      } catch (err) {}
    });

    // Force a measure pass on viewports (unsticks width=100% of stale parent)
    document.querySelectorAll('.fabric-mesh-viewport, .fabric-mesh-svg').forEach(function (el) {
      try {
        void el.offsetWidth;
      } catch (err) {}
    });
  }

  // Listen even if base.html reflow already fires — fabric needs its own cleanup
  (function bindFabricOrient() {
    var lastW = window.innerWidth || 0;
    var lastO =
      typeof window.orientation === 'number' ? window.orientation : null;
    var t = null;
    function schedule(forceReset) {
      clearTimeout(t);
      t = setTimeout(function () {
        refreshLayout({ resetZoom: !!forceReset });
        // Second pass after iOS chrome settles
        setTimeout(function () {
          refreshLayout({ resetZoom: false });
        }, 280);
      }, 40);
    }
    window.addEventListener('orientationchange', function () {
      schedule(true);
    });
    window.addEventListener('resize', function () {
      var w = window.innerWidth || 0;
      var o =
        typeof window.orientation === 'number' ? window.orientation : null;
      var crossed = (lastW < 768) !== (w < 768);
      var rotated = o !== null && lastO !== null && o !== lastO;
      lastW = w;
      if (o !== null) lastO = o;
      if (crossed || rotated) schedule(true);
    });
  })();

  window.PiHerderFabric = {
    focusPath: focusPath,
    clearFocus: clearFocus,
    boot: boot,
    exitMapFullscreen: exitMapFullscreenAll,
    refreshLayout: refreshLayout,
  };
})();
