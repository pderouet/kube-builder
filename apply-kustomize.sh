#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Choisissez le kustomization à appliquer :"
options=("1 : cloudDbeaver" "2 : PostgreSQL" "3 : Quit")
PS3="Votre choix : "
select opt in "${options[@]}"; do
  case "$opt" in
    "1")
      KUST_DIR="$ROOT_DIR/cloudDbeaver"
      break
      ;;
    "2")
      KUST_DIR="$ROOT_DIR/PostgreSQL"
      break
      ;;
    "3")
      echo "Abandon."; exit 0
      ;;
    *) echo "Choix invalide." ;;
  esac
done

if [ ! -d "$KUST_DIR" ]; then
  echo "Dossier kustomization introuvable : $KUST_DIR" >&2
  exit 2
fi

# Detect default namespace from kustomization if present
KUST_FILE="$KUST_DIR/kustomization.yaml"
DEFAULT_NS="default"
if [ -f "$KUST_FILE" ]; then
  ns_line=$(grep -E "^\s*namespace\s*:\s*" "$KUST_FILE" || true)
  if [ -n "$ns_line" ]; then
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