/**
 * P4/P4.5 — Map stack expand (path map + hosts map).
 *
 * Layout: sideways role fan (edge | app | queue | data). data = db+redis+tooling.
 * Custom stack order reorders columns left→right (celery last → queue rightmost).
 * Soft structure lines follow adjacent columns. Click container → Stack detail.
 */
(function () {
  'use strict';

  var cache = {};
  var layerId = 'fabric-stack-expand-layer';
  var NS = 'http://www.w3.org/2000/svg';

  // Subtle fills + clear strokes (role identity without loud paint)
  var ROLE_COLORS = {
    edge: { fill: '#f59e0b12', stroke: '#d97706', chip: '#f59e0b28', label: 'EDGE' },
    app: { fill: '#05966910', stroke: '#059669', chip: '#05966928', label: 'APP' },
    queue: { fill: '#a855f712', stroke: '#9333ea', chip: '#a855f728', label: 'QUEUE' },
    data: { fill: '#6366f112', stroke: '#4f46e5', chip: '#6366f128', label: 'DB' },
    cache: { fill: '#06b6d412', stroke: '#0891b2', chip: '#06b6d428', label: 'CACHE' },
    tooling: { fill: '#64748b0c', stroke: '#64748b', chip: '#64748b20', label: 'TOOL' },
    other: { fill: '#64748b0c', stroke: '#64748b', chip: '#64748b20', label: 'SVC' },
  };

  function svgRoot(root) {
    if (!root) return null;
    return (
      root.querySelector('svg.fabric-mesh-svg--logical') ||
      root.querySelector('svg[data-fabric-mesh="logical"]') ||
      root.querySelector('svg[data-fabric-mesh="physical"]') ||
      root.querySelector('svg.fabric-mesh-svg')
    );
  }

  function isPhysical(svg) {
    if (!svg) return false;
    var m = (svg.getAttribute('data-fabric-mesh') || '').toLowerCase();
    return m === 'physical' || svg.classList.contains('fabric-mesh-svg--physical');
  }

  function anchorNode(svg, pathId) {
    if (!svg || pathId == null) return null;
    var want = String(pathId);
    var selectors = isPhysical(svg)
      ? ['.fabric-mesh-node--app[data-path-id]', '.fabric-mesh-node--app[data-path-ids]']
      : ['.fabric-mesh-node--dest[data-path-id]', '.fabric-mesh-node--url[data-path-id]'];
    for (var s = 0; s < selectors.length; s++) {
      var nodes = svg.querySelectorAll(selectors[s]);
      for (var i = 0; i < nodes.length; i++) {
        var el = nodes[i];
        var one = el.getAttribute('data-path-id');
        if (one != null && String(one) === want) return el;
        var multi = el.getAttribute('data-path-ids') || '';
        if (
          multi &&
          multi
            .split(/[\s,]+/)
            .map(function (x) {
              return x.trim();
            })
            .indexOf(want) >= 0
        ) {
          return el;
        }
      }
    }
    return null;
  }

  function anchorGeom(nodeG) {
    var rect = nodeG && nodeG.querySelector('rect');
    if (rect) {
      var x = parseFloat(rect.getAttribute('x') || '0');
      var y = parseFloat(rect.getAttribute('y') || '0');
      var w = parseFloat(rect.getAttribute('width') || '0');
      var h = parseFloat(rect.getAttribute('height') || '0');
      return { x: x + w / 2, y: y + h / 2, right: x + w, left: x, top: y, bottom: y + h };
    }
    return { x: 820, y: 100, right: 910, left: 730, top: 80, bottom: 120 };
  }

  function clearLayer(svg) {
    if (!svg) return;
    var old = svg.querySelector('#' + layerId);
    if (old && old.parentNode) old.parentNode.removeChild(old);
  }

  /**
   * Do NOT rewrite SVG viewBox on expand/clear — pan-zoom owns viewBox.
   * Expanding the viewBox looked like a zoom-out and discarded zoom level.
   * Extra content stays in SVG space; user can pan while keeping scale.
   */
  function ensureViewBox() {
    /* no-op — preserve zoom */
  }

  function restoreViewBox() {
    /* no-op — preserve zoom */
  }

  function el(name, attrs) {
    var n = document.createElementNS(NS, name);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (attrs[k] != null && attrs[k] !== '') n.setAttribute(k, String(attrs[k]));
      });
    }
    return n;
  }

  function roleKey(ct) {
    var r = (ct.role || 'app').toLowerCase();
    if (r === 'db') return 'data';
    if (r === 'tool') return 'tooling';
    return ROLE_COLORS[r] ? r : 'other';
  }

  function byOrderThenName(a, b) {
    var ia = a.order_index != null ? a.order_index : 999;
    var ib = b.order_index != null ? b.order_index : 999;
    if (ia !== ib) return ia - ib;
    return String(a.name || a.id || '').localeCompare(String(b.name || b.id || ''));
  }

  function minOrderIndex(items) {
    var m = Infinity;
    (items || []).forEach(function (c) {
      if (c.order_index != null && c.order_index < m) m = c.order_index;
    });
    return m === Infinity ? 999 : m;
  }

  /**
   * Sideways role columns. data+cache+tooling stay one "data" column (db+redis together).
   * When the operator set a stack order, column left→right follows that order
   * (min order_index in each column). Celery last in panel → queue column rightmost.
   */
  function buildColumns(containers, hasCustomOrder) {
    var buckets = { edge: [], app: [], queue: [], data: [], cache: [], tooling: [], other: [] };
    (containers || []).forEach(function (c) {
      var r = roleKey(c);
      if (r === 'data' || r === 'cache' || r === 'tooling' || r === 'other') {
        if (!buckets[r]) buckets.other.push(c);
        else buckets[r].push(c);
      } else {
        buckets[r].push(c);
      }
    });
    Object.keys(buckets).forEach(function (k) {
      buckets[k].sort(byOrderThenName);
    });

    var candidates = [];
    if (buckets.edge.length)
      candidates.push({ key: 'edge', label: 'edge', items: buckets.edge, defaultRank: 0 });
    if (buckets.app.length)
      candidates.push({ key: 'app', label: 'app', items: buckets.app, defaultRank: 1 });
    if (buckets.queue.length)
      candidates.push({ key: 'queue', label: 'queue', items: buckets.queue, defaultRank: 2 });
    var deps = buckets.data
      .concat(buckets.cache, buckets.tooling, buckets.other)
      .sort(byOrderThenName);
    if (deps.length)
      candidates.push({ key: 'deps', label: 'data', items: deps, defaultRank: 3 });

    if (!candidates.length) {
      return [
        {
          key: 'empty',
          label: 'stack',
          items: [{ id: '∅', name: 'no containers', role: 'app', running: false }],
        },
      ];
    }

    if (hasCustomOrder) {
      candidates.sort(function (a, b) {
        var ma = minOrderIndex(a.items);
        var mb = minOrderIndex(b.items);
        if (ma !== mb) return ma - mb;
        return a.defaultRank - b.defaultRank;
      });
    } else {
      candidates.sort(function (a, b) {
        return a.defaultRank - b.defaultRank;
      });
    }
    return candidates;
  }

  function draw(svg, pathId, data) {
    clearLayer(svg);
    if (!data || !data.ok) return;
    var anchor = anchorNode(svg, pathId);
    if (!anchor) return;

    var a = anchorGeom(anchor);
    var hasCustomOrder = !!(
      data.has_custom_order ||
      (data.custom_order && data.custom_order.length)
    );
    var cols = buildColumns(data.containers || [], hasCustomOrder);
    var edges = data.edges || [];

    // Fan-out geometry — title + column headers (no deep-link chips)
    var boxW = 118;
    var boxH = 42;
    var colGap = 72;
    var rowGap = 16;
    var rowH = boxH + rowGap;
    var headerBand = 20; // edge / app / data labels
    var titleBand = 18; // "project · runtime"
    var zonePadTop = titleBand + headerBand + 12;
    var zonePadBot = 16;
    var startX = a.right + 72;

    var maxItems = 1;
    cols.forEach(function (c) {
      maxItems = Math.max(maxItems, c.items.length);
    });
    var nodesH = maxItems * rowH - rowGap;
    var midY = a.y;
    var nodesTop = midY - nodesH / 2;
    var zoneTop = nodesTop - zonePadTop;
    if (zoneTop < a.top - 28) {
      var shift = a.top - 28 - zoneTop;
      midY += shift;
      nodesTop += shift;
      zoneTop += shift;
    }

    var pos = {};
    var g = el('g', {
      id: layerId,
      class: 'fabric-stack-expand-layer',
      'data-path-id': String(pathId),
    });

    var zoneW = cols.length * boxW + (cols.length - 1) * colGap + 32;
    var zoneH = zonePadTop + nodesH + zonePadBot;
    g.appendChild(
      el('rect', {
        x: startX - 16,
        y: zoneTop,
        width: zoneW,
        height: zoneH,
        rx: 14,
        class: 'fabric-mesh-stack-zone',
        fill: 'var(--color-surface)',
        'fill-opacity': '0.55',
        stroke: 'var(--color-border)',
        'stroke-opacity': '0.75',
      })
    );

    g.appendChild(
      el('line', {
        x1: a.right + 2,
        y1: a.y,
        x2: startX - 16,
        y2: midY,
        class: 'fabric-mesh-edge fabric-mesh-edge--stack-lead',
        'data-path-id': String(pathId),
        stroke: '#64748b',
        'stroke-width': '2',
        'stroke-dasharray': '5 3',
        'stroke-opacity': '0.9',
        fill: 'none',
      })
    );

    var titleY = zoneTop + 15;
    var title = el('text', {
      x: startX + zoneW / 2 - 16,
      y: titleY,
      'text-anchor': 'middle',
      fill: 'var(--color-muted)',
      'font-size': '11',
      'font-weight': '600',
    });
    title.textContent = (data.project || 'stack') + ' · runtime';
    g.appendChild(title);

    // Nodes
    cols.forEach(function (col, ci) {
      var cx = startX + ci * (boxW + colGap) + boxW / 2;
      var items = col.items;
      var colH = items.length * rowH - rowGap;
      var y0 = midY - colH / 2;

      var hdr = el('text', {
        x: cx,
        y: y0 - 10,
        'text-anchor': 'middle',
        class: 'fabric-mesh-stack-col-label',
        fill: 'var(--color-muted)',
        'font-size': '9',
        'font-weight': '700',
        'letter-spacing': '0.06em',
      });
      hdr.textContent = col.label;
      g.appendChild(hdr);

      items.forEach(function (ct, i) {
        var id = String(ct.id || ct.name || i);
        var y = y0 + i * rowH + boxH / 2;
        pos[id] = { x: cx, y: y, col: ci };
        if (ct.name) pos[String(ct.name)] = pos[id];

        var rk = roleKey(ct);
        var colors = ROLE_COLORS[rk] || ROLE_COLORS.other;
        var rlab = (ct.role_label || colors.label || rk).toLowerCase();
        if (rlab === 'data') rlab = 'db';

        var tip = [ct.name || id, rlab];
        if (ct.ports_label) tip.push('ports ' + ct.ports_label);
        if (ct.kuma_state) tip.push('kuma ' + ct.kuma_state);
        if (ct.image) tip.push(ct.image);

        var ng = el('g', {
          class:
            'fabric-mesh-stack-node fabric-mesh-stack-node--' +
            rk +
            (ct.running ? ' is-running' : ' is-stopped'),
          'data-path-id': String(pathId),
          'data-stack-container': id,
          'data-stack-role': rlab,
          style: 'cursor:pointer',
        });
        var tEl = el('title');
        tEl.textContent = tip.join(' · ') + ' · click for detail';
        ng.appendChild(tEl);

        // Main box — inline colors so focus CSS cannot wash them out
        ng.appendChild(
          el('rect', {
            x: cx - boxW / 2,
            y: y - boxH / 2,
            width: boxW,
            height: boxH,
            rx: 9,
            class: 'fabric-mesh-stack-box',
            fill: colors.fill,
            stroke: colors.stroke,
            'stroke-width': '1.75',
          })
        );
        // Type chip
        ng.appendChild(
          el('rect', {
            x: cx - boxW / 2 + 5,
            y: y - boxH / 2 + 5,
            width: 32,
            height: 13,
            rx: 3,
            fill: colors.chip,
            stroke: 'none',
          })
        );
        var typeT = el('text', {
          x: cx - boxW / 2 + 21,
          y: y - boxH / 2 + 14.5,
          'text-anchor': 'middle',
          'font-size': '7.5',
          'font-weight': '800',
          fill: colors.stroke,
          style: 'letter-spacing:0.04em',
        });
        typeT.textContent = String(rlab).slice(0, 5).toUpperCase();
        ng.appendChild(typeT);

        // Run dot
        ng.appendChild(
          el('circle', {
            cx: cx + boxW / 2 - 10,
            cy: y - boxH / 2 + 11,
            r: 3.5,
            fill: ct.running ? '#059669' : '#d97706',
            stroke: 'none',
          })
        );

        var nameT = el('text', {
          x: cx + 4,
          y: y + 13,
          'text-anchor': 'middle',
          'font-size': '10.5',
          'font-weight': '650',
          fill: 'var(--color-text)',
          class: 'fabric-mesh-stack-label',
        });
        nameT.textContent = String(ct.name || id).slice(0, 13);
        ng.appendChild(nameT);

        // Optional ports under name for app/edge only if space — skip to avoid clutter

        g.appendChild(ng);
      });
    });

    function hasConfirmed(frm, to) {
      for (var i = 0; i < edges.length; i++) {
        if (String(edges[i].from) === frm && String(edges[i].to) === to) return true;
        if (String(edges[i].from) === to && String(edges[i].to) === frm) return true;
      }
      return false;
    }

    var drawn = {};
    function drawLink(frm, to, solid, source) {
      var A = pos[String(frm)];
      var B = pos[String(to)];
      if (!A || !B) return;
      var key = frm + '=>' + to + (solid ? 's' : 'd');
      if (drawn[frm + '=>' + to] || drawn[to + '=>' + frm]) return;
      drawn[frm + '=>' + to] = true;

      var left = A.col <= B.col ? A : B;
      var right = A.col <= B.col ? B : A;
      var x1 = left.x + boxW / 2 + 1;
      var y1 = left.y;
      var x2 = right.x - boxW / 2 - 1;
      var y2 = right.y;
      var mx = (x1 + x2) / 2;
      var d =
        'M ' + x1 + ' ' + y1 + ' C ' + mx + ' ' + y1 + ', ' + mx + ' ' + y2 + ', ' + x2 + ' ' + y2;

      // Confirmed: strong solid; soft structure: clearer dashed (still secondary)
      var stroke = solid
        ? source === 'manual'
          ? '#d97706'
          : '#0d9488'
        : '#64748b';
      g.appendChild(
        el('path', {
          d: d,
          fill: 'none',
          stroke: stroke,
          'stroke-width': solid ? '2.75' : '2',
          'stroke-dasharray': solid ? 'none' : '4 3',
          'stroke-opacity': solid ? '1' : '0.88',
          'stroke-linecap': 'round',
          class: solid
            ? 'fabric-mesh-edge fabric-mesh-edge--stack-dep'
            : 'fabric-mesh-edge fabric-mesh-edge--stack-struct',
          'data-path-id': String(pathId),
        })
      );
    }

    function idsOf(col) {
      return (col && col.items ? col.items : []).map(function (c) {
        return String(c.id || c.name);
      });
    }

    function soft(fromIds, toIds) {
      fromIds.forEach(function (f) {
        toIds.forEach(function (t) {
          if (!hasConfirmed(f, t) && !hasConfirmed(t, f)) drawLink(f, t, false);
        });
      });
    }
    // Soft structure follows visual left→right column order (not fixed role pairs),
    // so when queue is last the lines don't reverse across the data column.
    for (var ci = 0; ci < cols.length - 1; ci++) {
      soft(idsOf(cols[ci]), idsOf(cols[ci + 1]));
    }

    edges.forEach(function (e) {
      drawLink(String(e.from), String(e.to), true, e.source || 'accepted');
    });

    // Click → Stack detail
    g.querySelectorAll('.fabric-mesh-stack-node').forEach(function (ng) {
      ng.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var cid = ng.getAttribute('data-stack-container') || '';
        var url =
          '/dns/stack-panel?service_id=' +
          encodeURIComponent(String(pathId)) +
          (cid ? '&focus_container=' + encodeURIComponent(cid) : '');
        if (window.PiHerderStackPanel && window.PiHerderStackPanel.open) {
          window.PiHerderStackPanel.open(url);
        }
      });
    });

    svg.appendChild(g);

    var lastX = startX + zoneW;
    var minY = zoneTop - 8;
    var maxY = zoneTop + zoneH + 12;
    ensureViewBox(svg, lastX + 20, maxY, minY);
  }

  function loadAndDraw(root, pathId) {
    var svg = svgRoot(root);
    if (!svg || pathId == null || pathId === '' || String(pathId).indexOf('n:') === 0) {
      if (svg) {
        clearLayer(svg);
        restoreViewBox(svg);
      }
      return;
    }
    var id = String(pathId);
    if (!/^\d+$/.test(id)) {
      clearLayer(svg);
      restoreViewBox(svg);
      return;
    }

    function apply(data) {
      if (!data || !data.ok) {
        clearLayer(svg);
        restoreViewBox(svg);
        return;
      }
      draw(svg, id, data);
    }

    if (cache[id]) {
      apply(cache[id]);
      return;
    }

    fetch('/dns/stack-expand.json?service_id=' + encodeURIComponent(id), {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data && data.ok) cache[id] = data;
        if (root._fabricFocusId != null && String(root._fabricFocusId) === id) {
          apply(data);
        }
      })
      .catch(function () {});
  }

  function onFocus(root, pathId) {
    loadAndDraw(root, pathId);
  }

  function onClear(root) {
    var svg =
      (root && svgRoot(root)) ||
      document.querySelector('svg.fabric-mesh-svg--logical') ||
      document.querySelector('svg[data-fabric-mesh="physical"]');
    if (svg) {
      clearLayer(svg);
      restoreViewBox(svg);
    }
  }

  function boot() {
    document.querySelectorAll('[data-fabric-root]').forEach(function (root) {
      var init = (root.getAttribute('data-fabric-initial-focus') || '').trim();
      if (init && /^\d+$/.test(init)) {
        setTimeout(function () {
          onFocus(root, init);
        }, 450);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  window.PiHerderStackExpand = {
    show: function (pathId) {
      var root =
        document.querySelector('[data-fabric-root].is-focusing') ||
        document.querySelector('[data-fabric-root]');
      if (root) onFocus(root, pathId);
    },
    clear: function () {
      onClear(document.querySelector('[data-fabric-root]'));
    },
    invalidate: function (pathId) {
      if (pathId != null) delete cache[String(pathId)];
      else cache = {};
    },
  };
})();
