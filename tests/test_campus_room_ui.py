from pathlib import Path


def test_campus_map_uses_the_gis_room_label_for_the_marker():
    html = (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")

    assert "function renderPlanImage(image, highlight, label" in html
    assert "r.campus_map.highlight_label" in html
