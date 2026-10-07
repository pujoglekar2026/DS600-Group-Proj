#!/bin/bash
# Pull the latest code from GitHub and restart the app.
# Run on the EC2 instance:  sudo bash /opt/nimbus/deploy/update.sh
set -euo pipefail
cd /opt/nimbus
sudo -u nimbus git pull --ff-only
sudo -u nimbus .venv/bin/pip install -r requirements.txt
sudo -u nimbus .venv/bin/flask --app wsgi init-db
systemctl restart nimbus
sleep 2
curl -fsS http://127.0.0.1/api/health && echo && echo "Updated."
