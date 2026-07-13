# From host

Turn an existing compose project into an operator-owned template.

## Steps

1. **Catalog → Templates → From host…**  
2. Pick a Docker-enabled **server** + **project**.  
3. Optional: move secret-like values to `.env` placeholders.  
4. Pull parameterizes **volumes**, **host ports**, **booleans**, and env/secrets into deploy variables; rewrites short mounts/ports to `{{VAR}}`.  
5. Review in the editor → **Save**.  
6. Progress overlay / wait modal while SSH pull runs.

## Tips

- Multi-file and odd layouts: polish continues in v0.5.0 — if pull fails, see [Templates troubleshooting](../troubleshooting/templates-docker.md).  
- After save, you can [deploy](deploy.md) to the same or another host.  
