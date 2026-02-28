# operator.py
import os
import time
import base64
import json
import logging
import requests
import kopf
import kubernetes
import urllib3
from kubernetes.client.rest import ApiException


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuration via env
IPA_SERVER = os.environ.get("IPA_SERVER", "https://ipa.example.com")
IPA_JSONRPC = f"{IPA_SERVER}/ipa/session/json"
LOGIN_PATH = f"{IPA_SERVER}/ipa/session/login_password"
CREDENTIALS_SECRET = os.environ.get("IPA_CRED_SECRET", "freeipa-credentials")
NAMESPACE = os.environ.get("OP_NS", "default")
FINALIZER = "dnsrecord.finalizers.example.com"
REQUEST_TIMEOUT = int(os.environ.get("REQ_TIMEOUT", "10"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

kubernetes.config.load_incluster_config()
corev1 = kubernetes.client.CoreV1Api()

import json
import logging
import requests

logger = logging.getLogger("operator")

# constants expected to exist in your module:
# IPA_JSONRPC = "https://lipar1.axiome-it.lan/ipa/session/json"
# REQUEST_TIMEOUT = 15

def ipa_login(session: requests.Session, username: str, password: str) -> bool:
    payload = {"user": username, "password": password}
    try:
        r = session.post(LOGIN_PATH, json=payload, timeout=REQUEST_TIMEOUT, verify=False)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error("Network error during IPA login: %s", e)
        raise

    try:
        j = r.json()
    except ValueError:
        logger.error("Invalid JSON response from IPA login: %s", r.text)
        raise RuntimeError("Invalid JSON response from IPA")

    if j.get("error"):
        logger.error("IPA login returned error: %s", j["error"])
        raise RuntimeError(f"IPA login failed: {j['error']}")
    return True

def ipa_call(session: requests.Session, method: str, params=None):
    payload = {"method": method, "params": [params or [], {}]}
    try:
        r = session.post(IPA_JSONRPC, json=payload, timeout=REQUEST_TIMEOUT, verify=False)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error("Network error during IPA call %s: %s", method, e)
        raise

    try:
        j = r.json()
    except ValueError:
        logger.error("Invalid JSON response from IPA call %s: %s", method, r.text)
        raise RuntimeError("Invalid JSON response from IPA")

    if j.get("error"):
        logger.error("IPA call %s returned error: %s", method, j["error"])
        raise RuntimeError(json.dumps(j["error"]))
    return j.get("result")

def get_credentials():
    try:
        sec = corev1.read_namespaced_secret(CREDENTIALS_SECRET, NAMESPACE)
    except ApiException as e:
        raise RuntimeError(f"Cannot read credentials secret {CREDENTIALS_SECRET}/{NAMESPACE}: {e}")
    data = sec.data or {}
    if "username" not in data or "password" not in data:
        raise RuntimeError("Secret must contain 'username' and 'password' keys (base64 encoded).")
    username = base64.b64decode(data["username"]).decode()
    password = base64.b64decode(data["password"]).decode()
    logger = logging.getLogger("operator")
    logger.error(username + " " + password)
    return username, password

def fetch_service_ip(namespace, name):
    try:
        svc = corev1.read_namespaced_service(name, namespace)
    except ApiException as e:
        if e.status == 404:
            return None
        raise
    ing = getattr(svc.status, "load_balancer") and getattr(svc.status.load_balancer, "ingress", None)
    if not ing:
        return None
    # take first ingress entry IP (or hostname not supported here)
    first = ing[0]
    ip = getattr(first, "ip", None) or getattr(first, "hostname", None)
    return ip

def normalize_name(spec_name, zone):
    name = spec_name.rstrip(".")
    zone = zone.rstrip(".")
    if name.endswith(zone):
        # if fully qualified with zone, return relative name
        rel = name[: -len(zone)].rstrip(".")
        return rel or "@"
    return name

def dnsrecord_show(session, zone, name):
    # method: dnsrecord_show
    params = {"id": zone, "record_name": name}
    return ipa_call(session, "dnsrecord_show", params)

def dnsrecord_add(session, zone, name, rec_type, value, ttl=None):
    params = {"id": zone, "record_name": name}
    if rec_type == "A":
        params["a_rec"] = [value]
    elif rec_type == "CNAME":
        params["cname_rec"] = value
    if ttl:
        params["ttl"] = str(ttl)
    return ipa_call(session, "dnsrecord_add", params)

def dnsrecord_del(session, zone, name, rec_type=None, value=None):
    params = {"id": zone, "record_name": name}
    # ipa dnsrecord_del typically deletes the whole record; server-side may require specifics
    return ipa_call(session, "dnsrecord_del", params)

def ensure_session():
    s = requests.Session()
    s.headers.update({"Referer": IPA_SERVER})
    username, password = get_credentials()
    ipa_login(s, username, password)
    return s

@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    # allow longer time for creating resources
    settings.posting.level = logging.INFO
    settings.scanning.disabled = False
    settings.watching.server_timeout = 60

@kopf.on.create('example.com', 'v1', 'dnsrecords')
@kopf.on.update('example.com', 'v1', 'dnsrecords')
def reconcile(spec, meta, status, namespace, logger, **kwargs):
    # Main reconcile logic for create/update
    name = spec.get("name")
    zone = spec.get("zone")
    rec_type = spec.get("type", "A")
    ttl = spec.get("ttl")
    svc_ns = spec.get("serviceNamespace", namespace)
    svc_name = spec.get("serviceName")

    # Add finalizer if not present
    if FINALIZER not in meta.get("finalizers", []):
        kubernetes.client.CustomObjectsApi().patch_namespaced_custom_object(
            group="example.com", version="v1", namespace=namespace, plural="dnsrecords",
            name=meta["name"],
            body={"metadata": {"finalizers": (meta.get("finalizers") or []) + [FINALIZER]}}
        )
        raise kopf.PermanentError("Finalizer added; requeueing")

    # Only A records supported for MetalLB-derived IPs
    if rec_type != "A":
        raise kopf.PermanentError("This operator supports only type: A when using service IP from MetalLB.")

    # Resolve IP from Service
    ip = fetch_service_ip(svc_ns, svc_name)
    if not ip:
        # Will requeue with backoff
        raise kopf.TemporaryError(f"Service {svc_ns}/{svc_name} has no external IP yet", delay=15)

    # Normalize name relative to zone for FreeIPA JSON-RPC (record_name)
    record_name = normalize_name(name, zone)

    # Authenticate to IPA
    try:
        session = ensure_session()
    except Exception as e:
        raise kopf.TemporaryError(f"IPA auth failed: {e}", delay=30)

    # Check existing record
    try:
        res = dnsrecord_show(session, zone, record_name)
        exists = True
    except Exception as e:
        # inspect message to detect not found vs other errors
        msg = str(e)
        if "not found" in msg.lower() or "no such" in msg.lower():
            exists = False
            res = None
        else:
            raise kopf.TemporaryError(f"IPA show failed: {e}", delay=20)

    # Compare and act
    if exists:
        # try to discover A records in result
        # result structure may vary; attempt to read 'result' keys
        recs = []
        try:
            recs = res.get("result", {}).get("a_rec", []) or []
        except Exception:
            recs = []
        if ip in recs:
            # nothing to do
            patch_status(namespace, meta["name"], {"phase": "Created", "message": f"A record present {ip}", "observedGeneration": meta.get("generation", 0)})
            return
        else:
            # Update: delete then add (atomicity caveat)
            try:
                dnsrecord_del(session, zone, record_name)
            except Exception as e:
                raise kopf.TemporaryError(f"IPA delete during update failed: {e}", delay=20)
            try:
                dnsrecord_add(session, zone, record_name, "A", ip, ttl)
            except Exception as e:
                raise kopf.TemporaryError(f"IPA add during update failed: {e}", delay=20)
            patch_status(namespace, meta["name"], {"phase": "Created", "message": f"A record updated to {ip}", "observedGeneration": meta.get("generation", 0)})
            return
    else:
        # Create
        try:
            dnsrecord_add(session, zone, record_name, "A", ip, ttl)
        except Exception as e:
            raise kopf.TemporaryError(f"IPA add failed: {e}", delay=20)
        patch_status(namespace, meta["name"], {"phase": "Created", "message": f"A record created {ip}", "observedGeneration": meta.get("generation", 0)})
        return

@kopf.on.delete('example.com', 'v1', 'dnsrecords')
def on_delete(spec, meta, namespace, logger, **kwargs):
    # Deletion handler triggered after deletionTimestamp; finalizer removal handled here
    name = spec.get("name")
    zone = spec.get("zone")
    rec_type = spec.get("type", "A")

    if rec_type != "A":
        # best-effort: try to remove generic record
        pass

    record_name = normalize_name(name, zone)

    try:
        session = ensure_session()
    except Exception as e:
        raise kopf.TemporaryError(f"IPA auth failed: {e}", delay=30)

    try:
        dnsrecord_del(session, zone, record_name)
    except Exception as e:
        # If not found, consider success; otherwise requeue
        msg = str(e)
        if "not found" in msg.lower() or "no such" in msg.lower():
            pass
        else:
            raise kopf.TemporaryError(f"IPA delete failed: {e}", delay=20)

    # Remove finalizer
    finalizers = meta.get("finalizers", []) or []
    if FINALIZER in finalizers:
        finalizers.remove(FINALIZER)
        kubernetes.client.CustomObjectsApi().patch_namespaced_custom_object(
            group="example.com", version="v1", namespace=namespace, plural="dnsrecords",
            name=meta["name"],
            body={"metadata": {"finalizers": finalizers}}
        )

def patch_status(namespace, name, status_dict):
    api = kubernetes.client.CustomObjectsApi()
    body = {"status": status_dict}
    try:
        api.patch_namespaced_custom_object_status(group="example.com", version="v1", namespace=namespace, plural="dnsrecords", name=name, body=body)
    except ApiException as e:
        logger = logging.getLogger("operator")
        logger.error(f"Failed to patch status: {e}")


# health server + kopf programmatic start
import threading
import http.server
import socketserver

class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, format, *args):
        return  # silence default logging

def _serve_health(port=8080):
    with socketserver.TCPServer(("0.0.0.0", port), _HealthHandler) as httpd:
        httpd.serve_forever()

if __name__ == "__main__":
    # start health server
    t = threading.Thread(target=_serve_health, kwargs={"port": 8080}, daemon=True)
    t.start()

    # run kopf (will block)
    # use programmatic run so handlers defined in this module are used
    kopf.run()

