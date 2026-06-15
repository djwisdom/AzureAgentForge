<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/azureagentforge-logo-dark.png">
    <img alt="AzureAgentForge" src="docs/assets/azureagentforge-logo-light.png" width="440">
  </picture>
</p>

# Roadmap

## v1.0 — foundation (released)

This stack runs in production on Azure; v1.0 is its sanitized, reusable version. Architecture, decisions, and full Terraform IaC are in the repo. Two cost profiles — cost-optimized (targets under $150/month) and hardened (zone-redundant, private endpoints) — and the repo's CI validates and plans both clean. The 13-role agent schema ships with tests. The model-router builds and runs locally: Azure AI Foundry as primary, any OpenAI-compatible endpoint as fallback. PaperClip, Honcho, and the agent-runtime ship as sanitized Dockerfiles and config. Telegram and Discord are each a single Terraform variable. Multi-tenant architecture is designed and partially scaffolded (see [`roadmap/multi-tenant/`](roadmap/multi-tenant/)).

`docker compose up` runs the working slice: Postgres and the model-router.

## v1.1 — shipped

**Forge Console** (`./forge`) — a local web GUI installer that replaced the
originally planned ANSI TUI: preflight checks, an Azure configuration wizard
with tfvars preview, automatic local-state backend handling, and a
live-streamed `init → validate → plan → apply` flow with typed confirmations.
The plan stage is validated against a live subscription (39 resources on the
cost-optimized profile). **Measured cost figures** from real bills landed in
[`docs/cost.md`](docs/cost.md).

**Governance & safety.** Role-scoped toolsets, a dedicated `CostGuardian` role,
and a **destroy-aware approval gate** that lets routine plans apply unattended
but blocks any delete/replace behind explicit human approval — in the
Forge Console and as a **reference CI/CD deploy pipeline**
([`.github/workflows/deploy.yml`](.github/workflows/deploy.yml),
[setup](docs/deploy-pipeline.md)) with OIDC auth and no stored secrets. The
[governance & blast-radius walkthrough](docs/walkthroughs/governance-and-blast-radius.md)
traces a destructive request being refused at every layer, backed by **14 golden
orchestration replay fixtures** ([`tests/replay/`](tests/replay/)).

**Design references.** The [governed-memory architecture](docs/design/memory-system.md)
— four planes, six classes, computed trust, contradiction detection, and a
self-improvement loop — is documented to build toward; the governor service code
is not bundled here.

## v1.2 — next

Closing the path from "infrastructure provisioned" to "fully running stack in one command":

- Image build and push for PaperClip/Honcho/agent-runtime
- Key Vault secret seeding
- Full service deployment automation
- Post-deploy smoke tests
- One-command full local stack (`docker compose --profile full up`)
- Full Microsoft Teams integration
- Secret-expiry monitoring: the watchdog detector that lists Key Vault secret/cert expiry and files an issue before a lapsed credential takes down the agents that depend on it (code shipped flag-gated off; goes live with the first deploy)
- First fully validated end-to-end Azure deploy from a clean subscription

## Later

Multi-tenant implementation (the [`roadmap/multi-tenant/`](roadmap/multi-tenant/) design). More chat surfaces. Observability pipeline.
