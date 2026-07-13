# Grafana

Read-mostly deep links into an existing Grafana. PiHerder does **not** have to deploy Grafana (you may still use the Grafana template for a new instance).

<figure class="ph-figure" markdown>
  ![Grafana integration](../assets/screenshots/integrations-grafana.svg)
  <figcaption>Kinds + tabbed bindings. <span class="ph-wireframe-badge">wireframe</span></figcaption>
</figure>

## Connect

1. Grafana → **Administration → Service accounts** — Viewer token (`glsa_…`) recommended.  
2. Probe:

   ```bash
   curl -sS -H "Authorization: Bearer $GRAFANA_TOKEN" "https://grafana.example.com/api/health"
   curl -sS -H "Authorization: Bearer $GRAFANA_TOKEN" \
     "https://grafana.example.com/api/search?type=dash-db" | head
   ```

3. PiHerder → **Catalog → Integrations → + Grafana** — base URL, optional token, **query templates** (`var-` prefix).  
4. **Poll / Test** stores health + inventory (with token).  
5. **Bind** with a **kind**:

| Kind | Surfaces |
|------|----------|
| Host metrics / Host logs | Server detail Grafana card |
| Containers (host) | Server detail Grafana card |
| Containers + container name | Docker page chip / ⋯ / expand |

### Display name (rename in PiHerder)

When binding or editing a dashboard:

- Optional **Display name in PiHerder** — shown on server detail / Docker chips.  
- Survives **Poll** (Grafana’s own title is kept as reference).  
- Leave blank (or clear and save) to follow the Grafana dashboard title again.  
- You can still rename the dashboard in Grafana; without an override, PiHerder picks up the new title on the next poll.

### Placeholders

`{hostname}`, `{hostname_short}`, `{container}`, `{project}`, `{ip}`, …  
Grafana variables need the **`var-`** prefix (`var-job=…`).

### Docker UX

- **Grafana** chip (tap opens filtered dashboard)  
- Container **⋯** menu  
- Expanded row links (mobile-friendly)

Without a token you can still deep-link by pasting dashboard UIDs; inventory list will be empty.
