"""Status word -> tint class: CR statuses (ready/failed/updating) + Kubernetes event types (Normal/Warning)."""

from django import template

register = template.Library()
register.filter(
    "badge_cls",
    lambda status: {
        "ready": "ok",
        "failed": "err",
        "updating": "warn",
        "normal": "ok",
        "warning": "warn",
    }.get(str(status).lower(), "info"),
)
