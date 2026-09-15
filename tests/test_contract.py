"""The input contract (REQUIREMENTS §8): schema tables, form schema,
validators with exact rejection reasons, and the client-mirror drawer
contract. Byte-level expectations pin the messages the Alpine gate mirrors."""

from django.test import SimpleTestCase

from core import input_schema as ix


class TestSchemas(SimpleTestCase):
    def test_all_four_offerings_present(self):
        self.assertEqual(set(ix.SCHEMAS), {"minio", "redis", "postgresql", "vm"})
        self.assertEqual(set(ix.BIND_SCHEMAS), set(ix.SCHEMAS))

    def test_plan_values_shapes(self):
        self.assertEqual(
            ix.PLAN_VALUES["redis"]["small"], {"memory": "256Mi", "cpu": "200m"}
        )
        self.assertEqual(ix.PLAN_VALUES["minio"]["small"], {"storage": "10Gi"})


class TestFormSchema(SimpleTestCase):
    def test_unknown_offering_returns_none(self):
        self.assertIsNone(ix.form_schema_for("nope"))
        self.assertIsNone(ix.form_schema_for("nope", "bind"))

    def test_pinned_inputs_never_shown(self):
        form = ix.form_schema_for("redis", "provision", "redis-small")
        self.assertIn("maxmemory_policy", form["schema"]["properties"])
        self.assertNotIn("namespace", form["schema"]["properties"])
        self.assertNotIn("instance_name", form["schema"]["properties"])
        self.assertEqual(form["editable"], ["maxmemory_policy"])

    def test_plan_props_are_readonly_with_values(self):
        form = ix.form_schema_for("redis", "provision", "small")
        plan = form["schema"]["properties"]["memory"]
        self.assertTrue(plan["readOnly"])
        self.assertEqual(plan["default"], "256Mi")
        self.assertEqual(plan["x-layer"], "plan")

    def test_unknown_keys_rejected_by_schema(self):
        form = ix.form_schema_for("redis")
        self.assertFalse(form["schema"]["additionalProperties"])


class TestValidateUser(SimpleTestCase):
    def call(self, offering, params):
        return ix.validate_user_params(offering, params)

    def test_none_is_valid_empty(self):
        self.assertEqual(self.call("redis", None), ({}, None))

    def test_non_object_rejected(self):
        self.assertEqual(
            self.call("redis", "x"), (None, "parameters must be a JSON object")
        )

    def test_unknown_offering(self):
        self.assertEqual(self.call("nope", {}), (None, 'unknown offering "nope"'))

    def test_computed_input_reason(self):
        self.assertEqual(
            self.call("redis", {"kubeconfig_path": "x"}),
            (None, '"kubeconfig_path" is a computed input - controlled by the broker'),
        )

    def test_plan_input_reason(self):
        self.assertEqual(
            self.call("redis", {"memory": "1"}),
            (
                None,
                '"memory" is a plan input - locked by the selected plan '
                "(pick another plan instead)",
            ),
        )

    def test_platform_pinned_reason(self):
        err = self.call("redis", {"namespace": "x"})[1]
        self.assertIn('"namespace" is', err)
        self.assertIn("platform", err)

    def test_unknown_parameter_lists_known(self):
        err = self.call("redis", {"nope": 1})[1]
        self.assertIn('unknown parameter "nope"', err)
        self.assertIn("maxmemory_policy", err)

    def test_type_checks(self):
        self.assertEqual(
            self.call("minio", {"console_port": "x"}),
            (None, '"console_port" must be a number'),
        )
        self.assertEqual(
            self.call("minio", {"console_port": True}),
            (None, '"console_port" must be a number'),
        )
        self.assertEqual(
            self.call("minio", {"console_port": 9001}), ({"console_port": 9001}, None)
        )

    def test_enum_check(self):
        self.assertEqual(
            self.call("redis", {"maxmemory_policy": "nope"})[1],
            '"maxmemory_policy" must be one of: allkeys-lru, volatile-lru, '
            "allkeys-lfu, noeviction",
        )


class TestValidateBind(SimpleTestCase):
    def test_computed_bind_input_reason(self):
        self.assertEqual(
            ix.validate_bind_params("redis", {"host": "h"}),
            (None, '"host" is a computed bind input - projected from the instance'),
        )

    def test_unknown_bind_parameter(self):
        err = ix.validate_bind_params("redis", {"nope": 1})[1]
        self.assertIn('unknown bind parameter "nope"', err)


class TestDrawerContract(SimpleTestCase):
    def test_layers_mirror_server_vocabulary(self):
        c = ix.drawer_contract("redis", "provision")
        self.assertEqual(c["computed"], ["kubeconfig_path"])
        self.assertEqual(c["plan"], ["memory", "cpu"])
        self.assertEqual(
            c["pinned"]["namespace"],
            "set by the platform from your tenant (namespace isolation)",
        )
        self.assertEqual(c["editable"], ["maxmemory_policy"])

    def test_bind_contract_has_no_pinned_layer(self):
        c = ix.drawer_contract("redis", "bind")
        self.assertEqual(c["pinned"], {})
        self.assertEqual(c["computed"], ["host", "port", "password"])
