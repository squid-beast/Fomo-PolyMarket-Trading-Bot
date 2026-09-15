#!/usr/bin/env bash
# One-time VPS hardening + setup. Run as a sudo-capable user, NOT as root.
#   bash deploy/bootstrap.sh
set -euo pipefail

echo "==> packages"
sudo apt-get update -qq
sudo apt-get install -y -qq ca-certificates curl git ufw fail2ban unattended-upgrades jq

echo "==> docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  echo "    log out and back in for docker group to apply"
fi

echo "==> firewall (deny all inbound except SSH; the bot needs no inbound ports)"
sudo ufw --force reset >/dev/null
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw --force enable

echo "==> ssh hardening"
sudo tee /etc/ssh/sshd_config.d/99-copytrader.conf >/dev/null <<'EOF'
PermitRootLogin no
PasswordAuthentication no
ChallengeResponseAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
EOF
sudo systemctl restart ssh || sudo systemctl restart sshd

echo "==> automatic security updates"
echo 'Unattended-Upgrade::Automatic-Reboot "false";' | \
  sudo tee /etc/apt/apt.conf.d/51unattended-upgrades-local >/dev/null
sudo systemctl enable --now unattended-upgrades

echo "==> fail2ban"
sudo systemctl enable --now fail2ban

echo "==> directories"
mkdir -p ~/copytrader/data ~/backups
chmod 700 ~/backups

echo
echo "DONE. Next:"
echo "  1. cd ~/copytrader && cp .env.example .env && chmod 600 .env"
echo "  2. fill in .env  (LIVE_TRADING stays false)"
echo "  3. docker compose up -d --build"
echo "  4. crontab -e  ->  add the lines in deploy/crontab.example"
