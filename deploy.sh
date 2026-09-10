#!/bin/bash
# One-click deployment script for 24/7 Cloud Hosting (Ubuntu/Debian VPS)
set -e

echo "========================================================"
echo " Setting up Delta RWA 24/7 Autonomous Trading Engine"
echo "========================================================"

# Update and install Docker if not installed
if ! [ -x "$(command -v docker)" ]; then
  echo "[*] Installing Docker..."
  curl -fsSL https://get.docker.com -o get-docker.sh
  sh get-docker.sh
  rm get-docker.sh
fi

if ! [ -x "$(command -v docker-compose)" ] && ! docker compose version > /dev/null 2>&1; then
  echo "[*] Installing Docker Compose..."
  apt-get update && apt-get install -y docker-compose-plugin
fi

echo "[*] Launching container with auto-restart..."
docker compose down || true
docker compose up -d --build

echo "========================================================"
echo " [SUCCESS] System is now running 24/7 in the cloud!"
echo " Web Dashboard: http://$(curl -s ifconfig.me):8000"
echo " Check logs with: docker compose logs -f"
echo "========================================================"
