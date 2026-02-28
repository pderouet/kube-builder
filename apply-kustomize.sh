#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Choisissez le kustomization à appliquer :"
options=("cloudDbeaver" "PostgreSQL" "Quit")
PS3="Votre choix : "
select opt in "${options[@]}"; do
  if [ -z "$opt" ]; then
    echo "Choix invalide.";
    continue;
  fi
  case "$opt" in
    cloudDbeaver)
      KUST_DIR="$ROOT_DIR/cloudDbeaver"
      break
      ;;
    PostgreSQL)
      KUST_DIR="$ROOT_DIR/PostgreSQL"
      break
      ;;
    Quit)
      echo "Abandon."; exit 0
      ;;
    *) echo "Choix invalide." ;;
  esac
done
else
  # Fallback: edit kustomization.yaml namespace line or add it
  if [ -f "$TMPDIR/kustomization.yaml" ]; then
    if grep -q -E "^\s*namespace\s*:\s*" "$TMPDIR/kustomization.yaml"; then
      sed -i -E "s/^(\s*namespace\s*:\s*).*/\1$TARGET_NS/" "$TMPDIR/kustomization.yaml"
    else
      # insert namespace as second line to be safe
      awk -v ns="$TARGET_NS" 'NR==1{print; print "namespace: " ns; next} {print}' "$TMPDIR/kustomization.yaml" > "$TMPDIR/kustomization.yaml.tmp" && mv "$TMPDIR/kustomization.yaml.tmp" "$TMPDIR/kustomization.yaml"
    fi
  else
    # if there's no kustomization.yaml, create a minimal one that sets namespace and will let kubectl apply the files
    cat > "$TMPDIR/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: $TARGET_NS
resources:
  # resources are the files in this directory
EOF
    # append all yaml files found in the dir to the resources list (skip kustomization itself)
    for f in "$TMPDIR"/*.yaml; do
      [ "$(basename "$f")" = "kustomization.yaml" ] && continue
      echo "  - $(basename "$f")" >> "$TMPDIR/kustomization.yaml"
    done
  fi
DEFAULT_NS="default"
if [ -f "$KUST_FILE" ]; then
  ns_line=$(grep -E "^\s*namespace\s*:\s*" "$KUST_FILE" || true)
kubectl apply -k "$TMPDIR"
    DEFAULT_NS=$(echo "$ns_line" | awk -F: '{print $2}' | xargs)
  fi
fi

read -rp "Namespace cible [${DEFAULT_NS}]: " TARGET_NS
TARGET_NS=${TARGET_NS:-$DEFAULT_NS}

read -rp "Confirmer application de $KUST_DIR sur namespace '$TARGET_NS' ? (y/N) " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
  echo "Annulé."; exit 0
fi

# Build in a temporary copy to avoid modifying repo files
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT
cp -a "$KUST_DIR/." "$TMPDIR/"

# Prefer kustomize edit set namespace if available
if command -v kustomize >/dev/null 2>&1; then
  (cd "$TMPDIR" && kustomize edit set namespace "$TARGET_NS") || true
else
  # Fallback: edit kustomization.yaml namespace line or add it
  if [ -f "$TMPDIR/kustomization.yaml" ]; then
    if grep -q -E "^\s*namespace\s*:\s*" "$TMPDIR/kustomization.yaml"; then
      sed -i -E "s/^(\s*namespace\s*:\s*).*/\1$TARGET_NS/" "$TMPDIR/kustomization.yaml"
    else
      # insert namespace as second line to be safe
      awk 'NR==1{print;print "namespace: '