"""Input-layer contract per offering (single source of truth — mirror the brokerpaks): computed > plan > user, platform-pinned (namespace, instance_name). The server validates the user layer and explains WHY a field is not settable."""

import re

PLATFORM_PINNED = {
    "namespace": "set by the platform from your tenant (namespace isolation)",
    "instance_name": "set by the platform from the instance name field",
}
REDIS_MAXMEMORY_ENUM = ["allkeys-lru", "volatile-lru", "allkeys-lfu", "noeviction"]
VM_IMAGE_ENUM = [
    "lscr.io/linuxserver/openssh-server:latest",
    "lscr.io/linuxserver/openssh-server:amd64-version-10.3_p1-r0",
]


def _field(name, **extra):
    return {
        "name": name,
        "type": "string",
        **extra,
    }  # (offering, plan-input names, one extra free user field) per brokerpak


SCHEMAS = {
    offering: {
        "computedInputs": [{"name": "kubeconfig_path"}],
        "planInputs": [_field(n) for n in plans.split()],
        "userInputs": [_field(name, **extra)],
    }
    for offering, plans, (name, extra) in [
        (
            "minio",
            "storage",
            (
                "console_port",
                {
                    "type": "number",
                    "details": "Port for the MinIO web console",
                    "default": 9001,
                },
            ),
        ),
        (
            "redis",
            "memory cpu",
            (
                "maxmemory_policy",
                {
                    "details": "Redis eviction policy",
                    "enum": REDIS_MAXMEMORY_ENUM,
                    "default": "allkeys-lru",
                },
            ),
        ),
        (
            "postgresql",
            "memory cpu storage",
            (
                "max_connections",
                {
                    "type": "number",
                    "details": "PostgreSQL max_connections setting",
                    "default": 100,
                },
            ),
        ),
        (
            "vm",
            "memory cpu",
            (
                "image",
                {
                    "required": True,
                    "details": "Docker image for the VM",
                    "enum": VM_IMAGE_ENUM,
                    "default": VM_IMAGE_ENUM[0],
                },
            ),
        ),
    ]
}
input_schema_for = SCHEMAS.get  # bind-side layers: today's brokerpaks define computed inputs only (projected from the instance)
BIND_SCHEMAS = {
    offering: {
        "computedInputs": [{"name": n} for n in names.split()],
        "planInputs": [],
        "userInputs": [],
    }
    for offering, names in {
        "minio": "host port access_key secret_key",
        "redis": "host port password",
        "postgresql": "host port username password database",
        "vm": "ssh_host ssh_port ssh_user ssh_password",
    }.items()
}
bind_schema_for = (
    BIND_SCHEMAS.get
)  # plan-input values (what each locked plan input shows), mirroring the brokerpaks
# What each locked plan-input shows per plan, mirroring the brokerpaks —
# each row is "<plan> <value for key1> <value for key2> ..." against the
# offering's key order in PLAN_KEYS.
PLAN_KEYS = {
    "redis": ["memory", "cpu"],
    "postgresql": ["storage", "memory", "cpu"],
    "vm": ["memory", "cpu"],
    "minio": ["storage"],
}
PLAN_VALUES = {}
for _offering, _plans in {
    "redis": ["small 256Mi 200m", "medium 512Mi 500m", "large 1Gi 1"],
    "postgresql": ["small 1Gi 512Mi 300m", "medium 1Gi 1Gi 500m", "large 1Gi 2Gi 1"],
    "vm": ["small 512Mi 500m", "medium 1Gi 1", "large 2Gi 2"],
    "minio": ["small 10Gi", "medium 50Gi", "large 100Gi"],
}.items():
    PLAN_VALUES[_offering] = {
        row.split()[0]: dict(zip(PLAN_KEYS[_offering], row.split()[1:]))
        for row in _plans
    }


def _op_schema(offering_id, op):
    return (BIND_SCHEMAS if op == "bind" else SCHEMAS).get(offering_id)


def form_schema_for(
    offering_id, op="provision", plan_name=None
):  # JSON Schema for the FORM renderer (offering, op, plan): computed/pinned inputs omitted, plan inputs readOnly with the plan's value, user inputs editable. Returns {'schema': ..., 'editable': ...} or None.
    if not (schema := _op_schema(offering_id, op)):
        return None
    fields = schema.get("userInputs") or []
    properties = {
        f["name"]: {
            "type": f.get("type", "string"),
            "title": f["name"],
            "x-layer": "user",
            **({"description": f["details"]} if f.get("details") else {}),
            **({"enum": f["enum"]} if f.get("enum") else {}),
            **({"default": f["default"]} if f.get("default") is not None else {}),
            **(
                {"pattern": f["pattern"]}
                if f.get("pattern") and f.get("type", "string") == "string"
                else {}
            ),
        }
        for f in fields
    }
    required = [f["name"] for f in fields if f.get("required")]
    editable = [f["name"] for f in fields]
    plan_vals = (PLAN_VALUES.get(offering_id) or {}).get(plan_name) or {}
    properties.update(
        {
            f["name"]: {
                "type": "string",
                "title": f["name"],
                "default": plan_vals[f["name"]],
                "readOnly": True,
                "x-layer": "plan",
            }
            for f in schema.get("planInputs") or []
            if f["name"] in plan_vals
        }
    )
    return {
        "schema": {
            "type": "object",
            "title": f"{offering_id} - {op}" + (f" ({plan_name})" if plan_name else ""),
            "properties": properties,
            "additionalProperties": False,
            **({"required": required} if required else {}),
        },
        "editable": editable,
    }  # unknown keys fail client-side exactly like the server — one contract, both ends


def _validate(offering_id, params, bind):
    if params is None:
        return (
            {},
            None,
        )  # shared walker: reject computed/plan/pinned keys with their reason, type-check # user inputs; returns (params, None) on success or (None, first-error).
    if not isinstance(params, dict):
        return None, "parameters must be a JSON object"
    if not (schema := _op_schema(offering_id, "bind" if bind else "provision")):
        return None, f'unknown offering "{offering_id}"'
    user = {f["name"]: f for f in schema["userInputs"]}
    why = (
        "a computed bind input - projected from the instance"
        if bind
        else "a computed input - controlled by the broker"
    )
    layers = {f["name"]: f'"{f["name"]}" is {why}' for f in schema["computedInputs"]}
    if not bind:
        layers.update(
            {
                f[
                    "name"
                ]: f'"{f["name"]}" is a plan input - locked by the selected plan (pick another plan instead)'
                for f in schema["planInputs"]
            }
        )
        layers.update({k: f'"{k}" is {v}' for k, v in PLATFORM_PINNED.items()})
    for key, value in params.items():
        if key in layers:
            return None, layers[key]
        if not (definition := user.get(key)):
            known = ", ".join(sorted(user)) or "none"
            label = (
                f'unknown bind parameter "{key}" - bind user inputs'
                if bind
                else f'unknown parameter "{key}" - user inputs'
            )
            return None, f"{label} for this offering: {known}"
        if (ftype := definition.get("type", "string")) == "string" and not isinstance(
            value, str
        ):
            return None, f'"{key}" must be a string'
        if ftype == "number" and (
            not isinstance(value, (int, float)) or isinstance(value, bool)
        ):
            return None, f'"{key}" must be a number'
        if ftype == "boolean" and not isinstance(value, bool):
            return None, f'"{key}" must be a boolean'
        if (
            definition.get("pattern")
            and ftype == "string"
            and not re.search(definition["pattern"], value)
        ):
            return (
                None,
                f'"{key}" does not match required pattern {definition["pattern"]}',
            )
        if definition.get("enum") and value not in definition["enum"]:
            return None, f'"{key}" must be one of: {", ".join(definition["enum"])}'
    return params, None


def validate_user_params(offering_id, params):
    return _validate(
        offering_id, params, bind=False
    )  # Validate user-supplied params against the offering's provision schema.


def validate_bind_params(offering_id, params):
    return _validate(
        offering_id, params, bind=True
    )  # Same contract as validate_user_params but against the bind schema.


def drawer_contract(
    offering_id, op="provision"
):  # Compact JSON contract for the Alpine drawer's client-side validation (app.js smValidateParams) — mirrors the server validators (same layers, reasons, order); the server re-validates. None for an unknown offering.
    if not (schema := _op_schema(offering_id, op)):
        return None
    pinned = {} if op == "bind" else PLATFORM_PINNED
    return {
        "op": op,
        "computed": [f["name"] for f in schema.get("computedInputs") or []],
        "plan": [f["name"] for f in schema.get("planInputs") or []],
        "pinned": dict(pinned),
        "user": {
            f["name"]: {
                "type": f.get("type", "string"),
                **({"enum": f["enum"]} if f.get("enum") else {}),
                **(
                    {"pattern": f["pattern"]}
                    if f.get("pattern") and f.get("type", "string") == "string"
                    else {}
                ),
            }
            for f in schema.get("userInputs") or []
        },
        "editable": [f["name"] for f in schema.get("userInputs") or []],
    }
