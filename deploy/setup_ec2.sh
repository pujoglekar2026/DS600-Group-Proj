#!/bin/bash
# Installs and starts Nimbus RM on an Amazon Linux 2023 EC2 instance.
#
# Expects the repository at /opt/nimbus with a filled-in /opt/nimbus/.env.
# Run as root:  sudo bash /opt/nimbus/deploy/setup_ec2.sh
# Safe to run again (e.g. after changing .env).
set -euo pipefail

APP_DIR=/opt/nimbus
APP_USER=nimbus

echo "==> Installing system packages"
dnf install -y python3.11 python3.11-pip nginx mariadb105

echo "==> Creating service user"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /sbin/nologin "$APP_USER"

if [ ! -f "$APP_DIR/.env" ]; then
  echo "ERROR: $APP_DIR/.env not found. Copy .env.example to .env and fill it in first." >&2
  exit 1
fi

echo "==> Creating Python virtual environment"
python3.11 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"
chmod 755 "$APP_DIR"

echo "==> Waiting for the database and creating tables"
for i in $(seq 1 30); do
  if sudo -u "$APP_USER" bash -c "cd $APP_DIR && .venv/bin/flask --app wsgi init-db"; then
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "ERROR: could not reach the database. Check DB_HOST in .env and the security groups." >&2
    exit 1
  fi
  echo "   database not ready yet (attempt $i/30), retrying in 10s"
  sleep 10
done

echo "==> Installing systemd service"
cp "$APP_DIR/deploy/nimbus.service" /etc/systemd/system/nimbus.service
systemctl daemon-reload
systemctl enable nimbus
systemctl restart nimbus

echo "==> Configuring Nginx"
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/nginx.conf
nginx -t
systemctl enable nginx
systemctl restart nginx

echo "==> Health check"
for i in $(seq 1 15); do
  if curl -fsS http://127.0.0.1/api/health; then
    echo
    echo "Nimbus RM is running."
    exit 0
  fi
  sleep 2
done
echo "ERROR: app did not become healthy. See: journalctl -u nimbus -n 100" >&2
exit 1
