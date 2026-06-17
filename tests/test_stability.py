from loom.ai import _strip_thinking


def test_strip_thinking_tags():
    assert _strip_thinking("<think>private</think>Visible answer") == "Visible answer"


def test_strip_thinking_no_tags():
    assert _strip_thinking("Visible") == "Visible"
