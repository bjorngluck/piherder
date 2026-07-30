/**
 * Host ports on the Hosts map — lock a fleet host to expand:
 *   Host ──→ port chips (role) ──→ stack owners
 * This is the primary depth UX (not a hidden drawer).
 */
(function () {
  'use strict';

  var layerId = 'fabric-host-ports-expand-layer';
  var NS = 'http://www.w3.org/2000/svg';
  var cache = {};
  var pending = null;

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

  function findHostNode(svg, serverId, nodeId) {
    if (!svg) return null;
    var sid = String(serverId || '');
    var nid = nodeId || (sid ? 'host-' + sid : '');
    if (nid) {
      var byId = svg.querySelector('[data-node-id="' + nid.replace(/"/g, '') + '"]');
      if (byId) return byId;
    }
    if (sid) {
      var nodes = svg.querySelectorAll('.fabric-mesh-node--host[data-server-id]');
      for (var i = 0; i < nodes.length; i++) {
        if (String(nodes[i].getAttribute('data-server-id')) === sid) return nodes[i];
      }
    }
    return null;
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

  function draw(svg, data) {
    clearLayer(svg);
    if (!data || !data.ok) return;

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

    var hostNode = findHostNode(svg, data.server_id, data.node_id);
    if (!hostNode) return;
    var a = hostGeom(hostNode);

    if (!services.length) {
      var gEmpty = el('g', {
        id: layerId,
        class: 'fabric-host-ports-expand-layer',
        'data-server-id': String(data.server_id),
      });
      var te = el('text', {
        x: a.right + 24,
        y: a.y + 4,
        class: 'fabric-host-ports-empty',
        fill: 'var(--color-muted)',
        'font-size': '11',
      });
      te.textContent = 'No published / observed ports — refresh Docker inventory';
      gEmpty.appendChild(te);
      svg.appendChild(gEmpty);
      return;
    }

    // Card fully to the RIGHT of host (was overlapping: colX - cardW/2 < a.right)
    var cardW = 168;
    var gapFromHost = 88; // clear air between host edge and card left
    var cardLeft = a.right + gapFromHost;
    var colX = cardLeft + cardW / 2;
    var rowGap = 14;

    var metrics = services.map(serviceCardMetrics);
    var totalH = metrics.reduce(function (s, m) {
      return s + m.h;
    }, 0) + Math.max(0, services.length - 1) * rowGap;
    var startY = a.y - totalH / 2;

    var g = el('g', {
      id: layerId,
      class: 'fabric-host-ports-expand-layer',
      'data-server-id': String(data.server_id),
    });

    // Zone wraps cards only (not under host)
    var zonePadX = 14;
    var zonePadY = 18;
    var zoneTop = startY - zonePadY - 8;
    var zoneH = totalH + zonePadY * 2 + 14;
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

    // Lead host → column
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

      // One group: outer card + all content clipped to card coords
      var sg = el('g', {
        class:
          'fabric-host-service-node' + (isObs ? ' is-observed' : ' is-service'),
        'data-service-id': svc.id || '',
        'data-project': svc.project || '',
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

      // Outer service card
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

      // Project / service title
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
        od.textContent = 'nmap · no Docker owner';
        sg.appendChild(od);
        y += 12;
      }

      y += 4; // gap before port chips inside card

      // Port chips as mini-rects INSIDE the service card
      var ports = svc.ports || [];
      var chipH = 15;
      var chipGap = 3;
      var chipMaxW = cardW - padX * 2;
      ports.forEach(function (p) {
        var rs = roleStyle(p.role);
        var chipY = y - 10;
        // background chip fully inside card
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
        var url =
          data.panel_url ||
          '/dns/host-ports-panel?server_id=' +
            encodeURIComponent(String(data.server_id));
        if (svc.project) {
          url += '&focus_project=' + encodeURIComponent(svc.project);
        }
        if (window.PiHerderStackPanel && window.PiHerderStackPanel.open) {
          window.PiHerderStackPanel.open(url);
        }
      });
      g.appendChild(sg);
      yCursor += h + rowGap;
    });

    svg.appendChild(g);
  }

  function loadAndDraw(root, serverId, opts) {
    opts = opts || {};
    var svg = svgRoot(root);
    if (!svg || !serverId) return;
    var sid = String(serverId);
    var force = !!opts.force;
    var ck = 'h:' + sid;

    // Clear stack expand so layers don't fight
    try {
      if (window.PiHerderStackExpand && window.PiHerderStackExpand.clear) {
        window.PiHerderStackExpand.clear();
      }
    } catch (e) {}

    function apply(data) {
      if (!root || !root.classList.contains('is-focusing')) {
        if (!force) return;
      }
      // Only if still focused on this host
      var fid = root._fabricFocusId != null ? String(root._fabricFocusId) : '';
      var still =
        force ||
        fid === 'n:host-' + sid ||
        (fid.indexOf('n:host-') === 0 && fid.slice(7) === sid);
      if (!still && fid) {
        // allow if node focus matches server via DOM
        var n = root.querySelector('[data-node-id="' + (fid.indexOf('n:') === 0 ? fid.slice(2) : fid) + '"]');
        if (n && String(n.getAttribute('data-server-id') || '') === sid) still = true;
      }
      if (!still && !force) return;
      draw(svg, data);
    }

    if (!force && cache[ck]) {
      apply(cache[ck]);
      return;
    }

    var token = String(Date.now());
    pending = token;
    var url =
      '/dns/host-ports-expand.json?server_id=' +
      encodeURIComponent(sid) +
      '&_=' +
      token;
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
    if (node.getAttribute('data-discovered') === '1') {
      clearLayer(svgRoot(root));
      return;
    }
    var sid = (node.getAttribute('data-server-id') || '').trim();
    if (!sid || !/^\d+$/.test(sid)) {
      clearLayer(svgRoot(root));
      return;
    }
    loadAndDraw(root, sid, { force: false });
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
      loadAndDraw(root, serverId, { force: !!force });
    },
    showForFocus: showForFocus,
    clear: clear,
    invalidate: function (serverId) {
      if (serverId) delete cache['h:' + String(serverId)];
      else cache = {};
    },
  };
})();
