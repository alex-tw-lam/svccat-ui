"""Kubernetes backend via Service Catalog CRs (Dryic fork) and the official
python client; when no cluster is configured kube is None and the views fall
back to the in-memory dev store (core/memory)."""

import hashlib
import os
import re
from functools import partial
from kubernetes import client as k8s_client, config as k8s_config
from kubernetes.client.exceptions import ApiException

GROUP, VERSION = "servicecatalog.k8s.io", "v1beta1"
_in_cluster = (
    os.environ.get("KUBERNETES_SERVICE_HOST") is not None
    or os.environ.get("KUBE_IN_CLUSTER") == "true"
)


def _connect():
    # in a pod: the mounted service-account config; on a dev box
    # (KUBE_IN_CLUSTER=true): the current kubectl context, so both backends
    # can be exercised against a real cluster
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
        # loud on purpose: a dev box silently hitting a prod context is a
        # real trap — name the context so the log answers "which cluster?"
        import logging

        _, ctx = k8s_config.list_kube_config_contexts()
        logging.getLogger(__name__).warning(
            "in-cluster config unavailable — using kubeconfig context %r",
            ctx["name"],
        )
    return {
        "custom": k8s_client.CustomObjectsApi(),
        "core": k8s_client.CoreV1Api(),
        "rbac": k8s_client.RbacAuthorizationV1Api(),
    }


kube = _connect() if _in_cluster else None


def _pod_namespace():
    try:
        return (
            open("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
            .read()
            .strip()
        )
    except OSError:
        return os.environ.get("POD_NAMESPACE", "service-manager")


def _ensure(
    read, create
):  # read-then-create-on-404; any other API error escalates unchanged
    try:
        read()
    except ApiException as err:
        if (getattr(err, "status", None) or 0) != 404:
            raise
        create()


def ensure_namespace(ns):
    _ensure(
        partial(kube["core"].read_namespace, ns),
        partial(kube["core"].create_namespace, {"metadata": {"name": ns}}),
    )
    binding = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {"name": "service-manager-binding-secrets", "namespace": ns},
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": "service-manager-binding-secret-reader",
        },
        "subjects": [
            {
                "kind": "ServiceAccount",
                "name": "service-manager",
                "namespace": _pod_namespace(),
            }
        ],
    }
    _ensure(
        partial(
            kube["rbac"].read_namespaced_role_binding, binding["metadata"]["name"], ns
        ),
        partial(kube["rbac"].create_namespaced_role_binding, ns, binding),
    )  # SA secret-read in the tenant ns only (binding creds live there); RoleBinding -> shared ClusterRole (the escalation guard rejects Roles the SA lacks); the SA lives in this deployment's namespace (pod serviceaccount token)


def read_secret(ns, name):
    return kube["core"].read_namespaced_secret(name, ns)


def _event_ts(e):
    # events carry up to three timestamp fields; the controller fills
    # whichever matches the event source
    ts = e.last_timestamp or e.first_timestamp or e.event_time
    return ts.isoformat() if ts else ""


def list_events(ns):
    """Service Catalog controller events (per instance and binding) in the ns."""
    out = []
    for e in kube["core"].list_namespaced_event(ns).items or []:
        o = e.involved_object
        out.append(
            {
                "kind": o.kind if o else "",
                "name": o.name if o else "",
                "reason": e.reason or "",
                "type": e.type or "Normal",
                "message": e.message or "",
                "lastTimestamp": _event_ts(e),
            }
        )
    return out


def tenant_namespaces():
    return [
        n.metadata.name
        for n in (kube["core"].list_namespace().items or [])
        if not n.metadata.deletion_timestamp
    ]  # All active namespaces — the local admin's tenant list.


def _list(plural, ns=None):
    fn, extra = (
        (kube["custom"].list_cluster_custom_object, ())
        if ns is None
        else (kube["custom"].list_namespaced_custom_object, (ns,))
    )
    return (
        fn(GROUP, VERSION, *extra, plural).get("items") or []
    )  # ns=None lists the cluster-scoped CR; otherwise the namespaced one


list_cluster_service_classes = partial(_list, "clusterserviceclasses")
list_cluster_service_plans = partial(_list, "clusterserviceplans")
list_service_classes = partial(_list, "serviceclasses")
list_service_plans = partial(_list, "serviceplans")


def _ref(item, ref_key, name_key):
    return (spec := item.get("spec") or {}).get(ref_key, {}).get("name") or spec.get(
        name_key
    )


def _enrich_namespaced(ns, items):
    # namespaced instances reference class/plan by UUID; attach the
    # human-facing external names so the UI never shows raw UUIDs
    if not any(_ref(i, "serviceClassRef", "serviceClassName") for i in items):
        return items
    cls_by = {
        c["metadata"]["name"]: c["spec"]["externalName"]
        for c in list_service_classes(ns)
    }
    plan_by = {
        p["metadata"]["name"]: p["spec"]["externalName"] for p in list_service_plans(ns)
    }
    enriched = []
    for cr in items:
        cls = _ref(cr, "serviceClassRef", "serviceClassName")
        if cls:
            cr = {
                **cr,
                "_offeringExternalName": cls_by.get(cls),
                "_planExternalName": plan_by.get(
                    _ref(cr, "servicePlanRef", "servicePlanName")
                ),
            }
        enriched.append(cr)
    return enriched


def list_instances(ns):
    return _enrich_namespaced(ns, _list("serviceinstances", ns))


def get_instance(ns, name):
    return _enrich_namespaced(
        ns,
        [
            kube["custom"].get_namespaced_custom_object(
                GROUP, VERSION, ns, "serviceinstances", name
            )
        ],
    )[0]


def resolve_namespaced_refs(ns, class_external_name, plan_external_name):
    # A namespaced ServiceBroker (registered per tenant namespace) produces
    # namespaced ServiceClass/ServicePlan CRs; instances reference them by
    # UUID via serviceClassRef/servicePlanRef instead of external names.
    cls = next(
        (
            c
            for c in list_service_classes(ns)
            if c["spec"].get("externalName") == class_external_name
        ),
        None,
    )
    if cls is None:
        return None
    plan = next(
        (
            p
            for p in list_service_plans(ns)
            if p["spec"].get("externalName") == plan_external_name
            and (p["spec"].get("serviceClassRef") or {}).get("name")
            == cls["metadata"]["name"]
        ),
        None,
    )
    if plan is None:
        return None
    return {"classRef": cls["metadata"]["name"], "planRef": plan["metadata"]["name"]}


# K8s names must be DNS labels; a non-conforming (any-language/case) user
# name becomes a <prefix>-<hash8> slug + display-name annotation on the CR.
INSTANCE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,29}$")
RFC1123_SUBDOMAIN_RE = re.compile(
    r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$"
)


def _name_or_slug(name, valid_re, prefix):
    return (
        {"id": trimmed, "displayName": None}
        if valid_re.fullmatch(trimmed := str(name).strip())
        else {
            "id": f"{prefix}-{hashlib.sha1(trimmed.encode(), usedforsecurity=False).hexdigest()[:8]}",
            "displayName": trimmed,
        }
    )


instance_slug = partial(_name_or_slug, valid_re=INSTANCE_NAME_RE, prefix="svc")
binding_slug = partial(_name_or_slug, valid_re=RFC1123_SUBDOMAIN_RE, prefix="b")


def _create_cr(ns, plural, kind, metadata, spec):
    body = {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": kind,
        "metadata": metadata,
        "spec": spec,
    }
    return kube["custom"].create_namespaced_custom_object(
        GROUP, VERSION, ns, plural, body
    )


def create_instance(
    ns,
    name,
    class_name,
    plan_name,
    extra_params=None,
    display_name=None,
    namespaced_refs=None,
):
    metadata = {
        "name": name,
        **(
            {"annotations": {"service-manager.io/display-name": display_name}}
            if display_name
            else {}
        ),
    }
    spec = (
        {
            "serviceClassName": namespaced_refs["classRef"],
            "servicePlanName": namespaced_refs["planRef"],
        }
        if namespaced_refs
        else {
            "clusterServiceClassExternalName": class_name,
            "clusterServicePlanExternalName": plan_name,
        }
    )
    spec["parameters"] = {
        **(extra_params or {}),
        "namespace": ns,
        "instance_name": name,
    }
    return _create_cr(
        ns, "serviceinstances", "ServiceInstance", metadata, spec
    )  # Free-form user name (any language); the CR name is a generated slug. admission webhook resolves names via serviceClassName/PlanName; brokerpak provisions into this ns and names resources after the instance; pinned keys LAST (tenant isolation)


def update_instance(ns, name, plan_name, extra_params=None):
    cr = get_instance(ns, name)
    spec = cr[
        "spec"
    ]  # declarative svcat-style update: rewrite plan ref + merge params; the controller reconciles to the broker
    if (
        plan_name
        and plan_name
        != (cr.get("_planExternalName") or spec.get("clusterServicePlanExternalName"))
    ):  # plan surgery only when the plan really changes — the webhook's "exactly one of" rule
        if (spec.get("serviceClassRef") or {}).get("name") or spec.get(
            "serviceClassName"
        ):
            if not (
                refs := resolve_namespaced_refs(
                    ns,
                    cr.get("_offeringExternalName")
                    or spec.get("clusterServiceClassExternalName")
                    or "",
                    plan_name,
                )
            ):
                raise ValueError(
                    f'plan "{plan_name}" not found in this namespace catalog'
                )  # namespaced plan change: servicePlanName=UUID + delete BOTH refs or the mutating webhook panics
            spec["servicePlanName"] = refs["planRef"]
            spec.pop("servicePlanRef", None)
            spec.pop("serviceClassRef", None)
        else:
            spec["clusterServicePlanExternalName"] = plan_name
    spec["parameters"] = {**(spec.get("parameters") or {}), **(extra_params or {})}
    spec["updateRequests"] = (spec.get("updateRequests") or 0) + 1
    cr.pop("_offeringExternalName", None)
    cr.pop("_planExternalName", None)
    return kube[
        "custom"
    ].replace_namespaced_custom_object(
        GROUP, VERSION, ns, "serviceinstances", name, cr
    )  # svcat touch: after a terminal failure the controller only reprocesses when updateRequests is bumped # strip display-enrichment fields — unknown spec fields made the webhook's plan-change path panic


def create_binding(ns, name, instance_name, display_name=None, bind_params=None):
    spec = {
        "instanceRef": {"name": instance_name},
        "secretName": f"binding-{name}",
        **({"parameters": bind_params} if bind_params else {}),
    }
    return _create_cr(
        ns,
        "servicebindings",
        "ServiceBinding",
        {
            "name": name,
            "annotations": {"service-manager.io/display-name": display_name}
            if display_name
            else {},
        },
        spec,
    )


list_bindings = partial(_list, "servicebindings")


def get_binding(ns, name):
    return kube["custom"].get_namespaced_custom_object(
        GROUP, VERSION, ns, "servicebindings", name
    )


def _delete_cr(ns, name, plural):
    kube["custom"].delete_namespaced_custom_object(GROUP, VERSION, ns, plural, name)


delete_instance = partial(_delete_cr, plural="serviceinstances")
delete_binding = partial(_delete_cr, plural="servicebindings")
