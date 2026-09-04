from engine.emulation.kinds import KINDS

BLOCKED = ("cisco", "ios", "iol", "iou")


def test_frr_is_registered_and_free():
    assert "frr" in KINDS
    assert KINDS["frr"].license_class == "free"


def test_no_cisco_kind_registered():
    for name in KINDS:
        assert not any(term in name for term in BLOCKED), name
