from oaw.errors import OawError


def test_oaw_error_preserves_user_facing_message():
    assert str(OawError("concise failure")) == "concise failure"
