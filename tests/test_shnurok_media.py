from src.shnurok.media import hdr_tonemap_prefix


def test_hdr_tonemap_prefix_sdr_is_noop():
    assert hdr_tonemap_prefix("bt709") == ""


def test_hdr_tonemap_prefix_unknown_is_noop():
    assert hdr_tonemap_prefix("") == ""


def test_hdr_tonemap_prefix_hlg_tonemaps():
    prefix = hdr_tonemap_prefix("arib-std-b67")
    assert "tonemap" in prefix
    assert prefix.endswith(",")


def test_hdr_tonemap_prefix_pq_tonemaps():
    assert hdr_tonemap_prefix("smpte2084") != ""
