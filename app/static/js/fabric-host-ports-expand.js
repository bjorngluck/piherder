/**
 * Host / discovered-device ports on the Hosts map.
 *
 * Progressive depth (touch-friendly):
 *   1. compact — small callout; tap whole box → ports
 *   2. ports   — ports-only list on the map
 *   3. full    — service/stack fan (follow-on)
 * Path/service focus can also request a project-scoped compact callout.
 */
(function () {
  'use strict';

  var layerId = 'fabric-host-ports-expand-layer';
  var NS = 'http://www.w3.org/2000/svg';
  var cache = {};
  var pending = null;
  /** @type {Record<string, 'compact'|'ports'|'full'>} */
  var viewMode = {};
  var lastKey = '';
  var VALID_MODES = { compact: 1, ports: 1, full: 1 };

  var ROLE = {
    web: { stroke: '#059669', fill: '#05966918', chip: '#059669' },
    dns: { stroke: '#2563eb', fill: '#2563eb18', chip: '#2563eb' },
    db: { stroke: '#7c3aed', fill: '#7c3aed18', chip: '#7c3aed' },
    cache: { stroke: '#d97706', fill: '#d9770618', chip: '#d97706' },
    proxy: { stroke: '#ea580c', fill: '#ea580c18', chip: '#ea580c' },
    ssh: { stroke: '#64748b', fill: '#64748b14', chip: '#64748b' },
    metrics: { stroke: '#0d9488', fill: '#0d948818', chip: '#0d9488' },
    other: { stroke: '#64748b', fill: '#64748b12', chip: '#64748b' },
  };

  function el(name, attrs) {
    var n = document.createElementNS(NS, name);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (attrs[k] != null && attrs[k] !== '') n.setAttribute(k, String(attrs[k]));
      });
    }
    return n;
  }

  function svgRoot(root) {
    if (!root) return null;
    return (
      root.querySelector('svg[data-fabric-mesh="physical"]') ||
      root.querySelector('svg.fabric-mesh-svg')
    );
  }

  function hostsCompactLayout() {
    var cb = document.getElementById('hosts-map-show-discovered');
    return !!(cb && !cb.checked);
  }

  function hostGeom(nodeG) {
    var rect = nodeG && nodeG.querySelector('.fabric-mesh-node-pop > rect, rect');
    var w = 124;
    var h = 72;
    if (rect) {
      w = parseFloat(rect.getAttribute('width') || '124') || 124;
      h = parseFloat(rect.getAttribute('height') || '72') || 72;
    }
    var cx = NaN;
    var cy = NaN;
    if (nodeG && hostsCompactLayout() && nodeG.getAttribute('data-layout-dual') === '1') {
      cx = parseFloat(nodeG.getAttribute('data-x-compact'));
      cy = parseFloat(nodeG.getAttribute('data-y-compact'));
    }
    if (isNaN(cx) || isNaN(cy)) {
      if (nodeG && nodeG.getAttribute('data-x-full') != null) {
        cx = parseFloat(nodeG.getAttribute('data-x-full'));
        cy = parseFloat(nodeG.getAttribute('data-y-full'));
      }
    }
    if ((isNaN(cx) || isNaN(cy)) && rect) {
      cx = parseFloat(rect.getAttribute('x') || '0') + w / 2;
      cy = parseFloat(rect.getAttribute('y') || '0') + h / 2;
    }
    if (isNaN(cx) || isNaN(cy)) {
      return { x: 400, y: 200, right: 462, left: 338, top: 164, bottom: 236 };
    }
    return {
      x: cx,
      y: cy,
      right: cx + w / 2,
      left: cx - w / 2,
      top: cy - h / 2,
      bottom: cy + h / 2,
    };
  }

  function clearLayer(svg) {
    if (!svg) return;
    var old = svg.querySelector('#' + layerId);
    if (old && old.parentNode) old.parentNode.removeChild(old);
  }

  function roleStyle(role) {
    var r = (role || 'other').toLowerCase();
    return ROLE[r] || ROLE.other;
  }

  function findHostNode(svg, data) {
    if (!svg || !data) return null;
    var nid = data.node_id || '';
    if (nid) {
      var byId = svg.querySelector('[data-node-id="' + String(nid).replace(/"/g, '') + '"]');
      if (byId) return byId;
    }
    var sid = data.server_id != null ? String(data.server_id) : '';
    if (sid) {
      var nodes = svg.querySelectorAll('.fabric-mesh-node--host[data-server-id]');
      for (var i = 0; i < nodes.length; i++) {
        if (String(nodes[i].getAttribute('data-server-id')) === sid) return nodes[i];
      }
    }
    var did = data.nmap_device_id != null ? String(data.nmap_device_id) : '';
    if (did) {
      var dnodes = svg.querySelectorAll('.fabric-mesh-node--host[data-discovery-id]');
      for (var j = 0; j < dnodes.length; j++) {
        if (String(dnodes[j].getAttribute('data-discovery-id')) === did) return dnodes[j];
      }
      var alt = svg.querySelector('[data-node-id="host-d-' + did + '"]');
      if (alt) return alt;
    }
    return null;
  }

  function cacheKey(opts) {
    opts = opts || {};
    if (opts.serverId) {
      var k = 'h:' + String(opts.serverId);
      if (opts.focusProject) k += ':p:' + String(opts.focusProject).toLowerCase();
      if (opts.focusContainer) k += ':c:' + String(opts.focusContainer).toLowerCase();
      return k;
    }
    if (opts.deviceId) return 'd:' + String(opts.deviceId);
    return '';
  }

  function openPanel(url) {
    if (!url) return;
    if (window.PiHerderStackPanel && window.PiHerderStackPanel.open) {
      window.PiHerderStackPanel.open(url);
    }
  }

  /**
   * Activate once for mouse / touch / pen.
   *
   * Desktop: pan-zoom used to setPointerCapture on the viewport for every
   * pointerdown, which retargets click away from this overlay. We therefore
   * fire on pointerdown (mouse/pen) *before* that capture, and rely on mesh
   * skipping capture when the hit is this overlay.
   * Mobile: touchend (still reliable on the original target).
   */
  function onActivate(node, fn) {
    if (!node || !fn) return;
    var last = 0;
    function run(ev) {
      if (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
      }
      var now = Date.now();
      // Debounce click after pointerdown / touchend
      if (now - last < 450) return;
      last = now;
      fn(ev);
    }
    // Mouse/pen: act on pointerdown (before viewport setPointerCapture)
    node.addEventListener(
      'pointerdown',
      function (ev) {
        if (ev.pointerType === 'touch') return;
        if (ev.button != null && ev.button !== 0) return;
        run(ev);
      },
      false
    );
    // Fallback if pointerdown was skipped
    node.addEventListener('click', run);
    node.addEventListener(
      'touchend',
      function (ev) {
        run(ev);
      },
      { passive: false }
    );
  }

  function setMode(svg, data, modeKey, mode) {
    viewMode[modeKey] = mode;
    draw(svg, data);
  }

  function allPorts(data) {
    return data.ports_flat || data.ports || data.compact_chips || [];
  }

  function leadLine(g, a, x2) {
    g.appendChild(
      el('line', {
        x1: a.right + 2,
        y1: a.y,
        x2: x2,
        y2: a.y,
        class: 'fabric-mesh-edge fabric-host-ports-lead',
        stroke: 'var(--color-accent, #00a651)',
        'stroke-width': '2.25',
        'stroke-dasharray': '5 3',
        fill: 'none',
      })
    );
  }

  function drawActionBar(g, opts) {
    // Compact chrome — readable, not shouty; hit target still ≥28px
    var x = opts.x;
    var y = opts.y;
    var w = opts.w;
    var h = opts.h != null ? opts.h : 28;
    var label = opts.label || '';
    var primary = !!opts.primary;
    var fontSize = opts.fontSize != null ? opts.fontSize : 9;
    var fontWeight = opts.fontWeight != null ? opts.fontWeight : '600';
    var bg = primary
      ? 'color-mix(in srgb, var(--color-accent, #00a651) 14%, var(--color-surface))'
      : 'color-mix(in srgb, var(--color-muted) 8%, var(--color-surface))';
    var stroke = primary
      ? 'color-mix(in srgb, var(--color-accent, #00a651) 55%, var(--color-border))'
      : 'var(--color-border)';
    var fill = primary
      ? 'var(--color-accent, #00a651)'
      : 'var(--color-muted)';
    var bar = el('g', {
      class: opts.className || 'fabric-host-ports-action',
      style: 'cursor:pointer',
    });
    var tip = el('title');
    tip.textContent = opts.title || label;
    bar.appendChild(tip);
    bar.appendChild(
      el('rect', {
        x: x,
        y: y,
        width: w,
        height: h,
        rx: opts.rx != null ? opts.rx : 6,
        fill: bg,
        stroke: stroke,
        'stroke-width': '1',
      })
    );
    var t = el('text', {
      x: x + w / 2,
      y: y + h / 2 + fontSize * 0.35,
      'text-anchor': 'middle',
      'font-size': String(fontSize),
      'font-weight': fontWeight,
      fill: fill,
      'pointer-events': 'none',
    });
    t.textContent = label;
    bar.appendChild(t);
    if (opts.onActivate) onActivate(bar, opts.onActivate);
    g.appendChild(bar);
    return bar;
  }

  /**
   * Card geometry (content measured top-down, then height from content).
   * pad 10 · header 28–40 · port rows 18 each · bottom 10
   */
  function serviceCardMetrics(svc) {
    var ports = svc.ports || [];
    var n = ports.length + (svc.ports_extra ? 1 : 0);
    var header = 28; // title
    if (svc.detail || svc.kind === 'observed') header += 12;
    header += 6; // gap before ports
    var portsH = Math.max(n, 0) * 18;
    if (!n) portsH = 14; // "no ports"
    var padTop = 10;
    var padBot = 10;
    return {
      h: padTop + header + portsH + padBot,
      padTop: padTop,
      header: header,
      portsH: portsH,
      n: n,
    };
  }

  /** 1 — Compact summary. Whole box taps → ports-only expand. */
  function drawCompact(svg, data, a, modeKey) {
    var chips = data.compact_chips || allPorts(data).slice(0, 6);
    var total = data.total_count || chips.length;
    var extra =
      data.compact_extra != null
        ? data.compact_extra
        : Math.max(0, total - chips.length);
    var cardW = 176;
    var gapFromHost = 72;
    var cardLeft = a.right + gapFromHost;
    var chipH = 16;
    var chipGap = 4;
    var padX = 12;
    var padTop = 12;
    var headerH = 36;
    var hintH = 28; // touch hint strip
    var nShow = Math.min(chips.length, 4); // keep compact short
    var bodyH = nShow ? nShow * (chipH + chipGap) : 16;
    var cardH = padTop + headerH + bodyH + hintH + 12;
    var cardTop = a.y - cardH / 2;

    var g = el('g', {
      id: layerId,
      class: 'fabric-host-ports-expand-layer is-compact',
      'data-server-id': data.server_id != null ? String(data.server_id) : '',
      'data-discovery-id': data.nmap_device_id != null ? String(data.nmap_device_id) : '',
      'data-view': 'compact',
      style: 'cursor:pointer',
    });

    leadLine(g, a, cardLeft - 6);

    // Full-box hit target (behind content) — primary mobile affordance
    var hit = el('rect', {
      x: cardLeft,
      y: cardTop,
      width: cardW,
      height: cardH,
      rx: 12,
      class: 'fabric-host-ports-callout fabric-host-ports-hit',
      fill: 'color-mix(in srgb, var(--color-surface) 92%, var(--color-bg))',
      stroke: 'color-mix(in srgb, var(--color-accent, #00a651) 45%, var(--color-border))',
      'stroke-width': '1.75',
    });
    g.appendChild(hit);

    var tip = el('title');
    tip.textContent = total
      ? 'Tap to show all ports on the map'
      : 'No ports — tap to edit';
    g.appendChild(tip);

    var nameBit = data.server_name || data.device_name || 'Ports';
    var title = el('text', {
      x: cardLeft + padX,
      y: cardTop + padTop + 14,
      class: 'fabric-host-ports-title',
      fill: 'var(--color-text)',
      'font-size': '12',
      'font-weight': '700',
    });
    title.textContent =
      String(total) +
      ' port' +
      (total === 1 ? '' : 's') +
      (data.stack_count
        ? ' · ' + data.stack_count + ' stack' + (data.stack_count === 1 ? '' : 's')
        : '');
    g.appendChild(title);

    var sub = el('text', {
      x: cardLeft + padX,
      y: cardTop + padTop + 28,
      fill: 'var(--color-muted)',
      'font-size': '9',
      'font-weight': '600',
    });
    sub.textContent = String(nameBit).slice(0, 22);
    g.appendChild(sub);

    var y = cardTop + padTop + headerH + 2;
    var contentX = cardLeft + padX;
    var chipMaxW = cardW - padX * 2;

    if (!nShow) {
      var none = el('text', {
        x: contentX,
        y: y + 12,
        fill: 'var(--color-muted)',
        'font-size': '10',
      });
      none.textContent = 'No open ports observed';
      g.appendChild(none);
    } else {
      for (var i = 0; i < nShow; i++) {
        var p = chips[i];
        var rs = roleStyle(p.role);
        var chipY = y;
        g.appendChild(
          el('rect', {
            x: contentX,
            y: chipY,
            width: chipMaxW,
            height: chipH,
            rx: 4,
            fill: rs.fill,
            stroke: rs.stroke,
            'stroke-width': p.role_sticky ? '1.5' : '1',
            'stroke-opacity': '0.85',
            'pointer-events': 'none',
          })
        );
        g.appendChild(
          el('rect', {
            x: contentX + 3,
            y: chipY + 3,
            width: 3,
            height: chipH - 6,
            rx: 1,
            fill: rs.stroke,
            stroke: 'none',
            'pointer-events': 'none',
          })
        );
        var pl = el('text', {
          x: contentX + 12,
          y: chipY + 12,
          'text-anchor': 'start',
          'font-size': '10',
          'font-weight': '650',
          fill: 'var(--color-text)',
          'font-family': 'ui-monospace, Menlo, monospace',
          'pointer-events': 'none',
        });
        var roleBit =
          p.role && p.role !== 'other'
            ? '  ' + String(p.role_label || p.role)
            : '';
        pl.textContent =
          String(p.host_port) +
          (p.proto && p.proto !== 'tcp' ? '/' + p.proto : '') +
          roleBit +
          (p.role_sticky ? ' ★' : '');
        g.appendChild(pl);
        y += chipH + chipGap;
      }
      if (extra > 0) {
        var more = el('text', {
          x: contentX + 4,
          y: y + 2,
          fill: 'var(--color-muted)',
          'font-size': '9.5',
          'font-weight': '600',
          'pointer-events': 'none',
        });
        more.textContent = '+' + extra + ' more · tap box';
        g.appendChild(more);
      }
    }

    // Hint strip (part of whole-box target, not a separate tiny button)
    var hintY = cardTop + cardH - hintH - 6;
    g.appendChild(
      el('rect', {
        x: cardLeft + padX,
        y: hintY,
        width: cardW - padX * 2,
        height: hintH,
        rx: 8,
        fill: 'color-mix(in srgb, var(--color-accent, #00a651) 16%, var(--color-surface))',
        stroke: 'var(--color-accent, #00a651)',
        'stroke-width': '1.25',
        'pointer-events': 'none',
      })
    );
    var hintT = el('text', {
      x: cardLeft + cardW / 2,
      y: hintY + hintH / 2 + 4,
      'text-anchor': 'middle',
      'font-size': '11',
      'font-weight': '700',
      fill: 'var(--color-accent, #00a651)',
      'pointer-events': 'none',
    });
    hintT.textContent = total ? 'Tap for ports ▸' : 'Tap to edit ▸';
    g.appendChild(hintT);

    onActivate(g, function () {
      if (!total) {
        openPanel(data.panel_url);
        return;
      }
      setMode(svg, data, modeKey, 'ports');
    });

    svg.appendChild(g);
  }

  /** 2 — Ports-only expand on the map (no service cards yet). */
  function drawPorts(svg, data, a, modeKey) {
    var ports = allPorts(data);
    var total = data.total_count || ports.length;
    var cardW = 188;
    var gapFromHost = 80;
    var cardLeft = a.right + gapFromHost;
    var padX = 12;
    var padTop = 10;
    var headerH = 36;
    var rowH = 28; // port rows stay roomy; chrome stays compact
    var rowGap = 4;
    var maxShow = 14;
    var shown = ports.slice(0, maxShow);
    var extra = Math.max(0, ports.length - shown.length);
    var btnH = 26;
    var btnGap = 6;
    // Footer: Edit always; Services when stacks exist (side-by-side)
    var hasStacks = (data.stack_count || 0) > 0;
    var footerH = 10 + btnH + 10; // pad + row of pills + pad
    var bodyH = shown.length
      ? shown.length * (rowH + rowGap) - rowGap + (extra ? 14 : 0)
      : 20;
    var cardH = padTop + headerH + bodyH + footerH + 4;
    // Cap height; if many ports, show fewer rows
    var maxCardH = 420;
    if (cardH > maxCardH && shown.length > 4) {
      var avail = maxCardH - padTop - headerH - footerH - 4;
      var fit = Math.max(4, Math.floor((avail + rowGap) / (rowH + rowGap)));
      shown = ports.slice(0, fit);
      extra = Math.max(0, ports.length - shown.length);
      bodyH =
        shown.length * (rowH + rowGap) - rowGap + (extra ? 14 : 0);
      cardH = padTop + headerH + bodyH + footerH + 4;
    }
    var cardTop = a.y - cardH / 2;

    var g = el('g', {
      id: layerId,
      class: 'fabric-host-ports-expand-layer is-ports',
      'data-server-id': data.server_id != null ? String(data.server_id) : '',
      'data-discovery-id': data.nmap_device_id != null ? String(data.nmap_device_id) : '',
      'data-view': 'ports',
    });

    leadLine(g, a, cardLeft - 6);

    g.appendChild(
      el('rect', {
        x: cardLeft,
        y: cardTop,
        width: cardW,
        height: cardH,
        rx: 12,
        class: 'fabric-host-ports-callout fabric-host-ports-ports-panel',
        fill: 'color-mix(in srgb, var(--color-surface) 94%, var(--color-bg))',
        stroke: 'color-mix(in srgb, var(--color-accent, #00a651) 40%, var(--color-border))',
        'stroke-width': '1.5',
      })
    );

    // Title leaves room for small Back pill (no overlap)
    var title = el('text', {
      x: cardLeft + padX,
      y: cardTop + padTop + 12,
      class: 'fabric-host-ports-title',
      fill: 'var(--color-text)',
      'font-size': '11',
      'font-weight': '700',
    });
    title.textContent =
      String(total) + ' port' + (total === 1 ? '' : 's');
    g.appendChild(title);

    var sub = el('text', {
      x: cardLeft + padX,
      y: cardTop + padTop + 24,
      fill: 'var(--color-muted)',
      'font-size': '8.5',
      'font-weight': '600',
    });
    sub.textContent = String(
      data.server_name || data.device_name || ''
    ).slice(0, 18);
    g.appendChild(sub);

    // Compact Back pill (muted, small type — separate from content)
    drawActionBar(g, {
      x: cardLeft + cardW - 48,
      y: cardTop + 8,
      w: 36,
      h: 22,
      label: 'Back',
      title: 'Back to compact summary',
      fontSize: 8,
      fontWeight: '600',
      rx: 5,
      className: 'fabric-host-ports-collapse-btn',
      onActivate: function () {
        setMode(svg, data, modeKey, 'compact');
      },
    });

    var y = cardTop + padTop + headerH + 2;
    var contentX = cardLeft + padX;
    var chipMaxW = cardW - padX * 2;

    if (!shown.length) {
      var none = el('text', {
        x: contentX,
        y: y + 14,
        fill: 'var(--color-muted)',
        'font-size': '10',
      });
      none.textContent = 'No open ports';
      g.appendChild(none);
    } else {
      shown.forEach(function (p) {
        var rs = roleStyle(p.role);
        var chipY = y;
        var row = el('g', {
          class: 'fabric-host-port-row',
          style: 'cursor:pointer',
        });
        row.appendChild(
          el('rect', {
            x: contentX,
            y: chipY,
            width: chipMaxW,
            height: rowH,
            rx: 6,
            fill: rs.fill,
            stroke: rs.stroke,
            'stroke-width': p.role_sticky ? '1.5' : '1',
            'stroke-opacity': '0.9',
          })
        );
        row.appendChild(
          el('rect', {
            x: contentX + 4,
            y: chipY + 6,
            width: 4,
            height: rowH - 12,
            rx: 1,
            fill: rs.stroke,
            stroke: 'none',
          })
        );
        var pl = el('text', {
          x: contentX + 14,
          y: chipY + (p.owner ? 12 : 18),
          'text-anchor': 'start',
          'font-size': '11',
          'font-weight': '650',
          fill: 'var(--color-text)',
          'font-family': 'ui-monospace, Menlo, monospace',
        });
        var roleBit =
          p.role && p.role !== 'other'
            ? '  ' + String(p.role_label || p.role)
            : '';
        pl.textContent =
          String(p.host_port) +
          (p.proto && p.proto !== 'tcp' ? '/' + p.proto : '') +
          roleBit +
          (p.role_sticky ? ' ★' : '');
        row.appendChild(pl);
        if (p.owner) {
          var ow = el('text', {
            x: contentX + 14,
            y: chipY + 23,
            'text-anchor': 'start',
            'font-size': '8',
            'font-weight': '600',
            fill: 'var(--color-muted)',
          });
          ow.textContent = String(p.owner).slice(0, 22);
          row.appendChild(ow);
        }
        var rTip = el('title');
        rTip.textContent =
          String(p.host_port) +
          (p.owner ? ' · ' + p.owner : '') +
          ' · tap to edit roles';
        row.appendChild(rTip);
        onActivate(row, function () {
          openPanel(data.panel_url);
        });
        g.appendChild(row);
        y += rowH + rowGap;
      });
      if (extra > 0) {
        var more = el('text', {
          x: contentX + 4,
          y: y + 9,
          fill: 'var(--color-muted)',
          'font-size': '8.5',
          'font-weight': '600',
        });
        more.textContent = '+' + extra + ' more · Edit';
        g.appendChild(more);
        y += 14;
      }
    }

    // Footer chrome: hairline separator so pills don't blend into port rows
    var footerTop = cardTop + cardH - footerH;
    g.appendChild(
      el('line', {
        x1: cardLeft + padX,
        y1: footerTop + 2,
        x2: cardLeft + cardW - padX,
        y2: footerTop + 2,
        stroke: 'var(--color-border)',
        'stroke-width': '1',
        'stroke-opacity': '0.85',
      })
    );

    var btnY = footerTop + 10;
    var innerW = cardW - padX * 2;
    if (hasStacks) {
      // Side-by-side: Edit (muted) | Services (accent) — short labels, clear gap
      var half = (innerW - btnGap) / 2;
      drawActionBar(g, {
        x: cardLeft + padX,
        y: btnY,
        w: half,
        h: btnH,
        label: 'Edit',
        title: 'Open full port table',
        fontSize: 9,
        primary: false,
        className: 'fabric-host-ports-edit-btn',
        onActivate: function () {
          openPanel(data.panel_url);
        },
      });
      drawActionBar(g, {
        x: cardLeft + padX + half + btnGap,
        y: btnY,
        w: half,
        h: btnH,
        label: 'Services',
        title: 'Show ports grouped by service / stack',
        fontSize: 9,
        primary: true,
        className: 'fabric-host-ports-expand-btn',
        onActivate: function () {
          setMode(svg, data, modeKey, 'full');
        },
      });
    } else {
      drawActionBar(g, {
        x: cardLeft + padX,
        y: btnY,
        w: innerW,
        h: btnH,
        label: 'Edit',
        title: 'Open full port table',
        fontSize: 9,
        primary: true,
        className: 'fabric-host-ports-edit-btn',
        onActivate: function () {
          openPanel(data.panel_url);
        },
      });
    }

    svg.appendChild(g);
  }

  /** 3 — Service / stack fan (follow-on after ports-only). */
  function drawFull(svg, data, a, modeKey) {
    var services = data.services || [];
    if (!services.length && (data.ports || []).length) {
      services = [
        {
          id: 'legacy',
          kind: 'observed',
          label: 'Ports',
          detail: '',
          ports: data.ports,
          port_count: data.ports.length,
          ports_extra: 0,
          project: '',
        },
      ];
    }

    if (!services.length) {
      var gEmpty = el('g', {
        id: layerId,
        class: 'fabric-host-ports-expand-layer',
        'data-server-id': data.server_id != null ? String(data.server_id) : '',
      });
      var te = el('text', {
        x: a.right + 24,
        y: a.y + 4,
        class: 'fabric-host-ports-empty',
        fill: 'var(--color-muted)',
        'font-size': '11',
      });
      te.textContent = data.nmap_device_id
        ? 'No open ports from last scan'
        : 'No published / observed ports — refresh Docker inventory';
      gEmpty.appendChild(te);
      svg.appendChild(gEmpty);
      return;
    }

    var cardW = 168;
    var gapFromHost = 88;
    var cardLeft = a.right + gapFromHost;
    var rowGap = 14;

    var metrics = services.map(serviceCardMetrics);
    var totalH =
      metrics.reduce(function (s, m) {
        return s + m.h;
      }, 0) +
      Math.max(0, services.length - 1) * rowGap;
    // header bar for collapse
    var headerBand = 22;
    var startY = a.y - (totalH + headerBand) / 2 + headerBand;

    var g = el('g', {
      id: layerId,
      class: 'fabric-host-ports-expand-layer is-full',
      'data-server-id': data.server_id != null ? String(data.server_id) : '',
      'data-discovery-id': data.nmap_device_id != null ? String(data.nmap_device_id) : '',
      'data-view': 'full',
    });

    var zonePadX = 14;
    var zonePadY = 18;
    var zoneTop = startY - zonePadY - headerBand - 4;
    var zoneH = totalH + zonePadY * 2 + headerBand + 18;
    g.appendChild(
      el('rect', {
        x: cardLeft - zonePadX,
        y: zoneTop,
        width: cardW + zonePadX * 2,
        height: zoneH,
        rx: 12,
        class: 'fabric-host-ports-zone',
      })
    );

    var title = el('text', {
      x: cardLeft,
      y: zoneTop + 12,
      class: 'fabric-host-ports-title',
      fill: 'var(--color-muted)',
      'font-size': '9',
      'font-weight': '700',
    });
    title.textContent =
      'SERVICES · ' +
      String(data.total_count || 0) +
      ' ports' +
      (data.stack_count ? ' · ' + data.stack_count + ' owners' : '');
    g.appendChild(title);

    // Back to ports-only — compact pill, small type
    drawActionBar(g, {
      x: cardLeft + cardW - 48,
      y: zoneTop + 4,
      w: 40,
      h: 20,
      label: 'Ports',
      title: 'Back to ports-only list',
      fontSize: 8,
      fontWeight: '600',
      rx: 5,
      className: 'fabric-host-ports-collapse-btn',
      onActivate: function () {
        setMode(svg, data, modeKey, 'ports');
      },
    });

    g.appendChild(
      el('line', {
        x1: a.right + 2,
        y1: a.y,
        x2: cardLeft - 6,
        y2: a.y,
        class: 'fabric-mesh-edge fabric-host-ports-lead',
        stroke: 'var(--color-accent, #00a651)',
        'stroke-width': '2.25',
        'stroke-dasharray': '5 3',
        fill: 'none',
      })
    );

    var yCursor = startY;
    services.forEach(function (svc, si) {
      var m = metrics[si];
      var h = m.h;
      var cy = yCursor + h / 2;
      var isObs = svc.kind === 'observed';
      var stroke = isObs ? '#64748b' : 'var(--color-accent, #00a651)';
      var fill = isObs
        ? 'color-mix(in srgb, #64748b 8%, var(--color-surface))'
        : 'color-mix(in srgb, var(--color-accent, #00a651) 10%, var(--color-surface))';

      g.appendChild(
        el('line', {
          x1: a.right + 4,
          y1: a.y,
          x2: cardLeft,
          y2: cy,
          class: 'fabric-mesh-edge fabric-host-ports-edge',
          stroke: stroke,
          'stroke-width': '1.5',
          'stroke-opacity': '0.75',
          fill: 'none',
        })
      );

      var sg = el('g', {
        class:
          'fabric-host-service-node' + (isObs ? ' is-observed' : ' is-service'),
        'data-service-id': svc.id || '',
        'data-project': svc.project || '',
        'data-container': svc.container || svc.service || '',
        style: 'cursor:pointer',
      });
      var tip = el('title');
      tip.textContent =
        (svc.label || '') +
        (svc.detail ? ' / ' + svc.detail : '') +
        ' · ' +
        (svc.port_count || 0) +
        ' port(s) · click to edit';
      sg.appendChild(tip);

      sg.appendChild(
        el('rect', {
          x: cardLeft,
          y: yCursor,
          width: cardW,
          height: h,
          rx: 10,
          class: 'fabric-host-service-box',
          fill: fill,
          stroke: stroke,
          'stroke-width': '1.75',
        })
      );

      var padX = 10;
      var contentX = cardLeft + padX;
      var y = yCursor + m.padTop + 12;

      var tn = el('text', {
        x: contentX,
        y: y,
        'text-anchor': 'start',
        'font-size': '11.5',
        'font-weight': '700',
        fill: 'var(--color-text)',
      });
      tn.textContent = String(svc.label || 'service').slice(0, 18);
      sg.appendChild(tn);
      y += 13;

      if (svc.detail) {
        var det = el('text', {
          x: contentX,
          y: y,
          'text-anchor': 'start',
          'font-size': '9',
          'font-weight': '600',
          fill: 'var(--color-muted)',
          'font-family': 'ui-monospace, Menlo, monospace',
        });
        det.textContent = String(svc.detail).slice(0, 20);
        sg.appendChild(det);
        y += 12;
      } else if (isObs) {
        var od = el('text', {
          x: contentX,
          y: y,
          'text-anchor': 'start',
          'font-size': '8.5',
          fill: 'var(--color-muted)',
        });
        od.textContent = data.nmap_device_id
          ? 'nmap · discovered device'
          : 'nmap · no Docker owner';
        sg.appendChild(od);
        y += 12;
      }

      y += 4;

      var ports = svc.ports || [];
      var chipH = 15;
      var chipGap = 3;
      var chipMaxW = cardW - padX * 2;
      ports.forEach(function (p) {
        var rs = roleStyle(p.role);
        var chipY = y - 10;
        sg.appendChild(
          el('rect', {
            x: contentX,
            y: chipY,
            width: chipMaxW,
            height: chipH,
            rx: 4,
            class: 'fabric-host-port-chip-bg',
            fill: rs.fill,
            stroke: rs.stroke,
            'stroke-width': p.role_sticky ? '1.5' : '1',
            'stroke-opacity': '0.85',
          })
        );
        sg.appendChild(
          el('rect', {
            x: contentX + 3,
            y: chipY + 3,
            width: 3,
            height: chipH - 6,
            rx: 1,
            fill: rs.stroke,
            stroke: 'none',
          })
        );
        var pl = el('text', {
          x: contentX + 12,
          y: chipY + 11,
          'text-anchor': 'start',
          'font-size': '9.5',
          'font-weight': '650',
          fill: 'var(--color-text)',
          'font-family': 'ui-monospace, Menlo, monospace',
        });
        var roleBit =
          p.role && p.role !== 'other'
            ? '  ' + String(p.role_label || p.role)
            : '';
        pl.textContent =
          String(p.host_port) +
          (p.proto && p.proto !== 'tcp' ? '/' + p.proto : '') +
          roleBit +
          (p.role_sticky ? ' ★' : '');
        sg.appendChild(pl);
        y += chipH + chipGap;
      });
      if (svc.ports_extra) {
        var more = el('text', {
          x: contentX + 4,
          y: y,
          'text-anchor': 'start',
          'font-size': '9',
          'font-weight': '600',
          fill: 'var(--color-muted)',
        });
        more.textContent = '+' + svc.ports_extra + ' more';
        sg.appendChild(more);
      }
      if (!ports.length && !svc.ports_extra) {
        var none = el('text', {
          x: contentX + 4,
          y: y,
          'text-anchor': 'start',
          'font-size': '9',
          fill: 'var(--color-muted)',
        });
        none.textContent = 'no ports';
        sg.appendChild(none);
      }

      sg.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var url = data.panel_url || '';
        if (svc.project) {
          url +=
            (url.indexOf('?') >= 0 ? '&' : '?') +
            'focus_project=' +
            encodeURIComponent(svc.project);
        }
        openPanel(url);
      });
      g.appendChild(sg);
      yCursor += h + rowGap;
    });

    svg.appendChild(g);
  }

  function draw(svg, data) {
    clearLayer(svg);
    if (!data || !data.ok) return;

    var hostNode = findHostNode(svg, data);
    if (!hostNode) return;
    var a = hostGeom(hostNode);

    var modeKey =
      cacheKey({
        serverId: data.server_id,
        deviceId: data.nmap_device_id,
        focusProject: data.focus_project,
        focusContainer: data.focus_container,
      }) || lastKey || 'default';
    lastKey = modeKey;
    var mode = viewMode[modeKey] || data.default_view || 'compact';
    if (!VALID_MODES[mode]) mode = 'compact';

    if (mode === 'full') {
      drawFull(svg, data, a, modeKey);
    } else if (mode === 'ports') {
      drawPorts(svg, data, a, modeKey);
    } else {
      drawCompact(svg, data, a, modeKey);
    }
  }

  function loadAndDraw(root, opts) {
    opts = opts || {};
    var svg = svgRoot(root);
    var serverId = opts.serverId || opts.server_id || '';
    var deviceId = opts.deviceId || opts.nmap_device_id || opts.discoveryId || '';
    if (!svg || (!serverId && !deviceId)) return;

    var force = !!opts.force;
    var focusProject = opts.focusProject || opts.focus_project || '';
    var focusContainer = opts.focusContainer || opts.focus_container || '';
    var ck = cacheKey({
      serverId: serverId,
      deviceId: deviceId,
      focusProject: focusProject,
      focusContainer: focusContainer,
    });
    lastKey = ck;

    // Clear stack expand only when showing host-level ports (not project filter
    // alongside path stack — path focus keeps stack expand).
    if (!focusProject && !focusContainer) {
      try {
        if (window.PiHerderStackExpand && window.PiHerderStackExpand.clear) {
          window.PiHerderStackExpand.clear();
        }
      } catch (e) {}
    }

    function apply(data) {
      if (!root || !root.classList.contains('is-focusing')) {
        if (!force) return;
      }
      if (opts.resetView) {
        viewMode[ck] = 'compact';
      }
      draw(svg, data);
    }

    if (!force && !opts.resetView && cache[ck]) {
      apply(cache[ck]);
      return;
    }

    var token = String(Date.now());
    pending = token;
    var url = '/dns/host-ports-expand.json?_=' + token;
    if (serverId) url += '&server_id=' + encodeURIComponent(String(serverId));
    if (deviceId) url += '&nmap_device_id=' + encodeURIComponent(String(deviceId));
    if (focusProject) {
      url += '&focus_project=' + encodeURIComponent(String(focusProject));
    }
    if (focusContainer) {
      url += '&focus_container=' + encodeURIComponent(String(focusContainer));
    }
    fetch(url, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (pending !== token) return;
        if (data && data.ok) cache[ck] = data;
        apply(data);
      })
      .catch(function () {});
  }

  function showForFocus(root, focusId) {
    if (!root || focusId == null) return;
    var fid = String(focusId);
    if (fid.indexOf('n:') !== 0) {
      // Path/service focus — stack expand owns the canvas; no host fan.
      clearLayer(svgRoot(root));
      return;
    }
    var nid = fid.slice(2);
    var node = root.querySelector(
      '[data-node-id="' + nid.replace(/"/g, '') + '"]'
    );
    if (!node || !node.classList.contains('fabric-mesh-node--host')) {
      clearLayer(svgRoot(root));
      return;
    }

    var sid = (node.getAttribute('data-server-id') || '').trim();
    var did = (node.getAttribute('data-discovery-id') || '').trim();

    // Fleet host
    if (sid && /^\d+$/.test(sid) && node.getAttribute('data-discovered') !== '1') {
      loadAndDraw(root, {
        serverId: sid,
        force: false,
        resetView: true,
      });
      return;
    }

    // Discovered device (camera, printer, …) — nmap ports
    if (did && /^\d+$/.test(did)) {
      loadAndDraw(root, {
        deviceId: did,
        force: false,
        resetView: true,
      });
      return;
    }

    // Linked discovered chip that also has server_id
    if (sid && /^\d+$/.test(sid)) {
      loadAndDraw(root, {
        serverId: sid,
        force: false,
        resetView: true,
      });
      return;
    }

    clearLayer(svgRoot(root));
  }

  /**
   * Show ports for a compose project / container (service or stack-node click).
   */
  function showForService(root, opts) {
    opts = opts || {};
    if (!opts.serverId && !opts.deviceId) return;
    loadAndDraw(root, {
      serverId: opts.serverId,
      deviceId: opts.deviceId,
      focusProject: opts.project || opts.focusProject || '',
      focusContainer: opts.container || opts.focusContainer || '',
      force: !!opts.force,
      resetView: opts.resetView !== false,
    });
  }

  function clear(root) {
    var svg =
      (root && svgRoot(root)) ||
      document.querySelector('svg[data-fabric-mesh="physical"]');
    clearLayer(svg);
  }

  window.PiHerderHostPortsExpand = {
    show: function (serverId, force) {
      var root =
        document.querySelector('[data-fabric-root].is-focusing') ||
        document.querySelector('[data-fabric-root]');
      if (!root) return;
      loadAndDraw(root, { serverId: serverId, force: !!force });
    },
    showForFocus: showForFocus,
    showForService: showForService,
    clear: clear,
    invalidate: function (serverId) {
      if (serverId) {
        var prefix = 'h:' + String(serverId);
        Object.keys(cache).forEach(function (k) {
          if (k === prefix || k.indexOf(prefix + ':') === 0) delete cache[k];
        });
        Object.keys(viewMode).forEach(function (k) {
          if (k === prefix || k.indexOf(prefix + ':') === 0) delete viewMode[k];
        });
      } else {
        cache = {};
        viewMode = {};
      }
    },
  };
})();
