# GigSim reviewer deployment

The reviewer image bundles Next.js, FastAPI and GigSim. It runs one API worker and keeps both
SQLite files in `/var/lib/gigbid`. The first start seeds the synthetic mini-world. Later starts
reuse the simulator database and the approval checkpoints from that volume.

## Build and run

```bash
docker build -f infra/docker/reviewer.Dockerfile -t gigsim-reviewer:local .
docker run --rm -it --entrypoint caddy gigsim-reviewer:local hash-password
```

The second command prompts for the reviewer password and prints a bcrypt hash. Set
`GIGBID_REVIEWER_HASH` to the hash in the host's environment or secret settings.
`GIGBID_REVIEWER_USER` defaults to `reviewer`.

```bash
docker volume create gigsim-reviewer-state
docker run -d --name gigsim-reviewer --restart unless-stopped \
  --publish 127.0.0.1:10000:10000 \
  --mount source=gigsim-reviewer-state,target=/var/lib/gigbid \
  --env GIGBID_REVIEWER_HASH --env GIGBID_REVIEWER_USER gigsim-reviewer:local
curl --fail http://127.0.0.1:10000/healthz
```

Point an HTTPS ingress at the reviewer port. A container platform can set `PORT` and provide
HTTPS ingress itself. Configure one instance with a writable persistent disk at
`/var/lib/gigbid` and use `GET /healthz` for the health check. Each new deployment should use
the same disk and a green commit from `main`.

The reviewer password protects the app and its API. Only the health endpoint is public.
FastAPI, GigSim and the simulator admin routes listen on container loopback. The service uses
synthetic accounts and simulated Connects.

## Verify a fresh deployment

Use a dedicated fresh test instance or reserve the AI/RAG fixture for the smoke run. The
test makes one real simulator submission and spends simulated Connects on that fixture.
Set `GIGBID_LIVE_URL`, `GIGBID_REVIEWER_USER` (optional) and `GIGBID_REVIEWER_PASSWORD` in the
test environment before running it.

```bash
cd apps/web
npm ci
npx playwright install chromium --only-shell
npx playwright test --config=playwright.hosted.config.ts
```

The test checks reviewer authentication, checkpoint recovery after refresh, the approved
Connects quote and the single submission receipt. CI also restarts the image with a pending
Stripe approval to verify that the mounted volume preserves it. A reviewer can use that job
separately from the AI/RAG smoke run.

## Render free tier

The public demo runs this image as a Render web service on the Free instance type:
Dockerfile path `./infra/docker/reviewer.Dockerfile`, build context `.`, health check path
`/healthz`, and `GIGBID_REVIEWER_HASH` / `GIGBID_REVIEWER_USER` as environment variables.
Free instances cannot attach a persistent disk, so `/var/lib/gigbid` is ephemeral there: the
service reseeds the synthetic mini-world on every start, and pending approvals do not survive
a restart or the sleep after 15 idle minutes. Use a paid instance with a disk at
`/var/lib/gigbid` to keep state between restarts.
