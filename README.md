# 𝐓𝐢𝐭𝐚𝐧𝐂𝐥𝐨𝐮𝐝™ | VPS Bot

A Discord-managed Docker VPS service with a fixed plan:

- 32 GB RAM per container
- one container per eligible Discord user; owners bypass the per-user quota
- owner access through the configured owner role or owner IDs
- self-service deployment restricted to the configured user role
- premium DM embeds and custom Discord controls

## Ubuntu setup

Docker is required in addition to Git and Python. Run the bot as a user that can access the Docker daemon.

```bash
sudo apt update
sudo apt install -y git python3-venv docker.io
sudo systemctl enable --now docker

git clone <your-modified-repository-url> /root/Private1
cd /root/Private1

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

nano .env
python3 titancloud_bot.py
```

Add your Discord bot token to `.env`. The supplied IDs are already the defaults, but keeping them in `.env` makes the deployment explicit. Never commit `.env` or share the token.

To obtain a valid token, open the Discord Developer Portal, select the application, open **Bot**, choose **Reset Token**, and copy the new token immediately. Set it in `.env` without a `Bot ` prefix:

```env
DISCORD_TOKEN=paste_the_new_token_here
```

A `401 Unauthorized` or `Improper token has been passed` error means Discord rejected that token. Reset it again, update `.env`, and restart the process.

In the Discord Developer Portal, enable the **Server Members Intent** and **Message Content Intent** for the bot. Invite it with the `bot` and `applications.commands` scopes.

## Access model

`/deploy` creates a container for the invoking member. Regular members must have role `1536277694927474720` and may own one VPS. Owners can use `/deploy target:@member` to create unlimited VPS instances for themselves or another member, subject only to the global 250-container capacity.

`/manage` automatically opens the invoking member's only VPS. When the member owns multiple VPS instances, use `/manage vps_id:<id>`. The control panel is also delivered by DM and includes Start, Stop, Restart, current-OS Reinstall, Fresh SSH, and Transfer buttons. Fresh SSH closes the old tmate process and generates a different session every time.

Owners are recognized by role `1426555185454649447` or either configured owner ID.

The RAM amount and one-container quota are intentionally hard-coded. CPU, disk, image, Docker network, and global capacity can be configured through `.env`.

## Keep the bot running

Running the bot directly ties it to your SSH terminal. Install the included systemd service so it survives logout and automatically restarts after failures:

```bash
cd /root/Private1
sudo cp titancloud-bot.service /etc/systemd/system/titancloud-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now titancloud-bot
sudo systemctl status titancloud-bot
```

Follow live logs with:

```bash
sudo journalctl -u titancloud-bot -f
```

## Push to GitHub

Create an empty repository in your GitHub account, then run these commands from the `VPS_bot` project directory. Replace the repository URL with your own.

```bash
# The upstream repository tracked .env. Remove only its Git index entry;
# the local deployment file remains on disk and is protected by .gitignore.
git rm --cached .env

git add titancloud_bot.py titancloud-bot.service requirements.txt README.md .gitignore .env.example
git commit -m "Add premium role-based VPS deployment"

git branch -M main
git remote set-url origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

If Git asks you to authenticate over HTTPS, use a GitHub personal access token or sign in with GitHub CLI. Do not force-add `.env`. If the token-shaped value in `.env.example` is ever replaced with a real token, remove it from that file before committing and rotate the exposed token immediately.
