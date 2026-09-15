from django.urls import path
from core import views

urlpatterns = [
    path("", views.root, name="root"),
    # auth: local admin sign-in + OIDC authorization-code + PKCE, one
    # server-side session either way
    path("auth/login", views.auth_login),
    path("auth/oidc", views.auth_oidc),
    path("auth/local", views.auth_local),
    path("auth/callback", views.auth_callback),
    path("auth/logout", views.auth_logout),
    # static assets (vendored files — not worth the static machinery):
    # htmx + Alpine + app.js + the prebuilt DaisyUI css (zero build step)
    path("htmx.min.js", views.asset("htmx.min.js", "application/javascript")),
    path("alpine.min.js", views.asset("alpine.min.js", "application/javascript")),
    path("app.js", views.asset("app.js", "application/javascript")),
    path("app.css", views.asset("app.css", "text/css")),
    path("daisyui.css", views.daisyui_css),
    # service offerings picker (behind "Add Service Instance"); picking an
    # offering goes straight to its provision form
    path("services", views.services),
    # instances (list/detail pages host their polling table/body fragments
    # as inline partials — the 3s polls hit the page URLs with ?partial=)
    path("instances", views.instances_page),
    path("instances/new", views.provision_dispatch),
    path("instances/new/form", views.provision_form_fragment),
    path("instances/new/plan", views.plan_fields_fragment),
    path("instances/new/schema", views.schema_download),
    path("instances/show/<str:inst_id>", views.instance_show),
    path("instances/edit/<str:inst_id>", views.instance_edit_dispatch),
    path("instances/delete/<str:inst_id>", views.instance_delete),
    # bindings (created and managed inline on an instance's page)
    path("bindings/new/freeinputs", views.bind_freeinputs),
    path("bindings/new", views.bind_dispatch),
    path("bindings/<str:binding_id>/credentials", views.binding_credentials),
    path("bindings/delete/<str:binding_id>", views.binding_delete),
]
