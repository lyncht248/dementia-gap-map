#!/usr/bin/env bash
#
# setup-vercel.sh
#
# One-shot helper to connect this GitHub repo to a Vercel project so that
# pushing to `main` publishes a production deployment to your Vercel URL
# (Vercel's native Git integration).
#
# This uses the Vercel CLI and MUST be run locally / interactively, because
# it needs to authenticate against YOUR Vercel account. It cannot be run from
# an unauthenticated CI or sandbox environment.
#
# Usage:
#   ./scripts/setup-vercel.sh
#
# Optional non-interactive auth (e.g. CI): export a token first
#   export VERCEL_TOKEN=xxxxxxxx
#   ./scripts/setup-vercel.sh
#
set -euo pipefail

REPO_SLUG="lyncht248/dementia-gap-map"
PROD_BRANCH="main"

# Pass --token automatically if VERCEL_TOKEN is exported.
TOKEN_ARG=()
if [[ -n "${VERCEL_TOKEN:-}" ]]; then
  TOKEN_ARG=(--token "${VERCEL_TOKEN}")
fi

vercel() {
  # Prefer a globally installed CLI; otherwise fall back to npx.
  if command -v vercel >/dev/null 2>&1; then
    command vercel "$@"
  else
    npx --yes vercel@latest "$@"
  fi
}

echo "==> Checking Vercel authentication..."
if ! vercel whoami "${TOKEN_ARG[@]}" >/dev/null 2>&1; then
  echo "    Not logged in. Launching 'vercel login' (interactive)..."
  vercel login "${TOKEN_ARG[@]}"
fi
echo "    Authenticated as: $(vercel whoami "${TOKEN_ARG[@]}")"

echo
echo "==> Linking this directory to a Vercel project..."
echo "    (Accept the prompts to create a new project or link an existing one.)"
vercel link "${TOKEN_ARG[@]}"

echo
echo "==> Connecting the Git repository (${REPO_SLUG}) to the Vercel project..."
echo "    After this, pushes to '${PROD_BRANCH}' auto-deploy to production."
vercel git connect "${TOKEN_ARG[@]}"

echo
echo "==> Triggering an initial production deployment..."
vercel deploy --prod "${TOKEN_ARG[@]}"

echo
echo "Done. From now on, 'git push origin ${PROD_BRANCH}' will publish to your"
echo "Vercel production URL automatically."
