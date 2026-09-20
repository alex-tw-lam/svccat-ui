"""Status word -> tint class: CR statuses (ready/failed/updating) + Kubernetes event types (Normal/Warning)."""

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

# shared by the badge_cls filter and the status_badge tag below
_TINTS = {
    "ready": "ok",
    "failed": "err",
    "updating": "warn",
    "normal": "ok",
    "warning": "warn",
}
_INFLIGHT = ("provisioning", "updating", "deprovisioning")

register.filter("badge_cls", lambda status: _TINTS.get(str(status).lower(), "info"))


@register.simple_tag
def status_badge(value, label="", spinner=""):
    """The status cell pattern, in one place: tint badge (+ optional leading
    spinner). spinner="inflight" shows an xs spinner while the status is an
    in-flight one (list rows); spinner="poll" shows an sm spinner through the
    detail page's 3s-poll window (the title). label defaults to the value
    (the latest-event badge colors by type but shows the reason)."""
    cls = _TINTS.get(str(value).lower(), "info")
    spin = ""
    if spinner == "poll":
        spin = '<span class="loading loading-spinner loading-sm"></span>'
    elif spinner == "inflight" and str(value) in _INFLIGHT:
        spin = '<span class="loading loading-spinner loading-xs"></span>'
    return mark_safe(
        f'{spin}<span class="badge {cls}">{label or value}</span>'
    )
