# First login

## Register the first admin

1. Open the app URL from [Install](install.md).  
2. Complete **Register** with a strong email + password.  
3. You are the first user → role **admin**.

!!! warning "Open registration locks after the first account"
    Further users are created only by an admin under **Users** — see [Users](../account-security/users.md).

### Password policy

Enforced on register, password change, and admin-created users:

- At least **10** characters  
- At least one **uppercase**, one **lowercase**, one **digit**  
- Max **72** bytes (bcrypt)

## After login checklist

| Step | Where |
|------|--------|
| Set display name / avatar | **Account** |
| Optional 2FA | **Account** → TOTP — or [force 2FA for all](../account-security/two-factor.md) |
| Timezone | **Settings → General** |
| Create operators/viewers | **Users** (admin) |
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
