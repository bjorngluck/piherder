# PiHerder Stabilisation Decision Plan

**Date:** 2026-07-09  
**Status:** Stabilisation complete (Phases 1–3)  
**Goal:** Stabilise recent major feature additions (RBAC, scheduling, jobs, restore wizard, live progress) and improve overall quality before adding more functionality.

**Phase tracking:**
1. **Short Stabilisation Sprint** — done (tests + jobs helpers + menu single-source harden)
2. **Documentation Sprint** — done → [ADMIN.md](ADMIN.md) (RBAC, users, security policy, schedules, Jobs)
3. **Polish Pass** — done (shared empty-state pattern, header consistency, clearer no-results CTAs)
4. Resume feature development

---

## Focus Areas (ranked by priority)

### 1. Testing & Reliability (Highest Priority)

**Decision needed:** How much testing do we add now vs later?

**Recommended approach:**
- Expand pytest coverage with priority on:
  - RBAC enforcement (viewer/operator/admin restrictions)
  - Patch scheduling (edge cases, conflicts, skipped jobs)
  - Backup restore wizard (dry-run + apply paths)
  - Job progress / live log flows
- Add a small number of integration-style smoke tests for critical user journeys.
- Target: At least one meaningful test per major new feature area.

**Action:** Create a focused test expansion plan + start implementing the highest-risk areas first.

---

### 2. Job / Progress / Scheduling Layer

**Decision needed:** Should we refactor this layer now or keep extending it?

**Recommended approach:**
- Do a **small, targeted refactor** of the job/progress handling before adding more job types.
- Goal: Improve clarity, reduce duplication, and make future job types easier to add.
- Keep it lightweight — do not over-engineer.

**Action:** Propose a minimal refactor scope for the job system.

---

### 3. Mobile Menu & UI Stability

**Decision needed:** How do we prevent future menu breakage when making changes?

**Recommended approach:**
- Harden the menu rendering logic in `base.html` (single source of truth for nav items).
- Add better guards / simpler structure for secondary actions (especially "Toggle theme").
- Consider moving more menu logic into reusable includes/macros if it reduces fragility.

**Action:** Review current mobile + desktop menu code and propose a more robust version.

---

### 4. Documentation for Admin Features

**Status:** Done — see [ADMIN.md](ADMIN.md). Linked from README + SPEC.

**Delivered:**
- RBAC roles and enforcement matrix
- User administration (create, password policy, sole admin, invite)
- Force 2FA security policy
- Update-check vs patch-apply schedules + skip rules
- Jobs page, progress, vs audit/notifications

---

### 5. UI Polish & Consistency Pass

**Status:** Done (lightweight).

**Delivered:**
- Shared `.empty-state` styles in `themes.css`
- Clearer empty / no-filter-match cards on Jobs, Audit, Notifications, Servers
- Servers header spacing aligned (`mb-6`); Users header uses standard `page-header-actions`
- Server detail Jobs panel + Settings archive empty hints

---

### 6. Overall Approach & Sequencing

**Recommended sequence:**

1. **Short Stabilisation Sprint** (1–2 weeks)
   - Testing expansion (RBAC + scheduling first)
   - Job/progress layer light refactor
   - Mobile menu hardening

2. **Documentation Sprint**
   - Core admin features documented

3. **Polish Pass**
   - Quick wins on consistency and UX

4. **Then resume feature development** (with higher confidence)

---

**End of Decision Plan**