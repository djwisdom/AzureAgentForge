# Vendoring & Buildability — Design Spec

**Date:** 2026-06-18
**Status:** Approved (design); ready for implementation planning
**Sub-project:** #1 of 5 in the "make AAF deployment super easy for an external adopter" track
**Audience target:** External adopter — a stranger who finds AAF on GitHub with a fresh Azure subscription and zero context

---

## 1. Problem

The public AzureAgentForge README sells a full platform — PaperClip (orchestration + UI),
Hermes (agent runtime), Honcho (private memory) — wrapped in an Azure foundation. But a
stranger who clones the public repo **cannot build three of the seven service images**:

- `services/agent-runtime/Dockerfile` does `COPY apps/hermes/src/ .`
- `services/honcho/Dockerfile` does `COPY apps/honcho/src/...`
- `services/paperclip/Dockerfile` does `COPY apps/paperclip/*` (AAF patch/wrapper files) and
  `COPY apps/hermes/src/...` (adapter + skills)

`apps/` has **never existed in the public repo** — it is not gitignored and appears in no
branch's history. The upstream source and AAF's own patch files live only in the private
MRTek platform repo (`mrt-ai-agent-platform`). `scripts/build-and-push.sh` therefore marks
these three as "upstream-dependent / unbuildable" and refuses to build them without
`--skip-unbuildable`.

Because the images can't be built, the deploy must run with `deploy_upstream_apps = false`,
which stands up the Azure foundation and the four self-contained services but **not the
actual agent platform**. The headline experience the repo advertises is unreachable for an
external adopter.

This sub-project is the enabling gate: nothing downstream (one-command deploy, doctor,
verify, docs) delivers the full platform until a fresh clone can build all seven images.

## 2. Goal

A fresh `git clone --recursive` of the **public** repo can build all three upstream images
via `scripts/build-and-push.sh`, with **no access to the private MRTek repo** and **no manual
file copying**.

**Success criteria.** From only the public repo URL on a clean machine:

```bash
git clone --recursive <public-url> && cd AzureAgentForge
scripts/build-and-push.sh -r <acr>
```

produces all seven images in the target ACR, and CI confirms no internal/private references
leaked into the committed `apps/` files.

## 3. Chosen approach — A: Submodules + committed AAF files

Selected over "full vendored copy" (B) and "build-time fetch for all" (C). Approach A mirrors
how the private repo is already structured, keeps the public repo's diff honestly "AAF's own
work plus pinned upstreams," and gives explicit, auditable upstream pins. Its one cost — the
`git clone --recursive` footgun — is cheaply mitigated by the doctor (sub-project #3) and a
README clone one-liner.

- **Upstream Hermes & Honcho** → git submodules pinned to exact commit SHAs.
- **AAF-authored patch/override files** → committed directly into the public repo after a
  sanitization pass.
- **PaperClip** → unchanged; its Dockerfile already `git clone`s the pinned public tag at
  build time.

## 4. Scope

### 4.1 In scope (four workstreams)

**WS1 — Upstream submodules.** Add `.gitmodules` + gitlinks pinned to exact SHAs (not floating
tags) for reproducibility:

| Path | Upstream | Pin (SHA) | Tag/describe |
|---|---|---|---|
| `apps/hermes/src` | `https://github.com/NousResearch/hermes-agent.git` | `a91a57fa5a13d516c38b07a141a9ce8a3daabeb0` | `v2026.5.16` |
| `apps/honcho/src` | `https://github.com/plastic-labs/honcho.git` | `72753721282b289bb63f51c03e4c69b5203d1f92` | `archive-v0.0.1-444-g7275372` |

PaperClip is **not** a submodule; the `services/paperclip/Dockerfile` keeps
`git clone --depth=1 --branch v2026.517.0 https://github.com/paperclipai/paperclip.git`.

**WS2 — AAF-authored file port + sanitization** *(sensitive — see §6 gate)*. Reconcile the
exact `COPY` list in the public `services/*/Dockerfile`s against the private inventory and port
**only what the public Dockerfiles reference**. Known private inventory:

- `apps/paperclip/` (~208K, 15 files): `patch-plugin-host.mjs`, `patch-plugin-secrets-handler.mjs`,
  `patch-plugin-worker-manager.mjs`, `patch-adapter.mjs`, `patch-adapter-router-env.mjs`,
  `patch-hermes-src.py`, `patch-hermes-cost-envelope.mjs`, `patch-wake-kick.mjs`,
  `auth-proxy.mjs`, `cost-envelope.mjs`, `discord-plugin-selfheal.mjs`, `ddgs-wrapper.sh`,
  `brave-search-wrapper.sh`, `docker-entrypoint.sh`, `skills-ui.html` (plus a private
  `Dockerfile` that is **not** ported — the public Dockerfile already lives at
  `services/paperclip/Dockerfile`).
- `apps/hermes/overrides/` (~124K, incl. `skills/`).

Each ported file passes a **sanitization pass** before commit: scrub internal hostnames
(`ca-*-dev`, `foundry-mrtek-dev`, `*.mrtek*`), Key Vault secret names, org/subscription IDs,
agent/run-id metadata, and any tokens or private paths — replacing with env-driven or
public-safe values. Behavior must be preserved; only environment-specific identifiers change.

**WS3 — Build wiring** (`scripts/build-and-push.sh`). The "unbuildable" gate keys on
`apps/<project>/src` presence, so initialized submodules satisfy hermes/honcho automatically;
verify the `apps/paperclip` marker resolves once the AAF files are ported. Add a loud,
actionable error when submodules are absent (tell the user to run
`git submodule update --init --recursive`). Confirm `--self-contained`, `--skip-unbuildable`,
and the full build path all still behave.

**WS4 — Deploy default (verify-only, no change).** Confirm `deploy_upstream_apps` stays at its
current opt-in default within this sub-project. Flipping it before the image-build step exists
would make `terraform apply` reference images not yet in ACR.

### 4.2 Out of scope (later sub-projects)

- The three-pass deploy orchestration (Pass 1 targeted → seed → build → Pass 2) — **#2**.
- Flipping the `deploy_upstream_apps` default to `true` and wiring the image-build step — **#2**.
- Pre-flight doctor (submodule check, `az login`, RP registration, Foundry endpoint/model) — **#3**.
- Post-deploy verify, cost preview, teardown story — **#4**.
- Golden-path docs rewrite — **#5**.

This sub-project stops at *"a stranger can build the images."*

## 5. Architecture & data flow

```
git clone --recursive <public-url>
        │
        ├─ submodules populate apps/hermes/src  (NousResearch/hermes-agent @ a91a57f)
        └─ submodules populate apps/honcho/src  (plastic-labs/honcho       @ 7275372)
        │
        ▼
scripts/build-and-push.sh -r <acr>
        │  (per service: image | context | dockerfile | kind | required-input)
        ├─ self-contained: model-router, memory-governor, watchdog, teams-bridge
        └─ upstream:       agent-runtime(apps/hermes/src), honcho(apps/honcho/src),
                           paperclip(apps/paperclip/* + build-time clone of paperclipai/paperclip)
        │
        ▼
az acr build  (server-side; no local Docker daemon required)
        │
        ▼
7 images in <acr>  →  referenced by terraform (deploy step, sub-project #2)
```

## 6. The sanitization / security gate (hard blocker)

Porting AAF's own `.mjs`/`.py`/`.sh` files from the private platform into a **public** repo is
outward-facing and hard to reverse. No file in `apps/` is committed to a public branch until:

1. **Sanitization pass** — manual review of every ported file for internal hostnames, secret
   names, org/subscription/run IDs, tokens, and private paths; replace with env-driven or
   public-safe values.
2. **Security-auditor review** — a `security-auditor` agent pass over the full `apps/` diff.
3. **CI secret/internal-reference scan** — an automated grep-based gate over `apps/` for the
   forbidden patterns above (`ca-*-dev`, `foundry-mrtek-dev`, `*.mrtek*`, KV secret-name
   conventions, UUID/subscription patterns, known internal tokens). The scan must run on every
   PR and block merge on any hit.

All three must pass before the branch is pushed to the public remote.

## 7. Failure modes & handling

| Failure | Detection | Handling |
|---|---|---|
| Submodules not initialized | `build-and-push.sh` finds no `apps/*/src` | Fail loud with the exact `git submodule update --init --recursive` fix |
| Upstream repo/tag unavailable | Submodule fetch error on clone | Pinned SHA resolves while the repo exists; README links the upstreams; document a mirror fallback |
| Internal reference leaks into `apps/` | CI secret/internal-ref scan | Block merge; the scan is the backstop behind manual sanitization |
| PaperClip upstream tag moves/deleted | Build-time clone fails | Pin is an immutable release tag; document bumping `PAPERCLIP_VERSION` |
| Self-contained builds regress | CI build of model-router et al. | Existing build path must pass unchanged; covered by acceptance test |

## 8. Testing & acceptance

- **Clean-room build test.** `git clone --recursive` into an empty temp dir (no private repo on
  the machine), then build all three upstream images successfully — proves no hidden dependency
  on the private tree or manual copying.
- **CI job.** (1) init submodules; (2) run the secret/internal-reference scan over `apps/`;
  (3) validate each upstream Dockerfile's `COPY` paths and build context resolve — at minimum a
  context/COPY-path validation, a full `az acr build --no-push` (or `docker build --target
  builder`) where CI budget allows.
- **Regression.** Self-contained image builds (`--self-contained`) still pass unchanged.

## 9. Risks

- **Sanitization miss** — the dominant risk; mitigated by the three-layer §6 gate (manual +
  agent review + CI scan).
- **Submodule UX** — the `--recursive` footgun; out-of-scope to fully solve here, mitigated
  downstream by the doctor (#3) and a README one-liner.
- **Upstream drift / availability** — pinned SHAs make builds reproducible as long as the
  upstreams exist; a mirror fallback is documented, not automated, in this sub-project.

## 10. Dependencies & sequencing

- **Base branch:** `public/main` (local `main` is stale/divergent — do not branch from it).
- **Blocks:** sub-project #2 (one-command deploy) consumes buildable images and flips the
  `deploy_upstream_apps` default.
- **No dependency on:** #3–#5.
