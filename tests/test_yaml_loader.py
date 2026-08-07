"""Verify strict YAML parsing at every specification boundary."""

import unittest

import yaml

from agent.yaml_loader import MAXIMUM_YAML_ALIASES, load_yaml


class StrictYamlLoaderTest(unittest.TestCase):
    def test_loads_an_unambiguous_safe_document(self):
        self.assertEqual(
            load_yaml("version: 2\nmodels: []\n"),
            {"version": 2, "models": []},
        )

    def test_rejects_duplicate_mapping_keys(self):
        with self.assertRaisesRegex(yaml.YAMLError, "duplicate key 'version'"):
            load_yaml("version: 1\nversion: 2\n")

    def test_rejects_excessive_alias_expansion(self):
        aliases = "\n".join(
            f"copy_{index}: *shared"
            for index in range(MAXIMUM_YAML_ALIASES + 1)
        )

        with self.assertRaisesRegex(yaml.YAMLError, "too many aliases"):
            load_yaml(f"shared: &shared {{value: 1}}\n{aliases}\n")

    def test_preserves_safe_loader_tag_restrictions(self):
        with self.assertRaises(yaml.YAMLError):
            load_yaml("value: !!python/object:builtins.object {}\n")


if __name__ == "__main__":
    unittest.main()
