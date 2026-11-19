import os

from django.urls import include, path

from core import views

handler404 = "core.views.handler404"  # styled 404 for unknown paths

urlpatterns = [
    path("", views.root, name="root"),
    # auth: local admin sign-in + OIDC authorization-code + PKCE, one
    # server-side session either way
    path("auth/login", views.auth_login),
    path("auth/oidc", views.auth_oidc),
    path("auth/local", views.auth_local),
    path("auth/callback", views.auth_callback),
    path("auth/logout", views.auth_logout),
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
    # bindings (connected/revealed/deleted inline on an instance's page)
    path("bindings/connect/<str:inst_id>", views.bind_connect),
    path("bindings/<str:binding_id>/credentials", views.binding_credentials),
    path("bindings/delete/<str:binding_id>", views.binding_delete),
]

# debug toolbar panels, dev only (DJANGO_DEBUG=1; the package is a dev
# dependency, so it is absent from the production image)
if os.environ.get("DJANGO_DEBUG") == "1":
    import debug_toolbar  # noqa: F401 (import probe doubles as the gate)

    urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
