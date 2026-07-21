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
   * Sideways category columns from vocab order (hide empty).
   * Fallback: classic edge | app | queue | data(+cache+tooling).
   * Custom stack order can still reorder columns by min order_index.
   */
  function buildColumns(containers, hasCustomOrder, categoryColumns) {
    var buckets = {};
    var defaultOrder = ['edge', 'app', 'queue', 'cache', 'data', 'tooling', 'other'];
    var colMeta = [];
    if (categoryColumns && categoryColumns.length) {
      categoryColumns.forEach(function (c, i) {
        var k = (c.key || '').toLowerCase();
        if (!k) return;
        colMeta.push({ key: k, label: (c.label || k).toLowerCase(), defaultRank: i });
        buckets[k] = [];
      });
    } else {
      defaultOrder.forEach(function (k, i) {
        colMeta.push({ key: k, label: k === 'data' ? 'db' : k, defaultRank: i });
        buckets[k] = [];
      });
    }
    if (!buckets.other) {
      colMeta.push({ key: 'other', label: 'svc', defaultRank: 99 });
      buckets.other = [];
    }

    (containers || []).forEach(function (c) {
      var r = roleKey(c);
      if (!buckets[r]) {
        buckets.other.push(c);
      } else {
        buckets[r].push(c);
      }
    });
    Object.keys(buckets).forEach(function (k) {
      buckets[k].sort(byOrderThenName);
    });

    var candidates = [];
    colMeta.forEach(function (meta) {
      var items = buckets[meta.key] || [];
      if (!items.length) return;
      candidates.push({
        key: meta.key,
        label: meta.label || meta.key,
        items: items,
        defaultRank: meta.defaultRank,
      });
    });

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

  function viewGroupKey(ct) {
    var vid = ct && ct.visual_stack_id;
    if (vid == null || vid === '' || vid === 'main') return 'main';
    return String(vid);
  }

  function groupHasCustomOrder(containers) {
    var list = containers || [];
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      if (c && (c.custom_ordered || c.order_index != null)) return true;
    }
    return false;
  }

  /** Stable 0..n-1 ranks within one view-group fan (matches panel drag order). */
  function renumberGroupOrder(containers) {
    var list = (containers || []).slice();
    list.sort(byOrderThenName);
    list.forEach(function (c, i) {
      c.order_index = i;
      c.custom_ordered = true;
    });
    return list;
  }

  function partitionViewGroups(data) {
    var multi = data.multi_view || [];
    var containers = data.containers || [];
    // Prefer server multi_view order when All and 2+ groups have members
    if (multi && multi.length >= 2) {
      return multi.map(function (m) {
        var key = String(m.key);
        var members = containers.filter(function (c) {
          return viewGroupKey(c) === key;
        });
        if (groupHasCustomOrder(members)) {
          members = renumberGroupOrder(members);
        }
        return {
          key: key,
          name: m.name || key,
          containers: members,
        };
      }).filter(function (g) {
        return g.containers.length;
      });
    }
    // Single fan (filtered group, or everything still on Main)
    var single = containers.slice();
    if (groupHasCustomOrder(single)) {
      single = renumberGroupOrder(single);
    }
    return [
      {
        key: 'all',
        name: (data.project || 'stack') + ' · runtime',
        containers: single,
      },
    ];
  }

  function measureFan(cols, boxW, colGap, rowH, rowGap, zonePadTop, zonePadBot) {
    var maxItems = 1;
    cols.forEach(function (c) {
      maxItems = Math.max(maxItems, c.items.length);
    });
    var nodesH = maxItems * rowH - rowGap;
    var zoneW = Math.max(1, cols.length) * boxW + Math.max(0, cols.length - 1) * colGap + 32;
    var zoneH = zonePadTop + nodesH + zonePadBot;
    return { maxItems: maxItems, nodesH: nodesH, zoneW: zoneW, zoneH: zoneH };
  }

  function drawFan(g, pathId, opts) {
    var cols = opts.cols;
    var edges = opts.edges || [];
    var startX = opts.startX;
    var midY = opts.midY;
    var zoneTop = opts.zoneTop;
    var zoneW = opts.zoneW;
    var zoneH = opts.zoneH;
    var titleText = opts.titleText;
    var boxW = opts.boxW;
    var boxH = opts.boxH;
    var colGap = opts.colGap;
    var rowH = opts.rowH;
    var pos = opts.pos;
    var zoneClass = opts.zoneClass || '';

    g.appendChild(
      el('rect', {
        x: startX - 16,
        y: zoneTop,
        width: zoneW,
        height: zoneH,
        rx: 14,
        class: 'fabric-mesh-stack-zone' + (zoneClass ? ' ' + zoneClass : ''),
        fill: 'var(--color-surface)',
        'fill-opacity': '0.55',
        stroke: 'var(--color-border)',
        'stroke-opacity': '0.75',
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
    title.textContent = titleText;
    g.appendChild(title);

    cols.forEach(function (col, ci) {
      var cx = startX + ci * (boxW + colGap) + boxW / 2;
      var items = col.items;
      var colH = items.length * rowH - (opts.rowGap || 16);
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
        // Namespace pos by fan so multi-view same service names don't collide
        var posKey = opts.posPrefix ? opts.posPrefix + '::' + id : id;
        pos[posKey] = { x: cx, y: y, col: ci, fan: opts.posPrefix || '' };
        if (!opts.posPrefix) {
          pos[id] = pos[posKey];
          if (ct.name) pos[String(ct.name)] = pos[id];
        } else {
          // Within-fan lookup for edges
          pos[opts.posPrefix + '::' + id] = pos[posKey];
        }

        var rk = roleKey(ct);
        var colors = ROLE_COLORS[rk] || ROLE_COLORS.other;
        var rlab = (ct.role_label || colors.label || rk).toLowerCase();
        if (rlab === 'data') rlab = 'db';

        var tip = [ct.name || id, rlab];
        if (ct.visual_stack_name) tip.push('view ' + ct.visual_stack_name);
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
    function resolvePos(name) {
      if (opts.posPrefix) {
        return pos[opts.posPrefix + '::' + name] || pos[name];
      }
      return pos[name];
    }
    function drawLink(frm, to, solid, source) {
      var A = resolvePos(String(frm));
      var B = resolvePos(String(to));
      if (!A || !B) return;
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
    for (var ci = 0; ci < cols.length - 1; ci++) {
      soft(idsOf(cols[ci]), idsOf(cols[ci + 1]));
    }

    // Edges only within this fan's containers
    var fanIds = {};
    cols.forEach(function (col) {
      idsOf(col).forEach(function (id) {
        fanIds[id] = true;
      });
    });
    edges.forEach(function (e) {
      var f = String(e.from);
      var t = String(e.to);
      if (fanIds[f] && fanIds[t]) {
        drawLink(f, t, true, e.source || 'accepted');
      }
    });
  }

  function draw(svg, pathId, data) {
    clearLayer(svg);
    if (!data || !data.ok) return;
    var anchor = anchorNode(svg, pathId);
    if (!anchor) return;

    var a = anchorGeom(anchor);
    var globalCustom = !!(
      data.has_custom_order ||
      (data.custom_order && data.custom_order.length) ||
      groupHasCustomOrder(data.containers || [])
    );
    var edges = data.edges || [];
    var groups = partitionViewGroups(data);

    var boxW = 118;
    var boxH = 42;
    var colGap = 72;
    var rowGap = 16;
    var rowH = boxH + rowGap;
    var headerBand = 20;
    var titleBand = 18;
    var zonePadTop = titleBand + headerBand + 12;
    var zonePadBot = 16;
    var startX = a.right + 72;
    var fanGap = 28;

    var measured = groups.map(function (grp) {
      // Per-fan: e2e custom order must drive columns even if Main has none
      var fanCustom = globalCustom || groupHasCustomOrder(grp.containers);
      var cols = buildColumns(
        grp.containers,
        fanCustom,
        data.category_columns || null
      );
      var m = measureFan(cols, boxW, colGap, rowH, rowGap, zonePadTop, zonePadBot);
      return { grp: grp, cols: cols, m: m };
    });

    var totalH = 0;
    measured.forEach(function (x, i) {
      totalH += x.m.zoneH;
      if (i) totalH += fanGap;
    });
    var maxZoneW = 120;
    measured.forEach(function (x) {
      maxZoneW = Math.max(maxZoneW, x.m.zoneW);
    });

    var stackTop = a.y - totalH / 2;
    if (stackTop < a.top - 28) {
      stackTop = a.top - 28;
    }

    var pos = {};
    var g = el('g', {
      id: layerId,
      class: 'fabric-stack-expand-layer',
      'data-path-id': String(pathId),
    });

    // Lead line to first fan mid
    var firstMidY = stackTop + measured[0].m.zoneH / 2;
    g.appendChild(
      el('line', {
        x1: a.right + 2,
        y1: a.y,
        x2: startX - 16,
        y2: firstMidY,
        class: 'fabric-mesh-edge fabric-mesh-edge--stack-lead',
        'data-path-id': String(pathId),
        stroke: '#64748b',
        'stroke-width': '2',
        'stroke-dasharray': '5 3',
        'stroke-opacity': '0.9',
        fill: 'none',
      })
    );

    var yCursor = stackTop;
    measured.forEach(function (x, gi) {
      var zoneTop = yCursor;
      var midY = zoneTop + zonePadTop + x.m.nodesH / 2;
      var titleText =
        groups.length > 1
          ? (data.project || 'stack') + ' · ' + (x.grp.name || 'view')
          : x.grp.name || (data.project || 'stack') + ' · runtime';
      drawFan(g, pathId, {
        cols: x.cols,
        edges: edges,
        startX: startX,
        midY: midY,
        zoneTop: zoneTop,
        zoneW: x.m.zoneW,
        zoneH: x.m.zoneH,
        titleText: titleText,
        boxW: boxW,
        boxH: boxH,
        colGap: colGap,
        rowH: rowH,
        rowGap: rowGap,
        pos: pos,
        posPrefix: groups.length > 1 ? x.grp.key : '',
        zoneClass: groups.length > 1 ? 'fabric-mesh-stack-zone--view' : '',
      });
      yCursor += x.m.zoneH + fanGap;
    });

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

    var lastX = startX + maxZoneW;
    var minY = stackTop - 8;
    var maxY = yCursor - fanGap + 12;
    ensureViewBox(svg, lastX + 20, maxY, minY);
  }

  function cacheKey(pathId, visualStack) {
    return String(pathId) + '::' + (visualStack == null || visualStack === '' ? 'all' : String(visualStack));
  }

  function loadAndDraw(root, pathId, visualStack) {
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
    var vs =
      visualStack != null && visualStack !== ''
        ? String(visualStack)
        : root._fabricVisualStack != null
          ? String(root._fabricVisualStack)
          : 'all';
    root._fabricVisualStack = vs;
    var ck = cacheKey(id, vs);

    function apply(data) {
      if (!data || !data.ok) {
        clearLayer(svg);
        restoreViewBox(svg);
        return;
      }
      draw(svg, id, data);
    }

    if (cache[ck]) {
      apply(cache[ck]);
      return;
    }

    var url =
      '/dns/stack-expand.json?service_id=' +
      encodeURIComponent(id) +
      '&visual_stack=' +
      encodeURIComponent(vs) +
      '&_=' +
      String(Date.now());
    fetch(url, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json', 'Cache-Control': 'no-cache' },
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data && data.ok) cache[ck] = data;
        if (root._fabricFocusId != null && String(root._fabricFocusId) === id) {
          apply(data);
        }
      })
      .catch(function () {});
  }

  function onFocus(root, pathId, visualStack) {
    loadAndDraw(root, pathId, visualStack);
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
    show: function (pathId, visualStack) {
      var root =
        document.querySelector('[data-fabric-root].is-focusing') ||
        document.querySelector('[data-fabric-root]');
      if (root) onFocus(root, pathId, visualStack);
    },
    clear: function () {
      onClear(document.querySelector('[data-fabric-root]'));
    },
    invalidate: function (pathId) {
      if (pathId == null) {
        cache = {};
        return;
      }
      var prefix = String(pathId) + '::';
      Object.keys(cache).forEach(function (k) {
        if (k === String(pathId) || k.indexOf(prefix) === 0) delete cache[k];
      });
    },
  };
})();
