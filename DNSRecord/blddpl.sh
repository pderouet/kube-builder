set -euo pipefail

# Quick build & deploy helper for the DNS operator.
# Usage: ./blddpl.sh [-i IMAGE] [--no-push] [--build-only] [--apply-only]

IMAGE_DEFAULT="docker.io/kigarsk/dns-operator:0.1.0"
IMAGE="$IMAGE_DEFAULT"
DO_PUSH=true
BUILD_ONLY=false
APPLY_ONLY=false

while [ "$#" -gt 0 ]; do
	case "$1" in
		-i|--image)
			IMAGE="$2"; shift 2;;
		--no-push)
			DO_PUSH=false; shift;;
		--build-only)
			BUILD_ONLY=true; shift;;
		--apply-only)
			APPLY_ONLY=true; shift;;
		-h|--help)
			echo "Usage: $0 [-i IMAGE] [--no-push] [--build-only] [--apply-only]"; exit 0;;
		*)
			echo "Unknown arg: $1"; exit 1;;
	esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$APPLY_ONLY" = false ]; then
	echo "Building image: $IMAGE"
	docker build -t "$IMAGE" "$SCRIPT_DIR"
	if [ "$DO_PUSH" = true ]; then
		echo "Pushing image: $IMAGE"
		docker push "$IMAGE"
	else
		echo "Skipping image push (--no-push)"
	fi
fi

if [ "$BUILD_ONLY" = true ]; then
	echo "Build-only requested; exiting.";
	exit 0
fi

echo "Applying manifests (kustomize) from $SCRIPT_DIR/manifests"
kubectl apply -k "$SCRIPT_DIR/manifests"

# Ensure deployment uses the built image (idempotent)
echo "Updating deployment image to $IMAGE"
kubectl -n dns-mngr set image deployment/dns-operator dns-operator="$IMAGE" --record || true

echo "Deploy complete."
