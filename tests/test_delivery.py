from __future__ import annotations

import asyncio
import csv
import io
import json
import sqlite3
import zipfile
from datetime import date

import httpx
import pytest

from app.ai import GeminiProvider, deterministic_analysis
from app.offline_import import import_strava_zip
from app.plans import plan_state
from app.workflow import Workflow
from test_workflow import add_run, client_for, form, setup
from tools.sqlite_backup import backup, verify


def archive(path, records):
    content = io.StringIO()
    writer = csv.writer(content)
    writer.writerow(['Activity ID', 'Activity Date', 'Activity Name', 'Activity Type', 'Distance', 'Moving Time', 'Elapsed Time'])
    for identity, distance in records:
        writer.writerow([identity, 'Sep 27, 2026, 08:00:00 AM', 'Private run name', 'Run', distance, 1200, 1400])
    with zipfile.ZipFile(path, 'w') as output:
        output.writestr('activities.csv', content.getvalue())


def test_batches_and_corrected_zip_preserve_links_and_invalidate_draft(tmp_path):
    settings, db, store = setup(tmp_path)
    first, second = tmp_path/'first.zip', tmp_path/'second.zip'
    archive(first, [(201, 3), (202, 3.5)])
    import_strava_zip(first, settings, db)
    state = plan_state(settings, db)
    store.link(state['next_session']['id'], 201)
    draft = store.propose(state['analysis'], 'deterministic', None, store.revision(), state['active_plan']['id'])
    archive(second, [(201, 4), (203, 3)])
    import_strava_zip(second, settings, db)
    assert db.activity_ids() == {201, 202, 203}
    assert store.sessions(completed_only=True)[0]['activity_id'] == 201
    assert plan_state(settings, db)['next_session']['position'] == 2
    with pytest.raises(ValueError, match='changed'):
        store.accept(draft)
    revision = store.revision()
    import_strava_zip(second, settings, db)
    assert store.revision() == revision


def test_chat_proposal_has_same_review_and_stale_rejection(tmp_path, monkeypatch):
    client, settings, db, store = client_for(tmp_path, monkeypatch, ai_provider='gemini', gemini_api_key='dummy-key')
    calls = []
    async def generated(*args, **kwargs):
        calls.append(kwargs)
        reply = {'answer': 'No run history is available. This is a gentle starting point for review.',
                 'proposal': deterministic_analysis([], 1).model_dump(mode='json')}
        return httpx.Response(200, request=httpx.Request('POST', 'https://example.invalid'),
                              json={'candidates': [{'content': {'parts': [{'text': json.dumps(reply)}]}}]})
    monkeypatch.setattr(GeminiProvider, '_request', generated)
    with client:
        old = plan_state(settings, db)['active_plan']['id']
        response = client.post('/coach/message', data=form(client, '/coach', message='Propose a gentle plan.'))
        assert response.status_code == 200 and len(calls) == 1
        draft = store.messages(1)[-1]['proposal_id']
        assert f'/plan/proposals/{draft}' in response.text
        assert store.active()['id'] == old and not store.sessions(completed_only=True)
        add_run(db)
        data = form(client, '/coach')
        assert client.post(f'/plan/proposals/{draft}/accept', data=data).status_code == 409
        assert store.active()['id'] == old


def test_actual_generation_deadline_preserves_conversation(tmp_path, monkeypatch):
    import app.coach as coach
    client, settings, db, store = client_for(tmp_path, monkeypatch, ai_provider='gemini', gemini_api_key='dummy-key')
    real_timeout = asyncio.timeout
    deadlines, calls = [], []
    def short_timeout(seconds):
        deadlines.append(seconds)
        return real_timeout(0.01)
    async def slow(*args, **kwargs):
        calls.append(1)
        await asyncio.sleep(0.1)
    monkeypatch.setattr(coach.asyncio, 'timeout', short_timeout)
    monkeypatch.setattr(GeminiProvider, '_request', slow)
    with client:
        old = plan_state(settings, db)['active_plan']['id']
        response = client.post('/coach/message', data=form(client, '/coach', message='What should I do next?'))
        assert response.status_code == 200
        assert deadlines == [30] and calls == [1]
        assert 'Gemini could not answer' in response.text
        assert len(store.messages(1)) == 2 and store.active()['id'] == old


def test_analysis_api_uses_review_csrf_and_duplicate_gate(tmp_path, monkeypatch):
    client, _, db, store = client_for(tmp_path, monkeypatch)
    with client:
        assert client.post('/analysis').status_code == 403
        data = form(client)
        old = store.active()['id']
        result = client.post('/analysis', data=data)
        assert result.status_code == 200 and result.json()['active_plan_changed'] is False
        assert store.plan(result.json()['proposal_id'])['status'] == 'proposal'
        assert store.active()['id'] == old
        assert client.post('/analysis', data=data).status_code == 409


def test_message_size_rejected_before_storage(tmp_path, monkeypatch):
    client, _, _, store = client_for(tmp_path, monkeypatch)
    with client:
        assert client.post('/coach/message', data=form(client, '/coach', message='x'*2001)).status_code == 422
        assert not store.conversations()


def test_malformed_unicode_csrf_is_rejected(tmp_path, monkeypatch):
    client, _, _, _ = client_for(tmp_path, monkeypatch)
    with client:
        data = form(client, csrf_token='é'*30)
        assert client.post('/coach/new', data=data).status_code == 403


def test_old_import_is_uncertainty_not_claimed_inactivity(tmp_path):
    settings, db, store = setup(tmp_path)
    add_run(db)
    from app.analysis import build_run_rows
    rows, _ = build_run_rows(db.all_activities(), settings)
    result = deterministic_analysis(rows, 1, as_of=date(2026, 11, 20))
    text = result.model_dump_json()
    assert 'Unuploaded runs are unknown' in text
    assert 'Returned after a break' not in text and 'Inactivity gap' not in text
    assert all(session.day == 'Session 1' for week in result.four_week_plan for session in week.sessions)


def test_snapshot_api_preserves_wal_committed_data_and_migration(tmp_path):
    settings, db, _ = setup(tmp_path)
    add_run(db)
    db.save_checkin(1, {'perceived_effort': 4, 'notes': 'private'})
    db.upsert_health_daily([{'date': '2026-09-20', 'steps': 1000}])
    db.save_token({'access_token': 'dummy', 'refresh_token': 'dummy', 'expires_at': 1})
    with db.connect() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('UPDATE activities SET distance_m=3500 WHERE id=1')
    snapshot = tmp_path/'snapshot.sqlite'
    backup(db.path, snapshot)
    verify(snapshot)
    with sqlite3.connect(snapshot) as conn:
        assert conn.execute('SELECT distance_m FROM activities WHERE id=1').fetchone()[0] == 3500
    with pytest.raises(FileExistsError):
        backup(db.path, snapshot)
    store = Workflow(db)
    store.initialize()
    store.initialize()
    assert db.get_token()['access_token'] == 'dummy'
    assert db.checkin_count() == 1 and db.health_daily()[0]['steps'] == 1000


def test_empty_dashboard_still_shows_next_session(tmp_path, monkeypatch):
    client, _, _, _ = client_for(tmp_path, monkeypatch)
    with client:
        text = client.get('/dashboard').text
        assert 'Up next · Session 1' in text
        assert 'No imported baseline is available' in text


def test_changed_active_plan_rejects_other_proposals(tmp_path):
    settings, db, store = setup(tmp_path)
    state = plan_state(settings, db)
    first = store.propose(state['analysis'], 'deterministic', None, store.revision(), state['active_plan']['id'])
    second = store.propose(state['analysis'], 'deterministic', None, store.revision(), state['active_plan']['id'])
    store.accept(first)
    with pytest.raises(ValueError, match='changed'):
        store.accept(second)


def test_storage_limit_counts_utf8_bytes(tmp_path, monkeypatch):
    import app.workflow as workflow
    _, _, store = setup(tmp_path)
    monkeypatch.setattr(workflow, 'CHAT_LIMIT', 100)
    older = store.new_conversation()
    store.append_message(older, 'user', 'é'*40)
    newer = store.new_conversation()
    store.append_message(newer, 'user', 'é'*40)
    assert not store.messages(older)
    assert sum(len(item['content'].encode('utf-8')) for item in store.messages(newer)) <= 100
