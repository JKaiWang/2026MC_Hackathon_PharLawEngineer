from pathlib import Path


HTML = Path(__file__).parents[1].joinpath("web", "index.html").read_text(encoding="utf-8")


def test_week_schedule_renders_all_seven_days():
    assert 'const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];' in HTML
    assert 'grid-template-columns: 3.25rem repeat(7, 1fr);' in HTML
    assert '<div>一</div><div>二</div><div>三</div><div>四</div><div>五</div><div>六</div><div>日</div>' in HTML
