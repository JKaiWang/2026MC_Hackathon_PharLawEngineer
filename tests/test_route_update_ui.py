from pathlib import Path


def test_route_update_notification_and_demo_controls_are_present():
    html = (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")

    assert 'id="demoRouteUpdate"' in html
    assert "function routeUpdateSignature" in html
    assert "function showRouteUpdate" in html
    assert "路線已更新" in html
    assert "new Notification" in html
