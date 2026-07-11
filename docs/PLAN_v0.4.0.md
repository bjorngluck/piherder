# PiHerder v0.4.0 — release plan (draft)

**Status:** Planning / discussion  
**Date:** 2026-07-11  
**Baseline:** `v0.3.0` (Grafana + Kuma hub)  
**Related:** [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) · [SPEC.md](../SPEC.md) Phase 5 remainder + Phase 6 · [RELEASE_v0.3.0.md](RELEASE_v0.3.0.md)

This document lines up **must-ship fixes**, **optional H1 carry-over**, and **H2 feature choices** for the next minor. Nothing here is locked until we agree ship bar and sequencing.

---

## 1. Already on `main` after v0.3.0 (must be in next tag)

These are **bugs / UX gaps** found right after Grafana ship. They live on `main` as of:

| Commit | Summary |
|--------|---------|
| `ba56c2b` | **Docker Deploy silent success** — pull/up results ignored; audit always `success`; no banner; paths unquoted; little evidence a pull ran |
| `d33f286` | **Container update alert stuck** — successful Deploy cleared badges but left open `container_updates` notification |

### Expected operator behaviour (now)

| Action | Effect |
|--------|--------|
| **Check updates** | `docker compose pull` only; badge/alert if new layers; **does not** recreate containers |
| **Deploy** | `pull` (optional) + `up -d`; audit with pull_rc/up_rc + output; banner; clears stack from pending; **resolves alert** when no stacks remain |

### Ship options for these fixes

| Option | Pros | Cons |
|--------|------|------|
| **A. Tag `v0.3.1` soon** (hotfix) | Operators on 0.3.0 get Deploy/alert fixes without waiting for templates | Extra tag/docs |
| **B. Only in `v0.4.0`** | One next release | Anyone tracking 0.3.0 misses critical Docker honesty until 0.4 |
| **C. Both** — note in 0.3.1 + changelog blurb in 0.4.0 | Clean | Slight duplication |

**Recommendation:** **A or C** — Deploy without feedback is ops-facing; don’t wait for full H2. Still **list them in 0.4.0 release notes** as included.

### Follow-ups (nice-to-have, not blocking)

- [ ] Optional: run Docker Deploy / Check updates as **Jobs** with live log (JobHold), like backup/patch  
- [ ] Optional: `docker compose up -d --pull always` single-command path for Compose v2.22+  
- [ ] Service logos in herder self-backup (paths restore; files do not)  
- [ ] Push notification when container-update alert **resolves** (today resolve is silent)

---

## 2. Roadmap theme for v0.4 (Horizon 2)

From [ROADMAP_ECOSYSTEM.md](ROADMAP_ECOSYSTEM.md) § Horizon 2 and SPEC Phase 6:

> Versioned **templates**: compose/install recipe + variables + post-deploy checklist/actions.  
> Onboard wizard steps: monitoring / DNS / TLS-proxy / feature flags — always **preview → confirm → audit**.

That is a large surface. v0.4.0 should pick a **thin vertical slice**, not the entire curated pack + every provider.

---

## 3. Feature candidates (discussion)

### A. Remaining H1 — multi-URL / generic adapters (Phase 5 remainder)

| Item | Value | Effort | Notes |
|------|-------|--------|-------|
| Generic **URL bookmark** integration (name + base URL + optional logo) | Medium | Low–med | Pi-hole, NPM, HA, Frigate, n8n without full APIs |
| Multi Pi-hole (status/deep link only) | Medium | Med | Seed from `PIHOLE_URL` if present |
| Docs: cert path NPM → n8n → consumers | Low | Low | ADMIN only |
| Kuma **create monitor** | High later | High | Spec says H2 / provider actions |

**Fit for 0.4:** Optional **sidecar** if templates need “open in Pi-hole” links; not required to start templates.

### B. Service templates — core (Phase 6)

| Slice | Description | Ship bar idea |
|-------|-------------|----------------|
| **B0 Schema + store** | Template model (JSON/YAML): id, name, variables, file set (compose, `.env` sample), checklist steps | DB or `templates/*.yaml` in repo; import/export |
| **B1 Apply to existing host** | “New Docker project from template” on a server: fill vars → preview file writes → confirm → write + optional deploy | One generic + 1–2 curated |
| **B2 Add-server wizard hooks** | After SSH onboard: offer template / feature flags | Depends on B1 |
| **B3 Post-deploy actions** | Checklist only (manual) vs call Kuma create-monitor / NPM | Checklist-only first is safer |
| **B4 Curated pack** | Pi-hole, Kuma, Grafana, Frigate, HA, NPM, n8n, media, generic web | Full pack can spill past 0.4.0 |

**Recommendation for 0.4.0 ship bar:** **B0 + B1** with **generic web** + **one real stack** (e.g. Uptime Kuma or Pi-hole) + checklist steps (no auto Kuma create yet).

### C. Docker ops polish (adjacent to recent bugs)

| Item | Notes |
|------|--------|
| Deploy/Check as Jobs | Long pulls no longer block web request; live logs |
| Per-service pull/up | Narrower than whole project |
| Better “Update available” after Deploy | Done for stack badge + alert; verify inventory refresh |

### D. Explicitly out of 0.4.0 (unless you insist)

- Full curated pack (all 9 templates production-ready)  
- Cloudflare / NPM automation  
- HA custom component, AI, Ansible inventory (Phase 7)  
- Multi-arch Hub publish (process exists; credentials)  
- k8s / bare install

---

## 4. Proposed v0.4.0 ship bar (strawman)

**Theme:** *Templates foundation + honest Docker apply lifecycle*

1. **Quality** — include post-0.3 Deploy + alert fixes (and prefer **v0.3.1** first if not tagged yet)  
2. **Templates v1** — schema, UI list, apply-to-server (preview → confirm → audit), import/export, 2 sample templates  
3. **Docs** — ADMIN section for templates; release notes  
4. **Tests** — schema validation + apply dry-run unit tests  

**Stretch (if time):**

5. Generic URL integration type (bookmarks)  
6. Deploy as Celery job + live log  

**Not in freeze:** full pack, provider auto-create monitors, multi-Pi-hole deep API.

---

## 5. Sequencing (if we agree on strawman)

```text
0. Tag v0.3.1 (Deploy honesty + alert resolve)   [optional but recommended]
1. Template schema design (short design note or FEATURE_PLAN_TEMPLATES.md)
2. Storage + API/registry + tests
3. UI: list / new from template / variable form / preview
4. Write files + optional redeploy (reuse docker_versions / redeploy_project)
5. Checklist post-steps (links, manual)
6. Sample templates (generic + one stack)
7. Docs + RELEASE_v0.4.0.md + tag
```

---

## 6. Discussion prompts

Please react to these so we can lock the plan:

1. **Hotfix:** Tag **v0.3.1** now for Docker Deploy/alert, or only roll into 0.4.0?  
2. **Primary 0.4 theme:** Templates (H2) vs finish multi-URL H1 first vs Docker-as-Jobs polish?  
3. **Template ship bar:** B0+B1 only, or also B2 (add-server wizard)?  
4. **First curated templates:** which two? (e.g. *generic web* + *Uptime Kuma* / *Pi-hole* / *Grafana*)  
5. **Post-deploy:** checklist-only for 0.4, or invest in Kuma create-monitor?  
6. **Anything from your fleet pain** that should beat templates (backup, IAM, metrics, …)?

---

## 7. Changelog stubs (for future RELEASE notes)

### v0.3.1 (if tagged)

- fix(docker): Deploy surfaces pull/up exit codes, audit output, and result banner  
- fix(docker): successful Deploy clears pending stack and resolves container update alert  

### v0.4.0 (when feature work lands)

- feat: service templates (schema, apply, samples) — *TBD*  
- Includes Docker Deploy/alert fixes if not shipped as 0.3.1  
- *…*

---

**End of draft plan** — edit this file as decisions land.
