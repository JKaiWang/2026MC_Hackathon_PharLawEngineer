from pathlib import Path


def test_mail_ui_exposes_gmail_sync_and_temporary_schedule_status():
    html = (Path(__file__).parents[1] / 'web' / 'index.html').read_text(encoding='utf-8')
    assert 'id="gmailConnect"' in html
    assert 'id="gmailSync"' in html
    assert '單次調整' in html
    assert '復原這次調整' in html


def test_interface_uses_ncku_maroon_as_primary_accent():
    html = (Path(__file__).parents[1] / 'web' / 'index.html').read_text(encoding='utf-8')
    assert '--accent: #7c1f2a' in html
