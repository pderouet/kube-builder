#!/usr/bin/env python3
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
REQUEST_TIMEOUT = int(os.environ.get("REQ_TIMEOUT", "10"))
# Annotations used to trigger Service-based DNS management
ANNOTATION_PREFIX = os.environ.get("DNS_ANNOTATION_PREFIX", "dns.example.com")
ANNOTATION_DNS_NAME = ANNOTATION_PREFIX + "/dns-name"
ANNOTATION_ZONE = ANNOTATION_PREFIX + "/zone"
ANNOTATION_TTL = ANNOTATION_PREFIX + "/ttl"

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
        # FreeIPA expects form-encoded login on /ipa/session/login_password
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/plain",
            "Referer": f"{IPA_SERVER}/ipa",
        }
        r = session.post(LOGIN_PATH, data=payload, headers=headers, timeout=REQUEST_TIMEOUT, verify=False)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error("Network error during IPA login: %s", e)
        raise

    # FreeIPA login typically returns a 200 with a session cookie set (ipa_session)
    # Do not assume JSON response. Consider the login successful if the session cookie is present.
    if 'ipa_session' in session.cookies or 'ipa_session' in r.cookies:
        return True

    # Fallback: try parsing JSON and check for errors if present
    try:
        logger.debug(r.text)
        j = r.json()
    except ValueError:
        logger.error("Unexpected non-JSON login response and no session cookie: %s", r.text)
        raise RuntimeError("IPA login failed: unexpected response")

    if j.get("error"):
        logger.error("IPA login returned error: %s", j["error"])
        raise RuntimeError(f"IPA login failed: {j['error']}")
    return True

def ipa_call(session: requests.Session, method: str, params=None):
    # Build JSON-RPC payload. Accept three forms for `params`:
    # - a 2-item (positional_list, kwargs_dict) sequence -> used as-is
    # - a list -> treated as positional args list
    # - a dict -> treated as kwargs
    if isinstance(params, (list, tuple)) and len(params) == 2 and isinstance(params[0], list) and isinstance(params[1], dict):
        payload = {"method": method, "params": [params[0], params[1]]}
    elif isinstance(params, list):
        payload = {"method": method, "params": [params, {}]}
    elif isinstance(params, dict):
        payload = {"method": method, "params": [[], params or {}]}
    else:
        payload = {"method": method, "params": [[], {}]}

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
    # avoid logging credentials to prevent secret leakage
    logging.getLogger("operator").debug("credentials retrieved for IPA login")
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
    # method: dnsrecord_show expects positional args: [zone, record_name]
    return ipa_call(session, "dnsrecord_show", [[zone, name], {}])

def dnsrecord_add(session, zone, name, rec_type, value, ttl=None):
    # dnsrecord_add expects positional args [zone, record_name] and keyword options
    kw = {}
    if rec_type == "A":
        kw["a_rec"] = [value]
    elif rec_type == "CNAME":
        kw["cname_rec"] = value
    if ttl is not None:
        # FreeIPA expects TTL as option 'dnsttl' (integer)
        try:
            kw["dnsttl"] = int(ttl)
        except Exception:
            kw["dnsttl"] = int(str(ttl))
    return ipa_call(session, "dnsrecord_add", [[zone, name], kw])

def dnsrecord_del(session, zone, name, rec_type=None, value=None):
    # dnsrecord_del: positional [zone, record_name], optional kwargs
    kw = {}
    if rec_type:
        if rec_type == "A":
            kw["a_rec"] = [value] if value else []
        elif rec_type == "CNAME":
            kw["cname_rec"] = value
    return ipa_call(session, "dnsrecord_del", [[zone, name], kw])

def ensure_session():
    s = requests.Session()
    # FreeIPA expects Referer including /ipa and prefers plain text responses
    s.headers.update({"Referer": f"{IPA_SERVER}/ipa", "Accept": "text/plain"})
    username, password = get_credentials()
    ipa_login(s, username, password)
    return s

@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    # allow longer time for creating resources
    settings.posting.level = logging.INFO
    settings.scanning.disabled = False
    settings.watching.server_timeout = 60
    logger.info("Operator scope: namespace=%s", NAMESPACE)
# Note: CRD-based handlers (DNSRecord) were removed; operator now uses Service annotations only.


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


# --- Service watchers using annotations ---------------------------------


def _get_annotation_map(meta):
    return (meta.get("annotations") or {})


def _process_service_dns(ns, svc_name, annotations, logger):
    dns_name = annotations.get(ANNOTATION_DNS_NAME)
    if not dns_name:
        return
    zone = annotations.get(ANNOTATION_ZONE)
    if not zone:
        logger.error("Service %s/%s: missing annotation %s; skipping", ns, svc_name, ANNOTATION_ZONE)
        return
    ttl = annotations.get(ANNOTATION_TTL)

    ip = fetch_service_ip(ns, svc_name)
    if not ip:
        raise kopf.TemporaryError(f"Service {ns}/{svc_name} has no external IP yet", delay=15)

    record_name = normalize_name(dns_name, zone)

    try:
        session = ensure_session()
    except Exception as e:
        raise kopf.TemporaryError(f"IPA auth failed: {e}", delay=30)

    # Check existing record
    try:
        res = dnsrecord_show(session, zone, record_name)
        exists = True
    except Exception as e:
        msg = str(e)
        if "not found" in msg.lower() or "no such" in msg.lower():
            exists = False
            res = None
        else:
            raise kopf.TemporaryError(f"IPA show failed: {e}", delay=20)

    if exists:
        recs = []
        try:
            recs = res.get("result", {}).get("a_rec", []) or []
        except Exception:
            recs = []
        if ip in recs:
            logger.info("Service %s/%s: A record present %s", ns, svc_name, ip)
            return
        else:
            try:
                dnsrecord_del(session, zone, record_name)
            except Exception as e:
                raise kopf.TemporaryError(f"IPA delete during update failed: {e}", delay=20)
            try:
                dnsrecord_add(session, zone, record_name, "A", ip, ttl)
            except Exception as e:
                raise kopf.TemporaryError(f"IPA add during update failed: {e}", delay=20)
            logger.info("Service %s/%s: A record updated to %s", ns, svc_name, ip)
            return
    else:
        try:
            dnsrecord_add(session, zone, record_name, "A", ip, ttl)
        except Exception as e:
            raise kopf.TemporaryError(f"IPA add failed: {e}", delay=20)
        logger.info("Service %s/%s: A record created %s", ns, svc_name, ip)
        return


@kopf.on.create('', 'v1', 'services')
@kopf.on.update('', 'v1', 'services')
def service_create_update(body, meta, spec, namespace, logger, **kwargs):
    try:
        annotations = _get_annotation_map(meta)
        # debug log incoming event
        logger.info("Service event received: %s/%s type=%s annotations=%s", namespace, meta.get('name'), spec.get('type'), annotations)
        # only handle LoadBalancer services
        if spec.get('type') != 'LoadBalancer':
            logger.info("Service %s/%s: ignored (type != LoadBalancer)", namespace, meta.get('name'))
            return
        _process_service_dns(namespace, meta.get('name'), annotations, logger)
    except kopf.TemporaryError:
        raise
    except Exception as e:
        logger.exception("Service handler error")


@kopf.on.delete('', 'v1', 'services')
def service_delete(body, meta, spec, namespace, logger, **kwargs):
    annotations = _get_annotation_map(meta)
    logger.info("Service delete event: %s/%s annotations=%s", namespace, meta.get('name'), annotations)
    dns_name = annotations.get(ANNOTATION_DNS_NAME)
    if not dns_name:
        return
    zone = annotations.get(ANNOTATION_ZONE)
    if not zone:
        logger.error("Service %s/%s: missing annotation %s; cannot delete DNS", namespace, meta.get('name'), ANNOTATION_ZONE)
        return

    record_name = normalize_name(dns_name, zone)
    try:
        session = ensure_session()
    except Exception as e:
        raise kopf.TemporaryError(f"IPA auth failed: {e}", delay=30)

    try:
        dnsrecord_del(session, zone, record_name)
        logger.info("Service %s/%s: DNS record %s.%s deleted", namespace, meta.get('name'), record_name, zone)
    except Exception as e:
        msg = str(e)
        if "not found" in msg.lower() or "no such" in msg.lower():
            logger.info("Service %s/%s: DNS record not found (already removed)", namespace, meta.get('name'))
        else:
            raise kopf.TemporaryError(f"IPA delete failed: {e}", delay=20)

if __name__ == "__main__":
    # start health server
    t = threading.Thread(target=_serve_health, kwargs={"port": 8080}, daemon=True)
    t.start()

    # run kopf (will block)
    # use programmatic run so handlers defined in this module are used
    kopf.run()
