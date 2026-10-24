"""Protected paths are injected into coders during dispatch preparation."""

from unittest.mock import patch

from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import Task
from snodo.coders.base import Coder
from snodo.engine.loop import GraphBuilder


class _Coder(Coder):
    def implement(self, task, context):
        return []

    def execute(self, task, context):
        return []


def test_graph_builder_injects_protected_paths_as_tuple_before_coder_dispatch():
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        initial_mode="producer",
        modes=[Mode(mode_id="producer", name="Producer")],
        validators=[Validator(validator_id="quality", validator_type="quality")],
        protected_paths=["docs/policy.md", "schemas/protocol.json"],
    )
    coder = _Coder()

    with patch("snodo.config.ConfigManager.load", return_value={"model": "gpt-4o"}):
        builder = GraphBuilder(protocol=protocol, coder=coder)

    assert isinstance(coder.protected_paths, tuple)
    assert coder.protected_paths == ()
    builder._prepare_coder(coder, None, Task(id="task", spec="test"))
    assert coder.protected_paths == ("docs/policy.md", "schemas/protocol.json")
    assert isinstance(coder.protected_paths, tuple)
