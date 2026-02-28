DNS Operator - manifests and usage
=================================

This folder contains Kubernetes manifests to install the DNS operator that syncs Service LoadBalancer IPs into FreeIPA DNS.

This deployment is configured for the Service-annotation mode: the operator watches `Service` objects annotated with `dns.example.com/*` and creates/updates DNS records accordingly. The earlier `DNSRecord` CRD manifests have been removed; if you still need CRD-driven usage keep or restore the CRD files.

Annotations
- `dns.example.com/dns-name`: Fully-qualified DNS name to create (e.g. `ingress.example.lan.`)
- `dns.example.com/zone`: Zone (e.g. `example.lan.`)
- `dns.example.com/ttl`: TTL (seconds)

Deploy with kustomize (will create namespace `dns-mngr` and resources listed in `manifests/kustomization.yaml`):

```bash
kubectl apply -k DNSRecord/manifests
```

Secrets
Create `freeipa-credentials` in namespace `dns-mngr` with base64-encoded `username` and `password`, or use the provided `secret.yaml` example.

Example
Use `manifests/service-annotated.yaml` as an example LoadBalancer Service annotated for automatic DNS management.
