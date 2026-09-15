"""In-memory dev fallback (outside the cluster): static catalog + per-tenant instance/binding lists, process-local (nothing survives a restart); simulates the broker's ~4s async completion so the dev UI exercises the real status flow."""

from datetime import datetime, timedelta, timezone
from functools import partial

_READY_AFTER = timedelta(seconds=4)
STATIC_CATALOG = [
    {
        "id": "redis",
        "name": "Redis",
        "description": "In-memory cache and message broker, operator-managed.",
        "tags": ["cache", "queue"],
        "plans": [
            {
                "id": "redis-small",
                "name": "small",
                "description": "256Mi, single replica",
            },
            {"id": "redis-large", "name": "large", "description": "1Gi, replica set"},
        ],
    },
    {
        "id": "postgresql",
        "name": "PostgreSQL",
        "description": "Relational database cluster, operator-managed.",
        "tags": ["database", "sql"],
        "plans": [
            {
                "id": "postgresql-small",
                "name": "small",
                "description": "1Gi storage, single instance",
            },
            {
                "id": "postgresql-large",
                "name": "large",
                "description": "10Gi storage, HA cluster",
            },
        ],
    },
]
_MEM = {"instances": {}, "bindings": {}}
_EVENTS = []  # simulated lifecycle events (the K8s analog: Events on the CR)


def _event(ns, kind, name, reason, message):
    _EVENTS.append(
        {
            "namespace": ns,
            "kind": kind,
            "name": name,
            "reason": reason,
            "type": "Normal",
            "message": message,
            "lastTimestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    del _EVENTS[:-200]


def events(ns, kind, name=None):
    return [
        e
        for e in _EVENTS
        if e["namespace"] == ns
        and e["kind"] == kind
        and (name is None or e["name"] == name)
    ]


def _table(kind, tenant):
    return _MEM[kind].setdefault(tenant, [])


def _aged(rec, inflight, ns, kind):
    if (
        rec.get("status") == inflight
        and datetime.now(timezone.utc) - datetime.fromisoformat(rec["createdAt"])
        > _READY_AFTER
    ):
        rec["status"] = "ready"
        _event(
            ns,
            kind,
            rec["id"],
            {"provisioning": "Provisioned", "creating": "InjectedBindCredentials"}[
                inflight
            ],
            "simulated broker finished",
        )  # Flip a record past its simulated async window to ready.
    return rec


def list_instances(t):
    return [
        _aged(x, "provisioning", t, "ServiceInstance") for x in _table("instances", t)
    ]


def get_instance(t, i):
    return next((x for x in list_instances(t) if x["id"] == i), None)


def create_instance(t, name, offering_id, plan_id):
    rec = {
        "id": name,
        "name": name,
        "resourceName": name,
        "offeringId": offering_id,
        "planId": plan_id,
        "status": "provisioning",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "message": "in-memory (server not running in cluster)",
    }
    _table("instances", t).append(rec)
    _event(
        t,
        "ServiceInstance",
        name,
        "Provisioning",
        f"provision requested from the catalog ({offering_id}/{plan_id})",
    )
    return rec


def _delete(kind, t, i):
    if not (tbl := _table(kind, t)) or not any(x["id"] == i for x in tbl):
        raise LookupError("not found")
    tbl[:] = [x for x in tbl if x["id"] != i]


delete_instance = partial(_delete, "instances")


def list_bindings(t):
    return [_aged(x, "creating", t, "ServiceBinding") for x in _table("bindings", t)]


def get_binding(t, i):
    return next((x for x in list_bindings(t) if x["id"] == i), None)


def create_binding(t, name, instance_id):
    rec = {
        "id": name,
        "name": name,
        "resourceName": name,
        "instanceId": instance_id,
        "status": "creating",
        "secretName": f"binding-{name}",
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    _table("bindings", t).append(rec)
    _event(
        t,
        "ServiceBinding",
        name,
        "Creating",
        f"bind requested against instance {instance_id}",
    )
    return rec


delete_binding = partial(_delete, "bindings")
