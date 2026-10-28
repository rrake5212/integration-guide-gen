import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from guidegen.spec import SpecError, assert_valid, load_schema, validate_spec, walk_facts

FIXTURES = Path(__file__).with_name("fixtures")


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class SchemaTests(unittest.TestCase):
    def test_schema_is_valid_draft_2020_12(self) -> None:
        Draft202012Validator.check_schema(load_schema())

    def test_oauth_fixture_is_valid(self) -> None:
        self.assertEqual(validate_spec(load("example-oauth.spec.json")), [])

    def test_apikey_fixture_is_valid(self) -> None:
        self.assertEqual(validate_spec(load("example-apikey.spec.json")), [])

    def test_fact_paths_include_array_indexes(self) -> None:
        paths = [path for path, _fact in walk_facts(load("example-oauth.spec.json"))]
        self.assertIn("auth.oauth.scopes[0].name", paths)
        self.assertIn("auth.oauth.authorization_url", paths)

    def test_missing_top_level_key(self) -> None:
        spec = load("example-oauth.spec.json")
        del spec["quality"]
        self.assertTrue(any("quality" in issue for issue in validate_spec(spec)))

    def test_fact_with_value_needs_a_quote(self) -> None:
        spec = load("example-oauth.spec.json")
        spec["auth"]["oauth"]["token_url"]["quote"] = ""
        self.assertTrue(validate_spec(spec))

    def test_null_fact_needs_null_quote_and_source(self) -> None:
        spec = load("example-oauth.spec.json")
        spec["auth"]["oauth"]["discovery_url"]["quote"] = "something"
        self.assertTrue(validate_spec(spec))


class SemanticTests(unittest.TestCase):
    def test_api_key_only_rejects_oauth_details(self) -> None:
        spec = load("example-apikey.spec.json")
        spec["auth"]["oauth"] = load("example-oauth.spec.json")["auth"]["oauth"]
        self.assertTrue(any("api_key_only" in issue for issue in validate_spec(spec)))

    def test_oidc_only_needs_discovery(self) -> None:
        spec = load("example-oauth.spec.json")
        spec["resolution"]["availability"] = "oidc_only"
        issues = validate_spec(spec)
        self.assertTrue(any("discovery URL" in issue for issue in issues))

    def test_time_range_is_sane(self) -> None:
        spec = load("example-oauth.spec.json")
        spec["setup"]["time"]["range_min_minutes"] = 40
        spec["setup"]["time"]["range_max_minutes"] = 30
        self.assertTrue(any("range_min_minutes" in issue for issue in validate_spec(spec)))

    def test_unknown_field_must_be_listed(self) -> None:
        spec = load("example-oauth.spec.json")
        spec["unknown_fields"] = []
        self.assertTrue(any("missing 'auth.oauth.discovery_url'" in issue for issue in validate_spec(spec)))

    def test_unknown_field_cannot_be_a_verified_fact(self) -> None:
        spec = load("example-oauth.spec.json")
        spec["unknown_fields"].append("auth.oauth.token_url")
        self.assertTrue(any("is not an unknown fact" in issue for issue in validate_spec(spec)))

    def test_fact_source_must_be_declared(self) -> None:
        spec = load("example-oauth.spec.json")
        spec["auth"]["oauth"]["token_url"]["source_url"] = "https://elsewhere.example/docs"
        self.assertTrue(any("source_url not in sources" in issue for issue in validate_spec(spec)))

    def test_warning_code_must_exist(self) -> None:
        spec = load("example-oauth.spec.json")
        spec["warnings"][0]["code"] = "NOT_A_WARNING"
        self.assertTrue(any("unknown code" in issue for issue in validate_spec(spec)))

    def test_env_names_are_unique(self) -> None:
        spec = load("example-oauth.spec.json")
        spec["env"].append(dict(spec["env"][0]))
        self.assertTrue(any("duplicate variable" in issue for issue in validate_spec(spec)))

    def test_api_key_env_var_must_be_declared(self) -> None:
        spec = load("example-apikey.spec.json")
        spec["env"][0]["name"] = "OTHER_KEY"
        self.assertTrue(any("not declared in env" in issue for issue in validate_spec(spec)))

    def test_complete_needs_steps(self) -> None:
        spec = load("example-oauth.spec.json")
        spec["setup"]["steps"] = []
        self.assertTrue(any("at least one step" in issue for issue in validate_spec(spec)))

    def test_step_orders_are_unique(self) -> None:
        spec = load("example-oauth.spec.json")
        spec["setup"]["steps"][1]["order"] = 1
        self.assertTrue(any("duplicate order" in issue for issue in validate_spec(spec)))

    def test_quality_counters_are_computed(self) -> None:
        spec = load("example-oauth.spec.json")
        spec["quality"]["facts_total"] += 1
        self.assertTrue(any("facts_total" in issue for issue in validate_spec(spec)))

    def test_failed_status_can_be_sparse(self) -> None:
        spec = load("example-oauth.spec.json")
        spec["status"] = "failed"
        spec["resolution"]["availability"] = "unknown"
        spec["resolution"]["confidence"] = "none"
        spec["auth"] = {"methods": [], "primary": None, "oauth": None, "api_key": None}
        spec["setup"] = {
            "time": {"likely_minutes": None, "range_min_minutes": None, "range_max_minutes": None,
                     "review_wait_days": None, "drivers": [], "confidence": "none"},
            "prerequisites": [], "steps": [], "verification": [], "blockers": [],
        }
        spec["env"] = []
        spec["sources"] = []
        spec["quality"] = {"facts_total": 0, "facts_verified": 0, "unknown_count": 0, "confidence": "none"}
        spec["unknown_fields"] = []
        spec["warnings"] = []
        self.assertEqual(validate_spec(spec), [])

    def test_assert_valid_raises_with_issues(self) -> None:
        spec = load("example-oauth.spec.json")
        spec["quality"]["facts_total"] = 0
        with self.assertRaises(SpecError) as ctx:
            assert_valid(spec)
        self.assertTrue(ctx.exception.issues)


if __name__ == "__main__":
    unittest.main()
