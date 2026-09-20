"""Date-specific overlays. The recurring schedule is never rewritten."""
import hashlib
import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path


def course_key(course):
    raw = {k: course.get(k) for k in ('name', 'day', 'start_time', 'end_time', 'location')}
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def connect(path):
    db = Path(str(path) + '.changes.sqlite3')
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db, timeout=15)
    con.execute('CREATE TABLE IF NOT EXISTS changes (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    return con


def changes(path):
    if not Path(str(path) + '.changes.sqlite3').is_file():
        return []
    with connect(path) as con:
        return [json.loads(row[0]) for row in con.execute('SELECT data FROM changes')]


def match(courses, mail):
    code = (mail.get('course_code') or '').split('_')[-1]
    exact = [c for c in courses if code and code in (c.get('course_code'), c.get('code'))]
    if exact:
        return exact[0] if len(exact) == 1 else None
    def norm(value):
        value = re.sub(r'【[^】]*】|\[[^]]*\]', '', value or '')
        return re.sub(r'[\W_]', '', value).casefold()
    hint = norm(re.sub(r'^\d{4}_', '', mail.get('course') or '').split('...')[0].split('…')[0])
    candidates = [c for c in courses if len(hint) >= 6 and norm(c['name']).startswith(hint)]
    return candidates[0] if len(candidates) == 1 else None


def record(path, message_id, subject, mail, received_at=0):
    from commute_agent.tools.class_schedule import load_courses, WEEKDAY_OF
    with connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT data FROM changes WHERE id=?', (message_id,)).fetchone()
        if row:
            return json.loads(row[0])
        course = match(load_courses(Path(path)), mail)
        item = dict(id=message_id, subject=subject, received_at=int(received_at),
                    status='review', reason='課程、日期或異動內容需要確認',
                    course_key=course_key(course) if course else None,
                    course_name=course['name'] if course else mail.get('course'),
                    original={k: v for k, v in course.items() if not k.startswith('_')} if course else None,
                    patch={}, effective_date=mail.get('effective_date'),
                    source=mail.get('source'), created_at=datetime.now().isoformat())
        # Only explicit, validated notices are eligible for automatic application.
        if course and mail.get('source') == 'moodle_explicit_text':
            try:
                day = date.fromisoformat(mail['effective_date'])
                start = mail['new_time']
                datetime.strptime(start, '%H:%M')
                if day.weekday() != WEEKDAY_OF[course['day']]:
                    item['reason'] = '通知日期與原課程星期不同，需確認補課或改期'
                elif start >= course['end_time']:
                    item['reason'] = '新開始時間超過原結束時間，需確認結束時間'
                else:
                    item.update(status='active', reason='僅調整此日期；結束時間沿用原課表',
                                patch={'start_time': start})
                    for (raw,) in con.execute('SELECT data FROM changes').fetchall():
                        old = json.loads(raw)
                        if old['status'] == 'active' and old['course_key'] == item['course_key'] and old['effective_date'] == item['effective_date']:
                            if old['received_at'] >= item['received_at']:
                                item.update(status='review', reason='已有同時間或較新的通知，需確認')
                            else:
                                old['status'] = 'superseded'
                                con.execute('UPDATE changes SET data=? WHERE id=?', (json.dumps(old, ensure_ascii=False), old['id']))
            except (ValueError, KeyError, TypeError):
                pass
        con.execute('INSERT INTO changes VALUES (?, ?)', (message_id, json.dumps(item, ensure_ascii=False)))
        return item


def undo(path, message_id):
    with connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT data FROM changes WHERE id=?', (message_id,)).fetchone()
        if not row:
            raise ValueError('找不到異動')
        item = json.loads(row[0])
        item['status'] = 'reverted'
        con.execute('UPDATE changes SET data=? WHERE id=?', (json.dumps(item, ensure_ascii=False), message_id))
        return item


def overlay(course, day):
    for change in course.get('_changes', []):
        if change['status'] == 'active' and change['effective_date'] == day.isoformat():
            return {**course, **change['patch'], 'temporary_change': change}
    return course


def attach(courses, path):
    items = changes(path)
    return [{**c, '_changes': [x for x in items if x['course_key'] == course_key(c)]} for c in courses]
