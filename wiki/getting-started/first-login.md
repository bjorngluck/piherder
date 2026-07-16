# First login

## Register the first admin

1. Open the app URL from [Install](install.md).  
2. Complete **Register** with a strong email + password.  
3. You are the first user → role **admin**.

!!! warning "Open registration locks after the first account"
    After the first admin exists, the login screen no longer offers self-registration.
    New people **ask an admin** for an invite (Users → Create user). Direct `/auth/register`
    explains how to request access instead of a hard config error.

### Password policy

Enforced on register, password change, and admin-created users:

- At least **10** characters  
- At least one **uppercase**, one **lowercase**, one **digit**  
- Keep under **72 characters** (storage limit; emoji/symbols count more)

## After login checklist

| Step | Where |
|------|--------|
| Set display name / avatar | **Account** (full-width ops-hero + profile / security cards) |
| Optional 2FA | **Account** → TOTP — or [force 2FA for all](../account-security/two-factor.md) |
| Push notifications | **Account** → Push (after [HTTPS / PWA](../account-security/pwa-push.md)) |
| Timezone | **Settings → General** |
| Create operators/viewers | **Users** (admin) — after first admin, no public self-register |
| Add first server | [Add a server](../day-to-day/add-server.md) |

## Admin quick checklist

1. Create operators/viewers via **Users → Create user** (modal + one-time credentials); share invite passwords carefully.  
2. Optionally enable **Force 2FA** under Settings → Security policy.  
3. Per server: **Edit → Features** → then **Schedules** for checks → only then consider **apply** schedules. Remove a host later via **Edit → Remove**.  
4. Prefer “only if updates” on apply schedules; start with a quiet weekly window.  
5. For mobile push: trusted TLS + [PWA & Web Push](../account-security/pwa-push.md); open in-app alerts from the **bell**.  
6. DR: Settings → PiHerder backup; keep `PIHERDER_MASTER_KEY` offline safe.

<figure class="ph-figure" markdown>
  ![Dashboard](../assets/screenshots/dashboard.svg)
  <figcaption>After login you land near the fleet dashboard. <span class="ph-wireframe-badge">wireframe</span></figcaption>
</figure>
