from pathlib import Path


def test_route_road_events_surface_closed_route_warning():
    html = (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")

    assert "has_closed_route" in html
    assert "封閉路段" in html
    assert "請改道" in html
