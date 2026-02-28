#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Choisissez le kustomization à appliquer :"
options=("cloudDbeaver" "PostgreSQL" "Quit")
PS3="Votre choix : "
select opt in "${options[@]}"; do
  if [ -z "${opt:-}" ]; then
    echo "Choix invalide.";
    continue
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

if [ -z "${KUST_DIR:-}" ] || [ ! -d "$KUST_DIR" ]; then
  echo "Dossier kustomization introuvable : ${KUST_DIR:-<none>}" >&2
  exit 2
fi

# Detect namespaces via kubectl (present choices + create option)
KUST_FILE="$KUST_DIR/kustomization.yaml"
if command -v kubectl >/dev/null 2>&1; then
  mapfile -t NS_LIST < <(kubectl get ns -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || echo default)
else
  NS_LIST=(default)
fi

options_ns=("${NS_LIST[@]}" "Create new namespace" "Cancel")
echo "Choisissez un namespace existant ou créez-en un :"
PS3="Votre choix (num): "
select ns_opt in "${options_ns[@]}"; do
  if [ -z "${ns_opt:-}" ]; then
    echo "Choix invalide."; continue
  fi
  if [ "$ns_opt" = "Create new namespace" ]; then
    read -rp "Nom du namespace à créer: " NEW_NS
    if [ -z "$NEW_NS" ]; then echo "Nom vide."; continue; fi
    if command -v kubectl >/dev/null 2>&1; then
      kubectl create namespace "$NEW_NS" || { echo "Échec création du namespace" >&2; exit 1; }
      TARGET_NS="$NEW_NS"
      break
    else
      echo "kubectl introuvable, impossible de créer le namespace."; continue
    fi
  elif [ "$ns_opt" = "Cancel" ]; then
    echo "Annulé."; exit 0
  else
    TARGET_NS="$ns_opt"
    break
  fi
done

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
      awk -v ns="$TARGET_NS" 'NR==1{print; print "namespace: " ns; next} {print}' "$TMPDIR/kustomization.yaml" > "$TMPDIR/kustomization.yaml.tmp" && mv "$TMPDIR/kustomization.yaml.tmp" "$TMPDIR/kustomization.yaml"
    fi
  else
    # if there's no kustomization.yaml, create a minimal one that sets namespace and lists resources
    cat > "$TMPDIR/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: $TARGET_NS
resources:
EOF
    for f in "$TMPDIR"/*.yaml; do
      [ "$(basename "$f")" = "kustomization.yaml" ] && continue
      echo "  - $(basename "$f")" >> "$TMPDIR/kustomization.yaml"
    done
  fi
fi

echo "Applying kustomize from $TMPDIR (namespace=$TARGET_NS) ..."
kubectl apply -k "$TMPDIR"

echo "Done."
