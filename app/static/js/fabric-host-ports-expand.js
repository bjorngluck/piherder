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

  function draw(svg, data) {
    clearLayer(svg);
    if (!data || !data.ok) return;
    var ports = data.ports || [];
    var stacks = data.stacks || [];
    if (!ports.length) {
      var hostEmpty = findHostNode(svg, data.server_id, data.node_id);
      if (!hostEmpty) return;
      var ge = hostGeom(hostEmpty);
      var gEmpty = el('g', {
        id: layerId,
        class: 'fabric-host-ports-expand-layer',
        'data-server-id': String(data.server_id),
      });
      var te = el('text', {
        x: ge.right + 16,
        y: ge.y + 4,
        class: 'fabric-host-ports-empty',
        fill: 'var(--color-muted)',
        'font-size': '11',
      });
      te.textContent = 'No published / observed ports — refresh Docker inventory';
      gEmpty.appendChild(te);
      svg.appendChild(gEmpty);
      return;
    }

    var hostNode = findHostNode(svg, data.server_id, data.node_id);
    if (!hostNode) return;
    var a = hostGeom(hostNode);

    var portW = 92;
    var portH = 28;
    var stackW = 100;
    var stackH = 36;
    var rowGap = 8;
    var colGap = 48;
    var portX = a.right + 56;
    var stackX = portX + portW + colGap;

    var nPorts = ports.length;
    var totalH = nPorts * portH + Math.max(0, nPorts - 1) * rowGap;
    var startY = a.y - totalH / 2;

    var g = el('g', {
      id: layerId,
      class: 'fabric-host-ports-expand-layer',
      'data-server-id': String(data.server_id),
    });

    // Zone backdrop
    var zonePad = 14;
    var zoneW =
      stackX +
      stackW / 2 -
      (a.right + 8) +
      zonePad +
      (stacks.length ? 0 : -(colGap + stackW / 2));
    var zoneH = Math.max(totalH, stacks.length * (stackH + rowGap)) + zonePad * 2;
    var zoneTop = Math.min(startY, a.y - zoneH / 2) - zonePad;
    g.appendChild(
      el('rect', {
        x: a.right + 10,
        y: zoneTop,
        width: Math.max(zoneW, portW + 40),
        height: zoneH,
        rx: 12,
        class: 'fabric-host-ports-zone',
      })
    );
    var title = el('text', {
      x: a.right + 22,
      y: zoneTop + 14,
      class: 'fabric-host-ports-title',
      fill: 'var(--color-muted)',
      'font-size': '9',
      'font-weight': '700',
    });
    title.textContent =
      'PORTS → OWNERS · ' +
      String(data.total_count || ports.length) +
      (data.stack_count ? ' · ' + data.stack_count + ' stacks' : '');
    g.appendChild(title);

    // Lead from host
    g.appendChild(
      el('line', {
        x1: a.right + 2,
        y1: a.y,
        x2: portX - portW / 2 - 8,
        y2: a.y,
        class: 'fabric-mesh-edge fabric-host-ports-lead',
        stroke: 'var(--color-accent, #00a651)',
        'stroke-width': '2.25',
        'stroke-dasharray': '5 3',
        fill: 'none',
      })
    );

    var portPos = {};
    ports.forEach(function (p, i) {
      var cy = startY + i * (portH + rowGap) + portH / 2;
      var pid = p.id || p.host_port + '/' + p.proto;
      portPos[pid] = { x: portX, y: cy };
      var rs = roleStyle(p.role);

      // host → port edge
      g.appendChild(
        el('line', {
          x1: a.right + 4,
          y1: a.y,
          x2: portX - portW / 2,
          y2: cy,
          class: 'fabric-mesh-edge fabric-host-ports-edge',
          stroke: rs.stroke,
          'stroke-width': '1.5',
          'stroke-opacity': '0.75',
          fill: 'none',
        })
      );

      var ng = el('g', {
        class:
          'fabric-host-port-node fabric-host-port-node--' +
          (p.role || 'other') +
          (p.role_sticky ? ' is-sticky' : ''),
        'data-port-id': pid,
        'data-server-id': String(data.server_id),
        style: 'cursor:pointer',
      });
      var tip = el('title');
      tip.textContent =
        (p.display || p.label) +
        ' · ' +
        (p.source || '') +
        (p.owner_project ? ' → ' + p.owner_project : ' (unowned)') +
        ' · click for host ports';
      ng.appendChild(tip);
      ng.appendChild(
        el('rect', {
          x: portX - portW / 2,
          y: cy - portH / 2,
          width: portW,
          height: portH,
          rx: 8,
          fill: rs.fill,
          stroke: rs.stroke,
          'stroke-width': p.role_sticky ? '2.25' : '1.6',
        })
      );
      var roleT = el('text', {
        x: portX - portW / 2 + 8,
        y: cy + 1,
        'text-anchor': 'start',
        'dominant-baseline': 'middle',
        'font-size': '8',
        'font-weight': '800',
        fill: rs.stroke,
      });
      roleT.textContent = String(p.role_label || p.role || '?')
        .slice(0, 5)
        .toUpperCase();
      ng.appendChild(roleT);
      var portT = el('text', {
        x: portX + 10,
        y: cy + 1,
        'text-anchor': 'middle',
        'dominant-baseline': 'middle',
        'font-size': '11',
        'font-weight': '700',
        fill: 'var(--color-text)',
        'font-family': 'ui-monospace, Menlo, monospace',
      });
      portT.textContent = String(p.host_port);
      ng.appendChild(portT);
      // source mark
      if (p.source === 'nmap') {
        var obs = el('text', {
          x: portX + portW / 2 - 6,
          y: cy - portH / 2 + 9,
          'text-anchor': 'end',
          'font-size': '7',
          fill: 'var(--color-muted)',
        });
        obs.textContent = 'obs';
        ng.appendChild(obs);
      }
      ng.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var url =
          data.panel_url ||
          '/dns/host-ports-panel?server_id=' + encodeURIComponent(String(data.server_id));
        if (window.PiHerderStackPanel && window.PiHerderStackPanel.open) {
          window.PiHerderStackPanel.open(url);
        }
      });
      g.appendChild(ng);
    });

    // Stack owners column
    var stackPos = {};
    if (stacks.length) {
      var sTotal = stacks.length * stackH + Math.max(0, stacks.length - 1) * rowGap;
      var sStart = a.y - sTotal / 2;
      stacks.forEach(function (s, i) {
        var cy = sStart + i * (stackH + rowGap) + stackH / 2;
        var sid = 'stack:' + s.name;
        stackPos[sid] = { x: stackX, y: cy };
        var sg = el('g', {
          class: 'fabric-host-stack-node',
          'data-stack': s.name,
          style: 'cursor:pointer',
        });
        var st = el('title');
        st.textContent =
          s.name + ' · ' + (s.port_count || 0) + ' port(s) · click host ports';
        sg.appendChild(st);
        sg.appendChild(
          el('rect', {
            x: stackX - stackW / 2,
            y: cy - stackH / 2,
            width: stackW,
            height: stackH,
            rx: 9,
            class: 'fabric-host-stack-box',
            fill: 'color-mix(in srgb, var(--color-accent, #00a651) 10%, var(--color-surface))',
            stroke: 'var(--color-accent, #00a651)',
            'stroke-width': '1.75',
          })
        );
        var sn = el('text', {
          x: stackX,
          y: cy - 2,
          'text-anchor': 'middle',
          'font-size': '10',
          'font-weight': '700',
          fill: 'var(--color-text)',
        });
        sn.textContent = String(s.name).slice(0, 12);
        sg.appendChild(sn);
        var sc = el('text', {
          x: stackX,
          y: cy + 11,
          'text-anchor': 'middle',
          'font-size': '8',
          fill: 'var(--color-muted)',
        });
        sc.textContent = (s.port_count || 0) + ' ports';
        sg.appendChild(sc);
        sg.addEventListener('click', function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          var url =
            (data.panel_url ||
              '/dns/host-ports-panel?server_id=' +
                encodeURIComponent(String(data.server_id))) +
            '&focus_project=' +
            encodeURIComponent(s.name);
          if (window.PiHerderStackPanel && window.PiHerderStackPanel.open) {
            window.PiHerderStackPanel.open(url);
          }
        });
        g.appendChild(sg);

        // port → stack edges
        (s.port_ids || []).forEach(function (pid) {
          var pp = portPos[pid];
          if (!pp) return;
          g.appendChild(
            el('line', {
              x1: pp.x + portW / 2,
              y1: pp.y,
              x2: stackX - stackW / 2,
              y2: cy,
              class: 'fabric-mesh-edge fabric-host-ports-owner-edge',
              stroke: 'var(--color-accent, #00a651)',
              'stroke-width': '1.75',
              'stroke-opacity': '0.85',
              fill: 'none',
              'marker-end': '',
            })
          );
        });
      });
    }

    // Unowned ports: label on the right of port
    ports.forEach(function (p) {
      if (p.owner_project) return;
      var pid = p.id || p.host_port + '/' + p.proto;
      var pp = portPos[pid];
      if (!pp) return;
      var u = el('text', {
        x: pp.x + portW / 2 + 8,
        y: pp.y + 3,
        'font-size': '8',
        fill: 'var(--color-muted)',
      });
      u.textContent = p.source === 'nmap' ? 'observed only' : 'unowned';
      g.appendChild(u);
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
