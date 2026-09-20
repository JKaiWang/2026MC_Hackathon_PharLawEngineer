import base64
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from commute_agent.tools import schedule_changes as sc
from commute_agent.tools.class_schedule import find_classes, load_courses
from commute_agent.tools.course_mail import parse_moodle_time_change
from commute_agent.tools import gmail_sync
from commute_agent.tools.gmail_sync import body_text, fetch_messages

SUBJECT = '1151_影像處理、電腦視覺及...(1151_F743000)： 下周二(9/22)上課時間改為早上10:10'
BODY = '由 侯冠翔發表於2026年 09月 15日(Tue) 18:04\n下周二(9/22)上課時間改為早上10:10'
COURSE = dict(name='【F7-155】影像處理、電腦視覺及深度學習概論', day='Tuesday', start_time='09:00', end_time='12:00', location='共同教室-A1302')


@pytest.fixture
def schedule(tmp_path):
    path = tmp_path / 'schedule.json'
    path.write_text(json.dumps({'courses': [COURSE]}, ensure_ascii=False), encoding='utf-8')
    return path


def moment(day, hour=9):
    return datetime(2026, 9, day, hour, tzinfo=ZoneInfo('Asia/Taipei'))


def test_overlay_changes_only_one_date_without_rewriting_schedule(schedule):
    before = schedule.read_bytes()
    mail = parse_moodle_time_change(SUBJECT, BODY)
    result = sc.record(schedule, 'gmail:one', SUBJECT, mail, 100)
    assert result['status'] == 'active'
    courses = load_courses(schedule)
    current, next_class = find_classes(courses, moment(22))
    assert current is None
    assert next_class['start_time'] == '10:10'
    assert next_class['end_time'] == '12:00'
    assert next_class['temporary_change']['id'] == 'gmail:one'
    assert find_classes(courses, moment(29))[0]['start_time'] == '09:00'
    assert schedule.read_bytes() == before


def test_duplicate_undo_and_poll_do_not_reapply(schedule):
    mail = parse_moodle_time_change(SUBJECT, BODY)
    sc.record(schedule, 'one', SUBJECT, mail)
    sc.record(schedule, 'one', SUBJECT, mail)
    assert len(sc.changes(schedule)) == 1
    sc.undo(schedule, 'one')
    assert sc.record(schedule, 'one', SUBJECT, mail)['status'] == 'reverted'
    assert find_classes(load_courses(schedule), moment(22))[0]['start_time'] == '09:00'


def test_newer_notice_supersedes_and_older_does_not_override(schedule):
    mail = parse_moodle_time_change(SUBJECT, BODY)
    sc.record(schedule, 'one', SUBJECT, mail, 100)
    sc.record(schedule, 'two', SUBJECT, {**mail, 'new_time': '11:00'}, 200)
    assert sc.record(schedule, 'old', SUBJECT, mail, 50)['status'] == 'review'
    assert find_classes(load_courses(schedule), moment(22))[1]['start_time'] == '11:00'


def test_ambiguous_course_and_invalid_end_need_review(schedule):
    mail = parse_moodle_time_change(SUBJECT, BODY)
    assert sc.record(schedule, 'late', SUBJECT, {**mail, 'new_time': '13:00'})['status'] == 'review'
    schedule.write_text(json.dumps({'courses': [COURSE, {**COURSE, 'location': '其他教室'}]}), encoding='utf-8')
    assert sc.record(schedule, 'ambiguous', SUBJECT, mail)['status'] == 'review'


def test_changed_base_schedule_does_not_inherit_old_overlay(schedule):
    sc.record(schedule, 'one', SUBJECT, parse_moodle_time_change(SUBJECT, BODY))
    schedule.write_text(json.dumps({'courses': [{**COURSE, 'start_time': '08:00'}]}), encoding='utf-8')
    assert find_classes(load_courses(schedule), moment(22))[0]['start_time'] == '08:00'


def test_unavailable_model_is_never_auto_applied(schedule):
    mail = parse_moodle_time_change(SUBJECT, BODY)
    assert sc.record(schedule, 'one', SUBJECT, {**mail, 'source': 'gemma_local'})['status'] == 'review'


def test_reading_schedule_does_not_create_database(schedule):
    load_courses(schedule)
    assert not list(schedule.parent.glob('*.sqlite3'))


def test_gmail_mime_plain_preferred_and_attachments_ignored():
    def part(mime, text, **kw):
        return dict(mimeType=mime, body={'data': base64.urlsafe_b64encode(text.encode()).decode()}, **kw)
    assert body_text({'mimeType':'multipart/alternative', 'parts': [part('text/html','<p>html</p>'), part('text/plain','正文')]}) == '正文'
    assert body_text(part('text/plain','secret', filename='attached.txt')) == ''


def test_gmail_pagination():
    class Response:
        def __init__(self, payload): self.payload = payload
        def raise_for_status(self): pass
        def json(self): return self.payload
    class Session:
        def get(self, url, params, timeout):
            assert 'from:noreply@ncku.edu.tw' in params['q']
            return Response({'messages':[{'id':'two'}]} if params.get('pageToken') else {'messages':[{'id':'one'}], 'nextPageToken':'next'})
    assert list(fetch_messages(Session())) == [{'id':'one'}, {'id':'two'}]


def test_unparsed_moodle_notice_becomes_review_without_blocking_sync(schedule, monkeypatch):
    monkeypatch.setattr(gmail_sync, 'classify_course_mail',
                        lambda subject, body: {'status': 'unavailable'})
    payload = {
        'id': 'notice', 'internalDate': '100',
        'payload': {'headers': [
            {'name': 'From', 'value': 'NCKU Moodle <noreply@ncku.edu.tw>'},
            {'name': 'Subject', 'value': '1151_影像處理公告'},
        ], 'mimeType': 'text/plain',
        'body': {'data': base64.urlsafe_b64encode('一般公告'.encode()).decode()}},
    }
    item = gmail_sync.process_message(schedule, payload)
    assert item['status'] == 'review'
    assert item['source'] == 'moodle_unparsed'
    assert gmail_sync.process_message(schedule, payload)['id'] == item['id']
