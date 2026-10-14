# Hosting TCG Necropolis on a VPS

How to run TCG Necropolis on a small rented Linux server so friends can reach it over the
internet. About an hour the first time, plus copying ~11 GB of card images.

> **Prefer Docker?** [DOCKER.md](DOCKER.md) does the same with `docker compose`, and also
> updates the server automatically whenever you push to `main`. Steps 1–3 below (server,
> DNS, hardening) apply either way; then install Docker instead of steps 4–7. This guide
> is the no-Docker route.

What you end up with:

```
friend's browser ──HTTPS──► Caddy (port 443: certificates, password gate)
                                 │  http://127.0.0.1:8000
                                 ▼
                            tcgnecro serve  (systemd service, data in /srv/tcgnecro)
```

> **Sign-in is still a profile picker** (see [ACCOUNTS.md](ACCOUNTS.md)): without
> passwords, anyone who reaches the site can pick any profile. Until real sign-in exists,
> this guide puts a **password gate in Caddy** in front of everything (step 7), so only
> people you give a password to can reach the site at all.

## What you need

- **A VPS** with at least 2 vCPU, 2–4 GB RAM and 40 GB disk, running **Ubuntu 24.04 LTS**.
  Example: a Hetzner Cloud CX22-class server (~€4–5/month). Pick an EU location for the
  large included traffic allowance; US locations include much less.
- **A domain name** (~$10/year from any registrar). These steps use `necro.example.com`;
  substitute yours everywhere.
- **An SSH key** on your home machine (`ls ~/.ssh/id_ed25519.pub`; if missing:
  `ssh-keygen -t ed25519`).
- Your existing install at `/mnt/data1/bvogle/tcgnecro`, to copy data from.

Commands marked **[home]** run on your home machine. Everything else runs on the server.

---

## 1. Create the server

In your provider's console:

1. Create a server: **Ubuntu 24.04**, the size above, and your **SSH public key** added.
2. If the provider has a cloud firewall (Hetzner does), allow inbound **TCP 22, 80, 443**
   only.
3. Note the server's **IPv4 address** (and IPv6 if shown).

## 2. Point your domain at it

At your registrar or DNS host, add:

| Type | Name | Value |
|---|---|---|
| `A` | `necro` | the server's IPv4 |
| `AAAA` | `necro` | the server's IPv6 (optional) |

Check it from home after a few minutes: `ping necro.example.com` should show the server's
IP. Caddy can't get a certificate in step 7 until this resolves.

## 3. First login and basic hardening

**[home]**

```bash
ssh root@necro.example.com
```

On the server, create your own login and stop using root:

```bash
adduser brian                         # pick a password; you'll use it for sudo
usermod -aG sudo brian
rsync -a /root/.ssh/ /home/brian/.ssh/ && chown -R brian:brian /home/brian/.ssh
```

**[home]** In a *new* terminal, check that `ssh brian@necro.example.com` works and that
`sudo -v` accepts your password. Only then, on the server, turn off root and password
logins:

```bash
sudo tee /etc/ssh/sshd_config.d/10-hardening.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
EOF
sudo systemctl restart ssh
```

Firewall and automatic security updates:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp
sudo ufw enable
sudo apt update && sudo apt -y upgrade
sudo apt -y install unattended-upgrades
```

## 4. Install the software

```bash
sudo apt -y install python3 python3-venv git rsync
```

Ubuntu 24.04's Python is 3.12, which the app needs (3.11 or newer).

**Caddy**, from its official repository (Ubuntu's own package is older and spells some
settings differently):

```bash
sudo apt -y install debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt -y install caddy
caddy version                          # 2.8 or newer
```

## 5. Install the app

The app runs as its own unprivileged user, with code in `/opt/tcgnecro` and data in
`/srv/tcgnecro`:

```bash
sudo adduser --system --group --home /opt/tcgnecro tcgnecro
sudo mkdir -p /srv/tcgnecro && sudo chown tcgnecro:tcgnecro /srv/tcgnecro

sudo -u tcgnecro git clone https://github.com/mcwookie/tcg-necropolis.git /opt/tcgnecro/app
cd /opt/tcgnecro/app
sudo -u tcgnecro python3 -m venv .venv
sudo -u tcgnecro .venv/bin/pip install -e .
```

## 6. Copy your data

What to copy, and what to leave behind:

| From `data/` | Copy? | Why |
|---|---|---|
| `collections/` | **yes** | Card images. ~11 GB |
| `decks/`, `users.json` | **yes** | Your decks and profiles. The only copies |
| `cache/` | yes | ArkhamDB data, so the server needn't re-download it |
| `secret.key` | optional | Copying it keeps the same session key. A new one is generated if missing |
| `index.db`, `thumbs/` | **no** | Rebuilt on the server. `index.db` holds absolute paths from your home machine |

**[home]** Copy to a staging folder in your home directory on the server (the first run
takes a while; re-running only sends changes):

```bash
cd /mnt/data1/bvogle/tcgnecro/data
rsync -a --info=progress2 collections decks cache users.json secret.key \
  brian@necro.example.com:tcgnecro-data/
```

On the server, move it into place and build the index:

```bash
sudo rsync -a ~/tcgnecro-data/ /srv/tcgnecro/ && sudo chown -R tcgnecro:tcgnecro /srv/tcgnecro
cd /opt/tcgnecro/app
sudo -u tcgnecro TCGNECRO_DATA=/srv/tcgnecro .venv/bin/tcgnecro catalog
sudo -u tcgnecro TCGNECRO_DATA=/srv/tcgnecro .venv/bin/tcgnecro reindex
sudo -u tcgnecro TCGNECRO_DATA=/srv/tcgnecro .venv/bin/tcgnecro user list   # your profiles came along
rm -rf ~/tcgnecro-data
```

## 7. Run it as a service

**The app**, kept running by systemd and listening only on the server itself:

```bash
sudo tee /etc/systemd/system/tcgnecro.service <<'EOF'
[Unit]
Description=TCG Necropolis
After=network.target

[Service]
User=tcgnecro
Group=tcgnecro
WorkingDirectory=/opt/tcgnecro/app
Environment=TCGNECRO_DATA=/srv/tcgnecro
Environment=TCGNECRO_SECURE_COOKIES=1
# One process only: deck and profile writes are locked within a single process.
ExecStart=/opt/tcgnecro/app/.venv/bin/tcgnecro serve --host 127.0.0.1 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now tcgnecro
curl -sI http://127.0.0.1:8000/login | head -1     # HTTP/1.1 200 OK
```

**Caddy**, which handles HTTPS certificates automatically and adds the password gate.
Create a password hash for each person (it prompts for the password):

```bash
caddy hash-password
```

Then write `/etc/caddy/Caddyfile`, with one line per person under `basic_auth`:

```bash
sudo tee /etc/caddy/Caddyfile <<'EOF'
necro.example.com {
	encode zstd gzip

	# Password gate until the app has real sign-in. One line per person:
	#   username  <hash from `caddy hash-password`>
	basic_auth {
		brian $2a$14$REPLACE_WITH_HASH
		friend $2a$14$REPLACE_WITH_HASH
	}

	reverse_proxy 127.0.0.1:8000
}
EOF
sudo systemctl reload caddy
```

Open **https://necro.example.com**. The browser asks for the Caddy username and password,
then shows the profile picker. The certificate comes from Let's Encrypt automatically;
if the page doesn't load in the first minute, see Troubleshooting.

## 8. Check it works

- [ ] `https://necro.example.com` asks for a password, and the wrong one is refused
- [ ] After the password, the profile picker lists your profiles; your decks are there
- [ ] A Proxies preview shows thumbnails, and a PDF downloads
- [ ] `http://necro.example.com` redirects to `https://`
- [ ] `curl -I http://<server-ip>:8000` from home **fails** (the app isn't exposed directly)

Then give each friend their Caddy password, and add a profile for them on the Profile
page (or `tcgnecro user add`).

## 9. Backups

`decks/` and `users.json` are the only copies of your users' work. Two easy layers:

1. **Provider snapshots.** Hetzner's server backups cost about 20% of the server price and
   keep 7 daily images. Turn them on in the console.
2. **A nightly copy off the server.** A cron job tars the small user files into a folder
   you pull from home:

```bash
sudo tee /etc/cron.daily/tcgnecro-backup <<'EOF'
#!/bin/sh
mkdir -p /var/backups/tcgnecro
tar -czf /var/backups/tcgnecro/userdata-$(date +%F).tgz -C /srv/tcgnecro decks users.json
find /var/backups/tcgnecro -name 'userdata-*.tgz' -mtime +30 -delete
EOF
sudo chmod +x /etc/cron.daily/tcgnecro-backup
```

**[home]** Pull them now and then (or from a home cron job):
`rsync -a brian@necro.example.com:/var/backups/tcgnecro/ ~/tcgnecro-backups/`

To restore: stop the service, untar into `/srv/tcgnecro`, `chown -R tcgnecro:tcgnecro`,
and start it again. Images come back by re-running step 6's `rsync`.

## Day-to-day

**Update the app** after pushing changes to GitHub:

```bash
cd /opt/tcgnecro/app
sudo -u tcgnecro git pull
sudo -u tcgnecro .venv/bin/pip install -e .            # picks up new dependencies
sudo systemctl restart tcgnecro
```

If the change added a game or touched catalog data, also run `tcgnecro catalog` (and
`reindex` if images changed), with `sudo -u tcgnecro TCGNECRO_DATA=/srv/tcgnecro` as in
step 6.

**Add a collection:** **[home]** `rsync` the archive to the server, then
`sudo -u tcgnecro TCGNECRO_DATA=/srv/tcgnecro .venv/bin/tcgnecro import ~/new.pnx`.
For loose images, rsync them into `/srv/tcgnecro/collections/<game>/<name>/`,
`chown`, and `reindex`.

**Add a friend:** `caddy hash-password` → add a line under `basic_auth` →
`sudo systemctl reload caddy`, then add their profile in the app.

**Logs:** `journalctl -u tcgnecro -f` (app) and `journalctl -u caddy -f` (web server).

**AGPL:** the footer's "Source code" link points at your GitHub repo. Keep what the server
runs pushed there (deploying with `git pull` from `main` does that).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Browser can't connect, or certificate error in the first minutes | DNS doesn't point at the server yet, or ports 80/443 are blocked by `ufw` or the cloud firewall. `journalctl -u caddy` shows the certificate attempts |
| `502 Bad Gateway` | The app isn't running: `systemctl status tcgnecro`, `journalctl -u tcgnecro -n 50` |
| Signed out after every page | Visiting over plain `http://` with secure cookies on; use `https://` |
| Thumbnails missing, deck pages fail | `index.db` copied from home, so its paths point at the old machine. Run `reindex` |
| `Permission denied` in the logs | Files under `/srv/tcgnecro` not owned by `tcgnecro`; re-run the `chown` |
| Big PDFs slow | Expected: a whole set takes ~30 s. Compact images help |

## Later

- When real sign-in (Google/GitHub) lands, remove the `basic_auth` block. See
  [ROADMAP.md](ROADMAP.md).
- [DOCKER.md](DOCKER.md) replaces steps 4–7 with containers and adds automatic updates.
- Once you're no longer using the home server for friends, remove the home firewall rule
  for port 8765.
