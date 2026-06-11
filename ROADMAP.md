<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/azureagentforge-logo-dark.png">
    <img alt="AzureAgentForge" src="docs/assets/azureagentforge-logo-light.png" width="440">
  </picture>
</p>

# Roadmap

## v1.0 — now

This stack runs in production on Azure; v1.0 is its sanitized, reusable version. Architecture, decisions, and full Terraform IaC are in the repo. Two cost profiles — cost-optimized (targets under $150/month) and hardened (zone-redundant, private endpoints) — and the repo's CI validates and plans both clean. The 13-role agent schema ships with tests. The model-router builds and runs locally: Azure AI Foundry as primary, any OpenAI-compatible endpoint as fallback. PaperClip, Honcho, and the agent-runtime ship as sanitized Dockerfiles and config. Telegram and Discord are each a single Terraform variable. Multi-tenant architecture is designed and partially scaffolded (see [`roadmap/multi-tenant/`](roadmap/multi-tenant/)).

`docker compose up` runs the working slice: Postgres and the model-router.

## v1.1 — in progress

**Shipped: the Forge Console** (`./forge`) — a local web GUI that replaced the
originally planned ANSI TUI. It covers preflight checks, an Azure
configuration wizard with tfvars preview, automatic local-state backend
handling, and a live-streamed `init → validate → plan → apply` flow with
typed confirmations for apply/destroy. The plan stage is validated against a
live subscription (39 resources on the cost-optimized profile). Measured cost
figures from real bills landed in [`docs/cost.md`](docs/cost.md).

**Remaining for v1.1:** image build and push for PaperClip/Honcho/agent-runtime,
Key Vault secret seeding, post-deploy smoke tests, one-command full local
stack via `docker compose --profile full up`, and the first fully validated
end-to-end Azure deploy from a clean subscription.

## Later

Multi-tenant implementation (the [`roadmap/multi-tenant/`](roadmap/multi-tenant/) design). More chat surfaces. Observability pipeline.
