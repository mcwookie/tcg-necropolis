# Running TCG Necropolis with Docker

The repository includes everything to run the app in a container and keep a server
up to date automatically:

| File | What it does |
|---|---|
| `Dockerfile` | Builds the image: Python 3.12, the app, an unprivileged user. Code only, no card data |
| `compose.yaml` | Runs the app, plus an optional Caddy service (`https` profile) for HTTPS and a password gate |
| `deploy/docker-entrypoint.sh` | On every start: reload card catalogs and reindex collections, then run the server |
| `deploy/env.example` | Settings template. Copy to `.env` next to `compose.yaml` |
| `deploy/Caddyfile`, `deploy/caddy-users.example` | Caddy config and password file template |
| `.github/workflows/docker.yml` | On every push to `main`: run the tests, then build and publish the image to GitHub's container registry |
| `deploy/update.sh` + `tcgnecro-update.{service,timer}` | On the server: every 5 minutes, pull the newest image and restart the app if it changed |

```
git push to main ──► GitHub Actions: tests, then build ──► ghcr.io/mcwookie/tcg-necropolis:latest
                                                                    │
your server: tcgnecro-update.timer (every 5 min) ── docker compose pull + up ◄─┘
```

The server only ever *pulls*. GitHub never connects to it, and it holds no GitHub
credentials.

## The data folder

Everything you own lives in one folder on the host, mounted into the container at
`/data`. It has the same layout as a non-Docker install:

```
data/
  collections/{game}/{name}/   card images
  decks/  users.json           decks and profiles (back these up)
  cache/                       ArkhamDB downloads
  secret.key                   session key (generated if missing)
  index.db  thumbs/            caches, rebuilt automatically
```

`index.db` stores absolute paths, so the container rebuilds it on every start (a few
seconds). Don't point a container and a non-Docker install at the same data folder at
the same time: each rewrites the index with its own paths. If you switch back to a
non-Docker install, run `tcgnecro reindex` there.

The container runs as the uid/gid in `.env` (`TCGNECRO_UID` / `TCGNECRO_GID`, default
1000). Set them to the owner of the data folder (`id -u`, `id -g`), or the app can't
write decks and thumbnails.

## Quick start

```bash
git clone https://github.com/mcwookie/tcg-necropolis.git /opt/tcgnecro
cd /opt/tcgnecro
cp deploy/env.example .env          # then edit: TCGNECRO_DATA_DIR, TCGNECRO_UID/GID
docker compose up -d                # uses the published image
docker compose logs -f app          # "Uvicorn running" means it's up
```

Open **http://127.0.0.1:8000**. To build the image from your checkout instead of
pulling it, use `docker compose up -d --build`.

**Moving an existing install into Docker:** copy `collections/`, `decks/`,
`users.json`, `cache/` and (optionally) `secret.key` from the old `data/` folder into
`TCGNECRO_DATA_DIR`. Leave `index.db` and `thumbs/` behind; they're rebuilt. Then
`docker compose up -d`.

## Settings (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `TCGNECRO_DATA_DIR` | `./data` | The data folder on this machine |
| `TCGNECRO_UID`, `TCGNECRO_GID` | `1000` | Run the app as this user, normally the data folder's owner |
| `TCGNECRO_BIND` | `127.0.0.1` | Interface for the app's port: `127.0.0.1` = this machine only, `0.0.0.0` = your network |
| `TCGNECRO_PORT` | `8000` | Port on this machine |
| `TCGNECRO_SECURE_COOKIES` | `0` | Set `1` whenever people use the site over HTTPS |
| `TCGNECRO_DOMAIN` | `localhost` | `https` profile: the domain Caddy gets a certificate for |
| `COMPOSE_PROFILES` | (none) | Set `https` to include Caddy in every `docker compose` command |
| `TCGNECRO_IMAGE` | `ghcr.io/mcwookie/tcg-necropolis:latest` | Which image to run. Pin `:sha-<commit>` to stop following `main` |
| `TCGNECRO_SOURCE_URL` | your GitHub repo | The footer's "Source code" link (AGPL) |
| `TCGNECRO_REBUILD_INDEX` | `1` | Set `0` to skip the catalog reload and reindex on start |
| `TCGNECRO_SYNC_MINUTES` | `60` | Minutes between automatic syncs of decks linked to ArkhamDB; `0` turns it off |

## HTTPS and a password gate (internet-facing servers)

The `https` profile adds Caddy, which gets a free certificate from Let's Encrypt and asks
for a password before anyone reaches the app. That gate matters: the app's own sign-in
is still a profile picker (see [ACCOUNTS.md](ACCOUNTS.md)).

1. Point your domain's DNS `A` record at the server, and open ports 80 and 443.
2. In `.env`:
   ```bash
   COMPOSE_PROFILES=https
   TCGNECRO_DOMAIN=necro.example.com
   TCGNECRO_SECURE_COOKIES=1
   ```
3. Create the password file, one line per person (do this **before** starting Caddy;
   otherwise Docker creates an empty folder in its place):
   ```bash
   cp deploy/caddy-users.example deploy/caddy-users
   docker run --rm caddy:2 caddy hash-password --plaintext 'their password'
   # put "name <hash>" lines in deploy/caddy-users, and delete the example line
   ```
4. `docker compose up -d`, then open `https://necro.example.com`.

To add or change a password later: edit `deploy/caddy-users`, then
`docker compose restart caddy`. `.env` and `deploy/caddy-users` are in `.gitignore`, so
they never get committed.

## Publishing images (GitHub Actions)

`.github/workflows/docker.yml` runs on every push to `main` (and on demand from the
Actions tab):

1. **test:** installs the app and runs `pytest`. A failing test stops the release.
2. **image:** builds for `linux/amd64` and `linux/arm64`, then pushes
   `ghcr.io/mcwookie/tcg-necropolis:latest` and `:sha-<commit>`.

It needs no secrets; it uses the workflow's built-in `GITHUB_TOKEN`.

**One-time step after the first build:** GitHub creates the image package as *private*.
Either make it public (your profile → **Packages** → `tcg-necropolis` → **Package
settings** → **Change visibility** → Public), or log the server in with a personal access
token that has `read:packages`:
`echo <token> | docker login ghcr.io -u mcwookie --password-stdin`. The image contains
only code, the same as the public repository.

## Automatic updates on the server

`deploy/update.sh` does the whole update:

- pulls the latest `compose.yaml` and Caddy config with `git pull`
- pulls the latest image
- runs `docker compose up -d`, which recreates the app only if its image changed

A systemd timer runs it every 5 minutes. Install it once:

```bash
cd /opt/tcgnecro
sudo install -m 644 deploy/tcgnecro-update.service deploy/tcgnecro-update.timer /etc/systemd/system/
sudo sed -i "s/CHANGE_ME/$USER/" /etc/systemd/system/tcgnecro-update.service
# Checked out somewhere other than /opt/tcgnecro? Fix the ExecStart path in the .service too.
sudo usermod -aG docker "$USER"            # if you aren't in the docker group yet; log in again
sudo systemctl daemon-reload
sudo systemctl enable --now tcgnecro-update.timer
```

Check on it:

```bash
systemctl list-timers tcgnecro-update      # next and last run
journalctl -u tcgnecro-update -n 20        # what the last runs did
sudo systemctl start tcgnecro-update       # update right now
```

From a push to `main` to the new version running takes roughly 5–10 minutes: the tests
and the two-architecture build (most of it), then up to 5 minutes for the timer. Each
restart briefly interrupts the site while the index rebuilds.

**Pause or roll back:** `sudo systemctl stop tcgnecro-update.timer` stops updates. To
run a known-good build, set `TCGNECRO_IMAGE=ghcr.io/mcwookie/tcg-necropolis:sha-<commit>`
in `.env` and run `deploy/update.sh` once. With a pinned tag the timer keeps that build.

**Why not a self-hosted GitHub runner on the server?** Your repository is public. GitHub
warns that self-hosted runners on public repos can be made to run code from anyone's
pull request. Pulling published images avoids that entirely.

## Everyday commands

```bash
docker compose ps                                     # status (health: healthy)
docker compose logs -f app                            # app logs
docker compose exec app tcgnecro user list            # any CLI command, inside the container
docker compose exec app tcgnecro user add Friend
docker compose exec app tcgnecro import /data/new.pnx # copy the archive into the data folder first
docker compose exec app tcgnecro catalog ahlcg --refresh   # new Arkham expansion
docker compose exec app tcgnecro decks sync           # sync linked decks with ArkhamDB now
docker compose restart app                            # restart (also rebuilds the index)
```

**Backups:** copy `decks/` and `users.json` from the data folder. They're the only
copies of users' work. A provider snapshot or nightly `tar` (as in
[DEPLOY_VPS.md](DEPLOY_VPS.md#9-backups)) works the same with Docker.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Permission denied` under `/data` in the logs | `TCGNECRO_UID`/`GID` don't own the data folder |
| `docker compose pull` says *denied* or *unauthorized* | The GHCR package is still private. See "Publishing images" |
| Caddy fails to start: *is a directory* | `deploy/caddy-users` didn't exist when Caddy first started. `rm -r deploy/caddy-users`, create the file, `docker compose up -d` |
| Signed out after every page | `TCGNECRO_SECURE_COOKIES=1` but the site is used over plain `http://` |
| Container restarting, `unhealthy` | `docker compose logs app`. The first start after adding big collections takes longer while indexing |
| Timer runs but nothing updates | `journalctl -u tcgnecro-update`: a `git pull` conflict from local edits to tracked files, or the user isn't in the `docker` group |
