<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/azureagentforge-logo-dark.png">
    <img alt="AzureAgentForge" src="assets/azureagentforge-logo-light.png" width="440">
  </picture>
</p>

# Cost estimates

> ⚠️ **Estimates, not a bill.** The figures below are modeled from the Azure Pricing Calculator (East US) for the cost-optimized profile. Verify against your own Azure invoice before quoting them. LLM token usage is billed separately and is NOT included here.

Cost depends on your region, your activity level, and which profile you deploy. The numbers below model the cost-optimized default (`infrastructure/profiles/cost-optimized.tfvars`) for East US. Your bill will differ.

## Cost-optimized profile

| Service | Config | Est. $/mo (verify) |
|---|---|---|
| PostgreSQL Flexible Server | Burstable B1ms, 32 GB | ~$16 |
| Container Apps (Consumption) | ~2 always-on small apps + scale-to-zero workers | ~$40–70 |
| Log Analytics | 30-day retention, 1 GB/day cap | ~$5–15 |
| Container Registry | Basic | ~$5 |
| Key Vault | Standard, light op volume | ~$1 |
| Networking | VNet (free) + modest egress | ~$2–5 |
| **Infra total (excl. LLM tokens)** | | **~$70–115** |

That lands under the <$150 target with headroom. The Container Apps line is the biggest variable: it swings with whether your workers are idle or running jobs.

## What drives cost

**Container Apps** bills on vCPU-seconds and memory-seconds consumed. Services that aren't handling requests should scale to zero. The cost-optimized profile relies on Container Apps' consumption plan, which means you pay nothing for a worker that isn't running — but an always-on replica costs something every hour.

**Log Analytics** is a quiet cost trap. The profile sets `log_daily_quota_gb = 1` as a hard cap, and `log_retention_in_days = 30`. Both levers matter: ingestion is billed per GB, and retention extends storage costs. If you increase either without watching, the $5–15 estimate climbs fast.

**Container Registry** Basic tier covers most dev and small-scale production use. Upgrade only if you need geo-replication or higher throughput.

**Key Vault** costs are mostly negligible at typical operation volumes. The estimate assumes light usage (hundreds of secret reads per day, not millions).

## How to cut it further

- **Scale-to-zero non-interactive services.** Any worker that runs on-demand rather than continuously should have `min_replicas = 0` in its Container App definition.
- **Right-size CPU and memory.** Start with 0.25 vCPU / 0.5 GB per replica, measure, and only increase what's actually saturated.
- **Cap Log Analytics** retention and daily quota. The profile already does this; don't undo it without a cost review.
- **Leave `cloudflared_enabled = false`.** The cost-optimized profile skips the Cloudflared sidecar and uses Azure Container Apps managed ingress instead. Adding Cloudflared adds a running container; avoid it unless the tunnel is specifically required.

## Hardened profile

The hardened profile (`infrastructure/profiles/hardened.tfvars`) costs roughly $250+/mo. The main differences:

- PostgreSQL upgrades to B2s with zone-redundant HA (`postgres_high_availability_enabled = true`), which roughly doubles the database compute cost.
- Log retention extends to 90 days with no daily quota cap (`log_daily_quota_gb = -1`). Monitor ingestion volume actively or set an alert.
- Key Vault uses a private endpoint (`key_vault_public_network_access_enabled = false`), adding a small but fixed private DNS + endpoint charge.
- Cloudflared is enabled, adding a permanently running container.

If you're deploying to production and need the security posture, the cost difference is worth it. If you're evaluating the project or running a dev environment, start with cost-optimized.

## Measure your real cost

Use **Azure Cost Management** (portal > Cost Management + Billing > Cost analysis) to see actual spend by resource. Tag your resource group (the Terraform modules use a consistent `project` tag) so you can filter to just this deployment.

Set a cost alert on the resource group at 80% of your monthly budget. You want to know before you exceed it, not after.

<!-- TODO: replace estimates with real billing figures before publishing -->
