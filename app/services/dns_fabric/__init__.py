"""DNS / Network maps package.

Public API remains ``from app.services import dns_fabric`` / ``dns_fabric.X``.
Implementation is split for maintainability:
  core            — records, access paths, pi-hole, fabric views
  mesh_physical   — Hosts map SVG
  mesh_logical    — Path map SVG
"""
from __future__ import annotations

from .core import *  # noqa: F403
from . import core as _core

# Explicit re-exports used by tests / routers (static analyzers)
from .core import (  # noqa: F401
    DnsFabricError,
    normalize_fqdn,
    is_valid_fqdn,
    is_valid_ipv4,
    host_ip_for_dns,
    suggest_host_dns_name,
    match_pihole_host_for_server,
    find_npm_host_server,
    resolve_service_dns_plan,
    list_pihole_cnames,
    plan_from_pihole_cname,
    list_service_dns_candidates,
    import_pihole_cnames,
    attach_service_dns_from_plan,
    host_dns_form_defaults,
    fanout_pihole_dns,
    update_server_dns,
    sync_host_a,
    remove_host_a,
    get_service_record,
    list_service_records,
    find_service_for_deployment,
    upsert_service_record,
    sync_service_dns,
    sync_service_cname,
    delete_service_record,
    certs_matching_fqdn,
    resolve_app_layers,
    is_host_identity_name,
    build_access_path,
    build_access_path_for_record,
    host_focus_key,
    hosts_map_url,
    path_map_url,
    fabric_rack_for_server,
    fabric_paths_for_docker,
    fabric_index_for_server,
    fabric_path_for_fqdn,
    build_fabric_view,
    list_kuma_monitor_options,
    servers_with_dns_name,
    cleanup_dns_for_server,
)

__all__ = [n for n in dir(_core) if not n.startswith("__")]

# Private helpers still used by unit tests (and safe for internal tooling)
from .core import (  # noqa: F401,E402
    _server_name_tokens,
    _plan_summary,
    _is_already_present_error,
    _summarize_results,
    _ip_in_lan,
    _host_is_cloud,
    _build_physical_view,
    _build_logical_view,
    _npm_proxy_hosts_cached,
    _match_pihole_cname,
    _server_by_dns_name,
    _server_by_ip,
    _servers_by_id,
    _find_docker_container,
    _find_npm_forward,
    _fqdn_match_tokens,
    _with_map_anchor,
    _service_app_chip,
    _resolve_network_kuma_monitor,
)
from .mesh_physical import (  # noqa: F401,E402
    PHYSICAL_MESH_MAX_APPS_PER_HOST,
    _build_physical_mesh_svg,
)
from .mesh_logical import (  # noqa: F401,E402
    _build_logical_mesh_svg,
    _build_path_mesh,
)

# Alias path mesh from logical module onto package for tests that call fabric._build_path_mesh
