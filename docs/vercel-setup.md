# Vercel Setup: Publish on Push to `main`

Goal: pushing to `main` publishes a production deployment to your Vercel URL.

This is done with **Vercel's native Git integration** — the GitHub repo is
connected to a Vercel project once, and Vercel deploys every push to `main`
automatically. No GitHub Actions workflow or webhook is required.

## Status: connected ✅

This is already set up:

- **Vercel project:** `dementia-gap-map` (scope: `lyncht248's projects`)
- **GitHub repo:** `lyncht248/dementia-gap-map`
- **Production branch:** `main`

Every `git push origin main` now produces a **Production** deployment; other
branches / PRs produce **Preview** deployments. The script and steps below are
kept for reference / re-linking on a fresh machine.

## Quick start (scripted)

From the repo root:

```bash
./scripts/setup-vercel.sh
```

The script installs/uses the Vercel CLI, logs you in if needed, links (or
creates) a project, connects the Git repo, and triggers a first production
deploy. Accept the interactive prompts.

## Manual steps (equivalent)

```bash
# 1. Install the CLI (one time)
npm i -g vercel        # or use: npx vercel@latest ...

# 2. Authenticate against your Vercel account (interactive)
vercel login

# 3. Link this directory to a Vercel project (create a new one when prompted)
vercel link

# 4. Connect the GitHub repo so pushes trigger deploys
vercel git connect

# 5. First production deploy (optional; pushes will do this from now on)
vercel deploy --prod
```

After step 4, every `git push origin main` produces a **Production**
deployment; pushes to other branches / PRs produce **Preview** deployments.
Confirm the connection under: Vercel project → **Settings → Git**, and set the
**Production Branch** to `main` if it isn't already.

## Non-interactive / CI auth

If you ever want to run the CLI without the interactive login (e.g. from CI),
create a token at <https://vercel.com/account/tokens> and export it:

```bash
export VERCEL_TOKEN=xxxxxxxxxxxx
./scripts/setup-vercel.sh
```

## Note on this repository

This repo is currently a **data project** — there is no web app or build step
yet (per the README, the visual layer is added after both data tracks
stabilize). Because of that, no `vercel.json` build configuration is committed:
a fabricated build would just fail. When the visual layer lands, configure the
framework/build in `vercel.json` (or the Vercel dashboard), then the same
push-to-`main` integration will publish it.

## Alternative: Deploy Hook (POST to a URL)

If instead you want a plain **URL you can POST to** to trigger a deploy (rather
than automatic Git-based deploys), create a **Deploy Hook** in the Vercel
project (Settings → Git → Deploy Hooks). That gives you a URL like
`https://api.vercel.com/v1/integrations/deploy/prj_xxx/yyy` that starts a
deployment when hit with `curl -X POST <url>`. You could call it from a GitHub
Action on push to `main` — ask if you'd like that wired up instead.
