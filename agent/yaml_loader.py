"""Load YAML safely while rejecting ambiguous or expansive structures."""

from typing import Any

import yaml
from yaml.composer import ComposerError
from yaml.constructor import ConstructorError
from yaml.events import AliasEvent
from yaml.nodes import MappingNode


MAXIMUM_YAML_ALIASES = 50


class StrictSafeLoader(yaml.SafeLoader):
    """SafeLoader variant with duplicate-key and alias-count guards."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._alias_count = 0

    def compose_node(self, parent, index):
        if self.check_event(AliasEvent):
            self._alias_count += 1
            if self._alias_count > MAXIMUM_YAML_ALIASES:
                event = self.peek_event()
                raise ComposerError(
                    "while composing YAML",
                    None,
                    "YAML contains too many aliases",
                    event.start_mark,
                )
        return super().compose_node(parent, index)

    def construct_mapping(
        self,
        node: MappingNode,
        deep: bool = False,
    ) -> dict[Any, Any]:
        if not isinstance(node, MappingNode):
            raise ConstructorError(
                None,
                None,
                f"expected a mapping node, but found {node.id}",
                node.start_mark,
            )

        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as error:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable key",
                    key_node.start_mark,
                ) from error
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def load_yaml(content: str) -> Any:
    """Parse one YAML document using the strict safe loader."""
    return yaml.load(content, Loader=StrictSafeLoader)
