import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import media_handler as mh


def test_no_image_returns_marker():
    mh.clear_pending()
    mh.set_pending_media(None)
    assert mh.describe_image("anything") == "[NO_IMAGE: nothing is attached to describe]"


def test_describe_memoizes_per_turn():
    calls = {"n": 0}
    orig = mh._call_vision_model

    def fake(image_parts, prompt):
        calls["n"] += 1
        return "a red panda"

    mh._call_vision_model = fake
    try:
        mh.set_pending_media([{"type": "image_url",
                               "image_url": {"url": "data:image/jpeg;base64,AAAA"}}])
        r1 = mh.describe_image("")
        r2 = mh.describe_image("")
        assert r1 == r2 == "[IMAGE DESCRIPTION]\na red panda", r1
        assert calls["n"] == 1, calls["n"]

        # A reply's clear_pending() must NOT blind describe-image mid-turn.
        mh.clear_pending()
        assert mh.describe_image("") == "[IMAGE DESCRIPTION]\na red panda"
        # A new non-image message makes the prior image stale.
        mh.set_pending_media(None)
        assert mh.describe_image("") == "[NO_IMAGE: nothing is attached to describe]"
    finally:
        mh._call_vision_model = orig
        mh.clear_pending()
        mh.set_pending_media(None)


def test_describe_never_raises():
    def boom(image_parts, prompt):
        raise RuntimeError("network down")

    orig = mh._call_vision_model
    mh._call_vision_model = boom
    try:
        mh.set_pending_media([{"type": "image_url",
                               "image_url": {"url": "data:image/jpeg;base64,BBBB"}}])
        out = mh.describe_image("")
        assert out.startswith("[IMAGE_DESCRIPTION_FAILED:"), out
    finally:
        mh._call_vision_model = orig
        mh.clear_pending()
        mh.set_pending_media(None)


def test_describe_never_raises_on_malformed_media():
    # A producer could set a malformed value directly; describe_image must still
    # return a marker rather than propagate an exception.
    mh._pending_media = 12345          # not a list/None
    mh._describe_media = 12345
    try:
        out = mh.describe_image("")
        assert out.startswith("[IMAGE_DESCRIPTION_FAILED:"), out
    finally:
        mh.clear_pending()
        mh.set_pending_media(None)


if __name__ == "__main__":
    test_no_image_returns_marker()
    test_describe_memoizes_per_turn()
    test_describe_never_raises()
    test_describe_never_raises_on_malformed_media()
    print("all media_handler tests passed")
