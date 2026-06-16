#!/usr/bin/env bash
# Build and push the AzureAgentForge service images to an Azure Container Registry.
#
# Uses `az acr build` — the build runs server-side inside ACR and the result is
# pushed there, so NO local Docker daemon is required. The build context is
# uploaded to ACR and built remotely.
#
# Two classes of image:
#
#   self-contained   context = the service directory; builds from this repo alone.
#                    -> model-router, memory-governor, watchdog
#
#   upstream-dependent
#                    context = the repo root; the Dockerfile pulls or COPYs
#                    upstream project sources that are NOT vendored in this repo
#                    (see docs/local-development.md). Each needs apps/<project>/
#                    populated first. This script PREFLIGHTS for those inputs and
#                    refuses to start a build that would fail partway, unless
#                    --skip-unbuildable is given.
#                    -> agent-runtime (image: hermes), honcho, paperclip
#
# Image names match the tags Terraform consumes
# (infrastructure/.../variables.tf *_image_tag): hermes, honcho, router,
# paperclip, memory-governor, watchdog. cloudflared is a public Docker Hub image
# and is never built here.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# All buildable services, in build order. cloudflared is intentionally absent.
ALL_SERVICES="model-router memory-governor watchdog agent-runtime honcho paperclip"
SELF_CONTAINED="model-router memory-governor watchdog"

# Default build-arg for the paperclip upstream pin (matches the Dockerfile ARG).
PAPERCLIP_VERSION="${PAPERCLIP_VERSION:-v2026.517.0}"

REGISTRY=""
TAG=""
SERVICES=""
PUSH_LATEST=0
DRY_RUN=0
SKIP_UNBUILDABLE=0

die() { echo "error: $*" >&2; exit 2; }

usage() {
  cat <<'EOF'
Usage: scripts/build-and-push.sh --registry NAME [options]

  -r, --registry NAME    Azure Container Registry name (not the login server). Required.
  -t, --tag TAG          Image tag. Default: short git SHA, else "latest".
  -s, --services LIST    Comma-separated subset to build. Default: all buildable.
      --self-contained   Build only the images this repo can build alone
                         (model-router, memory-governor, watchdog).
      --push-latest      Also tag/push :latest alongside the resolved tag.
      --skip-unbuildable Skip (don't fail) upstream-dependent images whose
                         apps/<project>/ inputs are not vendored.
  -n, --dry-run          Print the az commands without running them.
  -l, --list             Print the service/context table and exit.
  -h, --help             This help.

Examples:
  # Build the three self-contained images and push :<sha> + :latest
  scripts/build-and-push.sh -r myacr --self-contained --push-latest

  # Build everything that can build, skipping un-vendored upstream images
  scripts/build-and-push.sh -r myacr --skip-unbuildable
EOF
}

# image|context(relative to repo root)|dockerfile|class|required-input
svc_meta() {
  case "$1" in
    model-router)    echo "router|services/model-router|services/model-router/Dockerfile|self|" ;;
    memory-governor) echo "memory-governor|services/memory-governor|services/memory-governor/Dockerfile|self|" ;;
    watchdog)        echo "watchdog|services/watchdog|services/watchdog/Dockerfile|self|" ;;
    agent-runtime)   echo "hermes|.|services/agent-runtime/Dockerfile|upstream|apps/hermes/src" ;;
    honcho)          echo "honcho|.|services/honcho/Dockerfile|upstream|apps/honcho/src" ;;
    paperclip)       echo "paperclip|.|services/paperclip/Dockerfile|upstream|apps/paperclip" ;;
    *) return 1 ;;
  esac
}

print_table() {
  printf '%-16s %-16s %-12s %-34s %s\n' SERVICE IMAGE CLASS CONTEXT/DOCKERFILE REQUIRES
  for s in $ALL_SERVICES; do
    IFS='|' read -r image context dockerfile class required <<EOF
$(svc_meta "$s")
EOF
    printf '%-16s %-16s %-12s %-34s %s\n' "$s" "$image" "$class" "$context ($dockerfile)" "${required:-—}"
  done
}

resolve_tag() {
  if [ -n "$TAG" ]; then echo "$TAG"; return; fi
  if git -C "$REPO_ROOT" rev-parse --short HEAD >/dev/null 2>&1; then
    git -C "$REPO_ROOT" rev-parse --short HEAD
  else
    echo "latest"
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    -r|--registry) REGISTRY="${2:-}"; shift 2 ;;
    -t|--tag) TAG="${2:-}"; shift 2 ;;
    -s|--services) SERVICES="${2:-}"; shift 2 ;;
    --self-contained) SERVICES="$(echo "$SELF_CONTAINED" | tr ' ' ',')"; shift ;;
    --push-latest) PUSH_LATEST=1; shift ;;
    --skip-unbuildable) SKIP_UNBUILDABLE=1; shift ;;
    -n|--dry-run) DRY_RUN=1; shift ;;
    -l|--list) print_table; exit 0 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

# Normalize the requested service list.
if [ -z "$SERVICES" ]; then
  REQUESTED="$ALL_SERVICES"
else
  REQUESTED="$(echo "$SERVICES" | tr ',' ' ')"
fi
for s in $REQUESTED; do
  svc_meta "$s" >/dev/null 2>&1 || die "unknown service '$s'. Known: $ALL_SERVICES"
done

[ -n "$REGISTRY" ] || [ "$DRY_RUN" -eq 1 ] || die "--registry is required (or use --dry-run)"
[ -n "$REGISTRY" ] || REGISTRY="<registry>"
TAG="$(resolve_tag)"

echo "registry=$REGISTRY tag=$TAG dry_run=$DRY_RUN"
echo "requested: $REQUESTED"
echo

built="" ; skipped="" ; failed=""

for s in $REQUESTED; do
  IFS='|' read -r image context dockerfile class required <<EOF
$(svc_meta "$s")
EOF

  # Assemble the az acr build command first so the preflight can show it.
  set -- az acr build --registry "$REGISTRY" --image "$image:$TAG" --file "$dockerfile"
  [ "$PUSH_LATEST" -eq 1 ] && set -- "$@" --image "$image:latest"
  [ "$s" = "paperclip" ] && set -- "$@" --build-arg "PAPERCLIP_VERSION=$PAPERCLIP_VERSION"
  set -- "$@" "$context"

  # Preflight upstream-dependent inputs — each service lands in exactly one bucket.
  if [ "$class" = "upstream" ] && [ -n "$required" ] && [ ! -e "$REPO_ROOT/$required" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "SKIP  $s: '$required' not vendored (would run): $*"
      skipped="$skipped $s"; continue
    elif [ "$SKIP_UNBUILDABLE" -eq 1 ]; then
      echo "SKIP  $s: '$required' not vendored — see docs/local-development.md"
      skipped="$skipped $s"; continue
    else
      echo "FAIL  $s: '$required' not vendored — vendor it or pass --skip-unbuildable" >&2
      failed="$failed $s"; continue
    fi
  fi

  echo "BUILD $s -> $image:$TAG"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "    (cd $REPO_ROOT && $*)"
    built="$built $s"
    continue
  fi
  if ( cd "$REPO_ROOT" && "$@" ); then
    built="$built $s"
  else
    echo "FAIL  $s: az acr build returned non-zero" >&2
    failed="$failed $s"
  fi
done

echo
echo "built:  ${built:-none}"
echo "skipped:${skipped:-none}"
echo "failed: ${failed:-none}"

# Emit the resolved tag for the GitHub Actions step that wires it into Terraform.
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "tag=$TAG" >> "$GITHUB_OUTPUT"
  echo "built=$(echo "$built" | xargs echo)" >> "$GITHUB_OUTPUT"
fi

[ -z "$failed" ] || exit 1
