# Deploying OceanTrace

The whole system (React UI, FastAPI, SQLite registry, CPU ONNX models) runs as
**one container on one port**. The free public deployment is a Hugging Face
Docker Space; the same image runs unchanged on any VM.

| Layer | Where it runs | Cost |
|---|---|---|
| Frontend | built into the image, served by FastAPI (`backend/core/spa.py`) | free |
| Backend / APIs | same container, uvicorn on `$PORT` (7860) | free |
| Database | SQLite inside the data bundle, restored at boot | free |
| AI models | `screen.onnx` + `segment.onnx` on CPU ONNX Runtime, shipped in the bundle | free |
| Data + weights | **private** Hugging Face dataset (`scripts/deploy/data_bundle.py`) | free |
| Hosting + HTTPS | Hugging Face Space, CPU basic (2 vCPU, 16 GB RAM) | free |
| CI / CD | GitHub Actions: `ci.yml` (build + smoke), `deploy-space.yml` (redeploy on push) | free |

Public link: `https://<user>-<space>.hf.space`. Share that one, not the
`huggingface.co/spaces/...` page: the page wraps the app in an iframe, where
the browser treats the session cookie as third-party.

## First deploy (once, from the demo laptop)

Prerequisites: `hf auth login` with a **write** token, weights present in
`main_system/backend/services/detection/weights/`, everything committed.

```bash
# 1. Stage the data the app serves (~9.5 GB of the 36 GB data/; hard links, no copy)
.venv/Scripts/python.exe scripts/deploy/data_bundle.py build

# 2. Upload it to a PRIVATE dataset (resumable; re-run if the network drops)
.venv/Scripts/python.exe scripts/deploy/data_bundle.py upload --repo IndhuPriyan/oceantrace-data

# 3. Create the Space, set its variables + secrets, push the code
.venv/Scripts/python.exe scripts/deploy/push_space.py --space IndhuPriyan/oceantrace \
    --configure --data-repo IndhuPriyan/oceantrace-data
```

Hugging Face then builds the image (~10 min). On first boot the container pulls
the bundle (a few minutes inside HF's network) before the app answers.

`--configure` sets:
- **Variables:** `OT_DATA_REPO` and `OT_PUBLIC_EVALUATOR=true`. Judges get the no-login evaluator view, and the Login button still shows RBAC.
- **Secrets:** `HF_TOKEN` (reads the private bundle), a fresh `SECRET_KEY` and `JWT_SECRET`, and every provider key found in `.env` (CDSE, Earthdata, CMEMS, CDS, AISStream).

## Continuous deployment

Add to the GitHub repo (Settings → Secrets and variables → Actions):
- **Secret:** `HF_TOKEN`, a Hugging Face write token.
- **Variable:** `HF_SPACE`, for example `IndhuPriyan/oceantrace`.

Every push to `main` that touches code in the image then redeploys. Data
changes are separate: re-run steps 1–2. The Space picks up the new bundle
revision on its next restart (Space settings → Factory rebuild).

## What to know before the demo

- **Cold start.** Free Spaces sleep after 48 h without visitors, and the disk is ephemeral. The next visitor waits for the image to start and the bundle to download again, and every restart puts the demo back to its published state. Open the link before presenting. A free UptimeRobot monitor on `/healthz` every 5 minutes keeps it awake.
- **Replays, not live scene downloads.** A new live run downloads Sentinel-1 scenes; on 2 vCPUs that is slow. The sealed runs in the bundle open instantly.
- **Provider keys.** A stage whose provider has no key fails honestly (no mock fallback). Check Monitoring → Test all after deploy.
- **Sealed runs stay verifiable.** Run artefacts are never rewritten. Paths they recorded on the laptop (`C:\Users\...`) are re-anchored at read time by `backend/core/paths.py`, so integrity checks still pass.

## Same image on a VM (Oracle Always Free, Azure for Students, etc.)

```bash
docker build -f docker/app.Dockerfile -t oceantrace .
docker run -d --restart unless-stopped -p 80:7860 \
  -e OT_DATA_REPO=IndhuPriyan/oceantrace-data -e HF_TOKEN=<read token> \
  -e OT_PUBLIC_EVALUATOR=true -e SECRET_KEY=<random> \
  -v oceantrace-data:/app/data oceantrace
```

With a volume, the bundle downloads once, and writes made during the demo
survive restarts. Put HTTPS in front with Cloudflare Tunnel or Caddy. The
two-container `docker compose up` stack still works for local development.

## Local check of the deploy image (no upload needed)

The staged bundle is hard-linked to your real `data/`, so mount it **read-only**
and give the container its own copy of the database:

```bash
docker build -f docker/app.Dockerfile -t oceantrace:local .
mkdir -p .deploy/state && cp .deploy/bundle/oceantrace.db .deploy/state/
docker run --rm -p 7860:7860 -e OT_PUBLIC_EVALUATOR=true -e SESSION_COOKIE_SECURE=false \
  -e DATABASE_URL=sqlite:////app/state/oceantrace.db \
  -v "$PWD/.deploy/bundle:/app/data:ro" -v "$PWD/.deploy/state:/app/state" oceantrace:local
```
Then open http://localhost:7860.

## The project page (GitHub Pages) and its built-in replay

`site/` is the page linked from the SIH presentation
(https://indhupriyan.github.io/AIS-SAR-OilSpill-Vessel-Attribution/). It is
published by `.github/workflows/pages.yml` on every push to `main` that
touches `site/`, and it points at the live system through `LIVE_URL` at the
bottom of `site/index.html`.

The page carries its own **incident replay** (`site/replay.js` +
`site/demo/flagship.json`) so the flagship case can be shown while the live
system is off. The JSON is exported from the sealed run's own artefacts, never
hand-written; regenerate it whenever the flagship run changes:

```bash
.venv/Scripts/python scripts/site/export_demo.py      # -> site/demo/flagship.json (~470 KB, 95 KB gzipped)
```

## What the public evaluator can and cannot do

With `OT_PUBLIC_EVALUATOR=true` (the deploy image's default) a visitor with no
session runs as the evaluator account: the whole investigation workflow is
open. What stays with the team, and is refused by the server
(`backend/core/authz.py`) as well as hidden or rendered view-only in the UI:

* provider credentials: the masked list is readable, but `PUT /api/keys`,
  `POST /api/keys/{provider}/test` and `GET /api/keys/audit` are 403; the
  Credentials and Data Sources screens show configured / unset with no form;
* accounts and staffing: `/api/users` writes, zone deletion and officer
  assignment; the Users & Roles and Credentials entries are not listed in the
  sidebar or the command palette;
* who built what: provider `owner` fields come back `null` and the Help page
  drops its Owner column for the evaluator;
* live workers and logs: AIS stream control, live-AIS pruning, log clearing.

A real login always wins over the evaluator, so signing in as an admin
restores every one of these.
