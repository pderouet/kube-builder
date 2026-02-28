#!/usr/bin/env bash
set -euo pipefail

# Build and deploy helper for the DNS operator
# Usage:
# IMAGE=<registry>/<user>/dns-operator:tag ./build-and-deploy.sh
# If IMAGE not set, defaults to docker.io/$(whoami)/dns-operator:local

IMAGE_DEFAULT="docker.io/kigarsk/dns-operator:0.1.0"
IMAGE=${IMAGE:-$IMAGE_DEFAULT}

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

# Clear terminal for a clean, step-separated output
clear

sep() { printf "\n===== %s =====\n\n" "$1"; }

# Colors
RED="\033[0;31m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
BLUE="\033[0;34m"
RESET="\033[0m"

color_sep() { printf "\n${BLUE}===== %s =====${RESET}\n\n" "$1"; }

sep "Build image"
echo "IMAGE = $IMAGE"

sep "Checks"
if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found in PATH; install docker or use another build tool" >&2
  exit 1
fi
# build
sep "Docker build"
docker build -t "$IMAGE" .

sep "Push image"
echo "Pushing image: $IMAGE"
# push may fail if not logged in; allow proceeding if push fails but warn
if ! docker push "$IMAGE"; then
  echo "Warning: docker push failed. If you are testing locally, you can load the image into your cluster instead." >&2
fi

color_sep "Update deployment"
echo -e "${GREEN}Updating deployment/dns-operator image to ${IMAGE}${RESET}"
kubectl -n dns-mngr set image deployment/dns-operator dns-operator="$IMAGE" --record

echo -e "${YELLOW}Attempting to delete dns-operator pods to force restart...${RESET}"
kubectl -n dns-mngr delete pods -l app=dns-operator --wait=false || true
echo -e "${YELLOW}Waiting 5s for pods to be re-created...${RESET}"
sleep 5
kubectl -n dns-mngr get pods -l app=dns-operator -o wide || true


color_sep "Deployed"
echo -e "${GREEN}Deployed ${IMAGE}${RESET}"

color_sep "Logs"
echo -e "${BLUE}Tailing logs (press Ctrl-C to stop) - showing timestamps${RESET}"
kubectl -n dns-mngr logs -l app=dns-operator -f --tail=200 --timestamps
