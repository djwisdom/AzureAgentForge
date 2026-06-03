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

## v1.1 — next

A CLI installer with an ANSI TUI covers the full sequence: preflight, config, provision, image build and push for PaperClip/Honcho/agent-runtime, Key Vault seeding, deploy, smoke test. One-command full local stack via `docker compose --profile full up`. Measured cost figures replacing the current estimates. First end-to-end Azure deploy, validated against a live subscription.

## Later

Multi-tenant implementation (the [`roadmap/multi-tenant/`](roadmap/multi-tenant/) design). More chat surfaces. Observability pipeline.
