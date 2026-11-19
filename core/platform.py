"""Platform backend: talks to svccat-platform's REST API instead of K8s
Service Catalog CRs. Activated when PLATFORM_URL is set; provides the same
verbs the views expect from kube.py, translated through HTTP."""

import json
import logging
import os

import requests

logger = logging.getLogger(__name__)

# the platform base URL (e.g. http://127.0.0.1:8090); None = not configured
PLATFORM_URL = os.environ.get("PLATFORM_URL")

# token auth for the platform API
TOKEN = os.environ.get("PLATFORM_TOKEN", "")


def _headers():
    h = {"Content-Type": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Token {TOKEN}"
    return h


def _get(path, params=None):
    resp = requests.get(
        f"{PLATFORM_URL}{path}", headers=_headers(), params=params, timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def _post(path, body):
    resp = requests.post(
        f"{PLATFORM_URL}{path}", headers=_headers(), json=body, timeout=60
    )
    if resp.status_code not in (200, 201, 202):
        raise RuntimeError(f"platform {path}: {resp.status_code} {resp.text[:200]}")
    return resp.json()


def _patch(path, body):
    resp = requests.patch(
        f"{PLATFORM_URL}{path}", headers=_headers(), json=body, timeout=60
    )
    if resp.status_code not in (200, 201, 202):
        raise RuntimeError(f"platform {path}: {resp.status_code} {resp.text[:200]}")
    return resp.json()


def _delete(path):
    resp = requests.delete(f"{PLATFORM_URL}{path}", headers=_headers(), timeout=60
    )
    if resp.status_code not in (200, 202, 204):
        raise RuntimeError(f"platform {path}: {resp.status_code} {resp.text[:200]}")


def _tenant_id(name):
    """Find or create the platform tenant matching a UI tenant name."""
    tenants = _get("/api/v1/tenants/", {"name": name})
    for t in tenants:
        if t["name"] == name:
            return t["id"]
    created = _post("/api/v1/tenants/", {"name": name})
    return created["id"]


# --- catalog (maps to the offering shapes the views expect) ---


def offerings(tenant):
    """Fetch classes + plans from the platform, shaped like kube.py's
    catalog_from_crs output."""
    classes = _get("/api/v1/classes/")
    out = []
    for cls in classes:
        out.append(
            {
                # use the class/plan names as IDs — svccat-ui's input_schema
                # keys offerings by external name ("redis", "postgresql"),
                # and the plan select submits the plan name ("small")
                "id": cls["name"],
                "name": cls["name"],
                "description": cls.get("description", ""),
                "tags": cls.get("tags", []),
                "namespaced": False,
                "plans": [
                    {
                        "id": p["name"],
                        "name": p["name"],
                        "description": p.get("description", ""),
                    }
                    for p in cls.get("plans", [])
                ],
                # TODO: when the platform carries input schemas per class,
                # pass them through; for now they are empty (the drawer
                # renders no typed fields)
                "inputSchema": {},
                "bindInputSchema": {},
            }
        )
    return out


# --- instances (maps to the record shapes the views expect) ---


def instances(tenant):
    """Fetch instances from the platform, shaped like instance_record()."""
    tid = _tenant_id(tenant)
    items = _get("/api/v1/instances/", {"tenant": tid})
    out = []
    for inst in items:
        out.append(
            {
                "id": str(inst["id"]),
                "name": inst["name"],
                "resourceName": inst["name"],
                "parameters": inst.get("parameters") or None,
                "offeringId": inst.get("service_class_name", str(inst["service_class"])),
                "planId": inst.get("service_plan_name", str(inst["service_plan"])),
                "status": inst["status"],
                "createdAt": inst.get("created_at", ""),
                "reason": "",
                "message": inst.get("error_message", ""),
                "dashboardUrl": inst.get("dashboard_url", ""),
            }
        )
    return out


def find_instance(tenant, inst_id):
    """Find one instance by ID."""
    for inst in instances(tenant):
        if inst["id"] == str(inst_id):
            return inst
    return None


def create_instance(tenant, name, class_name, plan_name, params, display_name=None):
    """POST a new instance to the platform (names are the class/plan
    external names, matching what offerings() returns as IDs)."""
    tid = _tenant_id(tenant)
    # resolve names to the platform's numeric FK IDs
    classes = _get("/api/v1/classes/")
    cls = next(c for c in classes if c["name"] == class_name)
    plan = next(p for p in cls["plans"] if p["name"] == plan_name)
    body = {
        "tenant": tid,
        "service_class": cls["id"],
        "service_plan": plan["id"],
        "name": name,
        "parameters": params or {},
    }
    result = _post("/api/v1/instances/", body)
    return result


def update_instance(tenant, inst_id, plan_name, params):
    """PATCH an existing instance (plan change or parameter update)."""
    classes = _get("/api/v1/classes/")
    for cls in classes:
        for p in cls["plans"]:
            if p["name"] == plan_name:
                result = _patch(f"/api/v1/instances/{inst_id}/", {
                    "service_plan": p["id"],
                    "parameters": params or {},
                })
                return result
    raise RuntimeError(f"plan {plan_name!r} not found in any class")


def delete_instance(tenant, inst_id):
    """DELETE an instance from the platform."""
    _delete(f"/api/v1/instances/{inst_id}/")


# --- bindings ---


def bindings(tenant):
    """Fetch bindings from the platform, shaped like binding_record()."""
    tid = _tenant_id(tenant)
    items = _get("/api/v1/bindings/", {"tenant": tid})
    out = []
    for b in items:
        out.append(
            {
                "id": str(b["id"]),
                "name": b["name"],
                "resourceName": b["name"],
                "instanceId": str(b["service_instance"]),
                "status": b["status"],
                "secretName": f"binding-{b['name']}",
                "createdAt": b.get("created_at", ""),
                "reason": "",
                "message": "",
            }
        )
    return out


def create_binding(tenant, name, instance_id, display_name=None, bind_params=None):
    """POST a new binding to the platform."""
    tid = _tenant_id(tenant)
    inst = find_instance(tenant, instance_id)
    body = {
        "tenant": tid,
        "service_instance": int(instance_id),
        "name": name,
    }
    if bind_params:
        body["parameters"] = bind_params
    result = _post("/api/v1/bindings/", body)
    return result


def delete_binding(tenant, binding_id):
    """DELETE a binding from the platform."""
    _delete(f"/api/v1/bindings/{binding_id}/")


# --- credentials ---


def read_credentials(tenant, binding_id):
    """Reveal the binding's stored credentials."""
    resp = requests.get(
        f"{PLATFORM_URL}/api/v1/bindings/{binding_id}/credentials/",
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("credentials", {})


# --- events (the platform doesn't have a separate events store yet;
#     return the instance's error message as the only "event") ---


def events(tenant, kind, name=None):
    """Minimal event shape: the platform's error_message surfaces as the
    failure event."""
    return []
