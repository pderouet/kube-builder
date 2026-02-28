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

echo "Building image: $IMAGE"
if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found in PATH; install docker or use another build tool" >&2
  exit 1
fi

docker build -t "$IMAGE" .

echo "Pushing image: $IMAGE"
# push may fail if not logged in; allow proceeding if push fails but warn
if ! docker push "$IMAGE"; then
  echo "Warning: docker push failed. If you are testing locally, you can load the image into your cluster instead." >&2
fi

# Update deployment image
echo "Updating deployment/dns-operator image to $IMAGE"
kubectl -n dns-mngr set image deployment/dns-operator dns-operator="$IMAGE" --record
kubectl -n dns-mngr rollout status deployment/dns-operator

echo "Deployed $IMAGE"

echo "Tail logs (press Ctrl-C to stop):"
kubectl -n dns-mngr logs -l app=dns-operator -f --tail=100
