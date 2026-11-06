import pytest

from commands.parser import parse_command


@pytest.mark.parametrize("separator", [".", ",", "!?", ":", ";"])
def test_command_boundary_punctuation_preserves_message(separator):
    message = f"/event{separator} Yesterday I met a friend, then went home."
    parsed = parse_command(message)
    assert parsed.command == "event"
    assert parsed.args == "Yesterday I met a friend, then went home."
    assert parsed.raw_message == message


def test_punctuation_inside_command_is_not_removed():
    assert parse_command("/event.foo details").command == "event.foo"
