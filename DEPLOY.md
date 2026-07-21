# Deploying MirrorBot to an always-on cloud server

This runs the bot 24/7 without your laptop. End result: a `systemd` service that
starts on boot, restarts itself if it crashes, and logs to `journalctl`. The bot
only makes **outbound** calls to Lichess, so no inbound ports are needed.

Estimated cost: a $4–6/month small VM, or a free-tier instance (e.g. Oracle
Cloud Always Free, Google Cloud e2-micro).

---

## 1. Get a VM

Any provider works (DigitalOcean, Hetzner, Vultr, Linode, AWS Lightsail, Oracle
free tier). Pick:

- **Ubuntu 24.04 LTS**
- **1 vCPU / 1 GB RAM** is enough for blitz/rapid. More vCPUs = a stronger engine
  (you can raise `Threads`), which helps it mirror sharp opponents.

SSH in:

```bash
ssh ubuntu@YOUR_SERVER_IP
```

## 2. Install dependencies

```bash
sudo apt update
sudo apt install -y stockfish python3-venv python3-pip git
```

On Ubuntu, Stockfish lands at `/usr/games/stockfish` — the service file below
points the bot at it explicitly.

## 3. Get the code onto the server

Either push the project to a **private** Git repo and clone it:

```bash
git clone git@github.com:YOU/mirror-chess-bot.git ~/mirror-chess-bot
```

…or copy it straight from your Mac with `scp` (run this **on your Mac**):

```bash
scp -r /Users/shlokagrawal/Claude/Projects/mirror-chess-bot \
    ubuntu@YOUR_SERVER_IP:~/mirror-chess-bot
```

## 4. Create the virtualenv

```bash
cd ~/mirror-chess-bot
python3 -m venv .venv
./.venv/bin/pip install -U pip
./.venv/bin/pip install -r requirements.txt
```

## 5. Store the token securely (not in the repo)

Put the token in a root-owned, `600`-permission env file that only the service
reads. **Do not** commit it and **do not** put it in the service file itself.

```bash
sudo tee /etc/mirrorbot.env >/dev/null <<'EOF'
LICHESS_TOKEN=lip_PUT_YOUR_TOKEN_HERE
EOF
sudo chmod 600 /etc/mirrorbot.env
```

## 6. Create the systemd service

```bash
sudo tee /etc/systemd/system/mirrorbot.service >/dev/null <<EOF
[Unit]
Description=MirrorBot Lichess bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$HOME/mirror-chess-bot
Environment=STOCKFISH_PATH=/usr/games/stockfish
EnvironmentFile=/etc/mirrorbot.env
ExecStart=$HOME/mirror-chess-bot/.venv/bin/python -u src/lichess_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

## 7. Start it and watch the logs

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mirrorbot
sudo systemctl status mirrorbot        # should say active (running)
journalctl -u mirrorbot -f             # live logs (Ctrl-C to stop watching)
```

You should see `authenticated as <username>` and, once challenged,
`play … | me X.X vs opp X.X`.

---

## Day-to-day

| Task | Command |
|------|---------|
| Live logs | `journalctl -u mirrorbot -f` |
| Restart | `sudo systemctl restart mirrorbot` |
| Stop | `sudo systemctl stop mirrorbot` |
| Update code | `git pull` (or re-`scp`), then `sudo systemctl restart mirrorbot` |
| Rotate token | edit `/etc/mirrorbot.env`, then `sudo systemctl restart mirrorbot` |

## Security notes

- The token lives only in `/etc/mirrorbot.env` (`chmod 600`). If it ever leaks,
  revoke it at <https://lichess.org/account/oauth/token> and update the file.
- Run as a **non-root** user (the service file uses your login user, not root).
- No inbound firewall rules are required. If you enable `ufw`, just
  `sudo ufw allow OpenSSH` and leave everything else denied — the bot only makes
  outbound HTTPS calls.
- To make the engine stronger on a multi-core VM, bump `Threads`/`Hash` in
  `src/engine.py` (`Engine(threads=…, hash_mb=…)`).
