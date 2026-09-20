"""Local single-user Gmail read-only connector and Moodle polling."""
import base64
import json
import os
import threading
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path

from bs4 import BeautifulSoup
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials

from commute_agent.tools.course_mail import classify_course_mail, parse_moodle_time_change
from commute_agent.tools.schedule_changes import changes, record

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
ROOT = Path(__file__).resolve().parents[2] / '.gmail-local'
LOCK = threading.Lock()
STATUS = {'state': 'disconnected', 'last_sync': None, 'error': None}


def credentials_path():
    return Path(os.getenv('GMAIL_CLIENT_FILE', str(ROOT / 'client.json')))


def token_path():
    return ROOT / 'token.json'


def status():
    return {**STATUS, 'configured': credentials_path().is_file(),
            'connected': token_path().is_file(), 'poll_seconds': 60}


def authorize():
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not credentials_path().is_file():
        raise ValueError('請先放入 Gmail 桌面應用程式 OAuth client.json（詳見 docs/gmail-setup.md）')
    if not LOCK.acquire(blocking=False):
        raise ValueError('同步或授權正在進行')
    def run():
        try:
            STATUS.update(state='authorizing', error=None)
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path()), SCOPES)
            creds = flow.run_local_server(host='127.0.0.1', port=0, timeout_seconds=180,
                                          success_message='Gmail connected. You may close this window.',
                                          prompt='select_account consent')
            ROOT.mkdir(parents=True, exist_ok=True)
            token_path().write_text(creds.to_json(), encoding='utf-8')
            STATUS.update(state='connected')
        except Exception as exc:
            STATUS.update(state='error', error='授權未完成：' + type(exc).__name__)
        finally:
            LOCK.release()
    threading.Thread(target=run, daemon=True).start()


def body_text(part):
    if part.get('filename'):
        return ''
    mime = part.get('mimeType', '')
    if mime in ('text/plain', 'text/html'):
        raw = part.get('body', {}).get('data', '')
        value = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)).decode('utf-8', errors='replace')
        return BeautifulSoup(value, 'html.parser').get_text(' ', strip=True) if mime == 'text/html' else value
    parts = part.get('parts', [])
    if mime == 'multipart/alternative':
        plain = next((p for p in parts if p.get('mimeType') == 'text/plain'), None)
        if plain:
            return body_text(plain)
    return '\n'.join(body_text(p) for p in parts)


def fetch_messages(session):
    base = 'https://gmail.googleapis.com/gmail/v1/users/me/messages'
    page = None
    while True:
        params = {'q': 'from:noreply@ncku.edu.tw newer_than:120d', 'maxResults': 100}
        if page:
            params['pageToken'] = page
        response = session.get(base, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        yield from payload.get('messages', [])
        page = payload.get('nextPageToken')
        if not page:
            break


def process_message(path, message):
    """Store one Moodle message as an active overlay or a review item.

    A local-model outage must never prevent later explicit notices from being
    handled.  Unreadable notices are therefore retained for review instead of
    aborting the whole mailbox poll.
    """
    payload = message['payload']
    headers = {h['name'].lower(): h['value'] for h in payload.get('headers', [])}
    sender = parseaddr(headers.get('from', ''))[1].lower()
    subject, body = headers.get('subject', ''), body_text(payload)
    if sender != 'noreply@ncku.edu.tw':
        return None
    if 'moodle' not in (headers.get('from', '') + body).lower() and '討論區' not in body:
        return None
    mail = parse_moodle_time_change(subject, body)
    if mail is None:
        mail = classify_course_mail(subject, body)
    if mail.get('status') != 'ok':
        mail = {
            'status': 'ok', 'kind': 'info', 'course': None, 'course_code': None,
            'new_location': None, 'new_time': None, 'effective_date': None,
            'importance': 'normal', 'summary': '無法自動判讀，等待確認',
            'confidence': 0.0, 'source': 'moodle_unparsed',
        }
    return record(path, 'gmail:' + message['id'], subject, mail, message.get('internalDate', 0))


def sync(path):
    if not token_path().is_file():
        return status()
    if not LOCK.acquire(blocking=False):
        return status()
    try:
        STATUS.update(state='syncing', error=None)
        creds = Credentials.from_authorized_user_file(str(token_path()), SCOPES)
        if not creds.valid:
            creds.refresh(Request())
            token_path().write_text(creds.to_json(), encoding='utf-8')
        known = {c['id'] for c in changes(path)}
        with AuthorizedSession(creds) as session:
            messages = []
            for item in fetch_messages(session):
                if 'gmail:' + item['id'] in known:
                    continue
                response = session.get('https://gmail.googleapis.com/gmail/v1/users/me/messages/' + item['id'],
                                       params={'format': 'full'}, timeout=30)
                response.raise_for_status()
                messages.append(response.json())
            for message in sorted(messages, key=lambda m: int(m.get('internalDate', 0))):
                process_message(path, message)
        STATUS.update(state='connected', last_sync=datetime.now().isoformat(), error=None)
    except Exception as exc:
        STATUS.update(state='error', error='收信未完成（' + type(exc).__name__ + '），請確認授權、網路與本機模型')
    finally:
        LOCK.release()
    return status()
