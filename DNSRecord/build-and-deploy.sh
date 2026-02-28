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

sep "Update deployment"
echo "Updating deployment/dns-operator image to $IMAGE"
kubectl -n dns-mngr set image deployment/dns-operator dns-operator="$IMAGE" --record
kubectl -n dns-mngr rollout status deployment/dns-operator

sep "Deployed"
echo "Deployed $IMAGE"

sep "Logs"
echo "Tailing logs (press Ctrl-C to stop) - showing timestamps"
kubectl -n dns-mngr logs -l app=dns-operator -f --tail=200 --timestamps
