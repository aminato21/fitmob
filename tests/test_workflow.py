from __future__ import annotations

import asyncio
import io
import json
import re
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.ai import GeminiProvider, deterministic_analysis, enforce_plan_safety
from app.auth import hash_password
from app.coach import answer_question, context_messages, grounded_context
from app.config import Settings
from app.db import Database
from app.plans import plan_state
from app.workflow import Workflow
import app.workflow as workflow_module


def setup(tmp_path, **overrides):
    settings = Settings(database_path=tmp_path/'db.sqlite', export_dir=tmp_path/'exports', _env_file=None, **overrides)
    db = Database(settings)
    db.initialize()
    store = Workflow(db)
    store.initialize()
    return settings, db, store


def add_run(db, activity_id=1, distance=3000):
    db.upsert_activity({'id':activity_id, 'start_date_local':'2026-09-20T18:00:00', 'sport_type':'Run',
                        'distance':distance, 'moving_time':1200, 'elapsed_time':1400,
                        'name':'My exact private route', 'description':'Private note',
                        'start_latlng':[33.123456,-7.654321], 'map':{'summary_polyline':'secret-route'}}, None, [])


def proposal(settings, db, store):
    state = plan_state(settings, db)
    analysis = deterministic_analysis([], state['sessions_per_week'], state['training_goal']).model_dump(mode='json')
    return store.propose(analysis, 'deterministic', None, store.revision(), state['active_plan']['id'])


def client_for(tmp_path, monkeypatch, **overrides):
    settings, db, store = setup(tmp_path, **overrides)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'database', db)
    return TestClient(main.app), settings, db, store


def form(client, path='/plan', request_id='test-request-id-00001', **values):
    response = client.get(path)
    assert response.status_code == 200
    return {'csrf_token':client.cookies['runstead_csrf'], 'request_id':request_id, **values}


def test_next_session_advances_only_after_confirmation(tmp_path):
    settings, db, store = setup(tmp_path)
    add_run(db)
    state = plan_state(settings, db)
    assert len(state['plan_sessions']) == 4
    assert state['next_session']['position'] == 1
    add_run(db, 2)
    assert plan_state(settings, db)['next_session']['position'] == 1
    store.link(state['next_session']['id'], 1)
    assert plan_state(settings, db)['next_session']['position'] == 2
    with pytest.raises(ValueError):
        store.link(state['plan_sessions'][1]['id'], 1)
    store.unlink(state['next_session']['id'])
    assert plan_state(settings, db)['next_session']['position'] == 1


def test_replacement_preserves_completed_history_and_undo(tmp_path):
    settings, db, store = setup(tmp_path)
    add_run(db)
    first = plan_state(settings, db)['next_session']['id']
    store.link(first, 1)
    old = store.active()['id']
    new = proposal(settings, db, store)
    assert store.active()['id'] == old
    store.accept(new)
    assert store.plan(old)['status'] == 'archived'
    assert store.sessions(completed_only=True)[0]['activity_id'] == 1
    assert plan_state(settings, db)['next_session']['position'] == 1
    with pytest.raises(ValueError):
        store.accept(new)
    store.unlink(first)
    assert not store.sessions(completed_only=True)


@pytest.mark.parametrize('kind', ['activity', 'checkin', 'health', 'preferences', 'completion'])
def test_changed_content_invalidates_proposal_without_count_change(tmp_path, kind):
    settings, db, store = setup(tmp_path)
    add_run(db)
    db.save_checkin(1, {'notes':'before'})
    db.upsert_health_daily([{'date':'2026-09-20','steps':1}])
    db.save_preferences({'training_goal':'consistency'})
    draft = proposal(settings, db, store)
    if kind == 'activity':
        add_run(db, distance=3500)
    elif kind == 'checkin':
        db.save_checkin(1, {'notes':'after'})
    elif kind == 'health':
        db.upsert_health_daily([{'date':'2026-09-20','steps':2}])
    elif kind == 'preferences':
        db.save_preferences({'training_goal':'build_endurance'})
    else:
        store.link(plan_state(settings, db)['next_session']['id'], 1)
    with pytest.raises(ValueError, match='changed'):
        store.accept(draft)


def test_duplicate_import_does_not_change_revision_or_links(tmp_path):
    from test_offline_import import make_archive
    from app.offline_import import import_strava_zip
    settings, db, store = setup(tmp_path)
    archive=tmp_path/'strava.zip'
    make_archive(archive)
    import_strava_zip(archive, settings, db)
    state=plan_state(settings,db)
    store.link(state['next_session']['id'],987654321)
    revision=store.revision()
    import_strava_zip(archive,settings,db)
    assert store.revision()==revision
    assert len(store.sessions(completed_only=True))==1
    assert store.latest_import()


def test_complete_sequence_and_expired_proposal(tmp_path):
    settings,db,store=setup(tmp_path)
    sessions=plan_state(settings,db)['plan_sessions']
    for index,session in enumerate(sessions,1):
        add_run(db,index)
        store.link(session['id'],index)
    assert plan_state(settings,db)['next_session'] is None
    draft=proposal(settings,db,store)
    with db.connect() as conn:
        conn.execute('UPDATE plan_versions SET created_at=? WHERE id=?', ((datetime.now(timezone.utc)-timedelta(days=8)).isoformat(),draft))
    with pytest.raises(ValueError):
        store.accept(draft)


def test_migration_is_additive_and_repeatable(tmp_path):
    settings,db,store=setup(tmp_path)
    add_run(db)
    salt,password,iterations=hash_password('owner-pass')
    db.create_user('owner',salt,password,iterations)
    store.initialize()
    store.initialize()
    db.initialize()
    assert db.user_exists('owner') and db.activity_ids()=={1}
    with db.connect() as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_csrf_matching_acceptance_and_duplicate_generation(tmp_path,monkeypatch):
    client,settings,db,store=client_for(tmp_path,monkeypatch)
    with client:
        add_run(db)
        first=plan_state(settings,db)['next_session']['id']
        assert client.post(f'/plan/sessions/{first}/match',data={'activity_id':1}).status_code==403
        data=form(client,activity_id=1)
        assert client.post(f'/plan/sessions/{first}/match',data=data).status_code==200
        assert 'Up next · Session 2' in client.get('/dashboard').text
        data=form(client,request_id='draft-request-id-00001')
        response=client.post('/plan/local',data=data,follow_redirects=False)
        assert response.status_code==303
        assert client.post('/plan/local',data=data).status_code==409
        draft=int(response.headers['location'].split('/')[-1])
        old=store.active()['id']
        assert 'Review your proposal' in client.get(response.headers['location']).text
        assert store.active()['id']==old
        assert client.post(f'/plan/proposals/{draft}/accept',data=data).status_code==200
        assert store.active()['id']==draft
        assert client.get('/coach').headers['cache-control']=='no-store, private'


def test_authentication_guards_new_routes(tmp_path,monkeypatch):
    client,settings,db,store=client_for(tmp_path,monkeypatch,auth_enabled=True,auth_secret_key='test-auth-secret')
    salt,password,iterations=hash_password('owner-pass')
    db.create_user('owner',salt,password,iterations)
    with client:
        assert client.get('/coach').status_code==401
        assert client.post('/plan/local',data={'request_id':'a'*20}).status_code==401
        assert client.post('/login',data={'username':'owner','password':'owner-pass'}).status_code==200
        assert client.get('/coach').status_code==200
        assert client.post('/coach/new').status_code==403
        assert client.post('/coach/new',data=form(client,'/coach')).status_code==200


def test_chat_unavailable_saved_safe_html_retry_and_delete(tmp_path,monkeypatch):
    client,settings,db,store=client_for(tmp_path,monkeypatch)
    with client:
        data=form(client,'/coach',message='<script>alert(1)</script> I ran yesterday but have not uploaded it.')
        response=client.post('/coach/message',data=data)
        assert response.status_code==200
        assert '&lt;script&gt;' in response.text and '<script>alert(1)</script>' not in response.text
        assert 'Runs you have not uploaded are unknown' in response.text
        assert len(store.messages(1))==2
        assert client.post('/coach/message',data=data).status_code==409
        assert not store.sessions(completed_only=True)
        assert client.post('/coach/1/retry',data=form(client,'/coach',request_id='retry-request-id-00001')).status_code==200
        assert len(store.messages(1))==4
        assert client.post('/coach/1/delete',data=form(client,'/coach')).status_code==200
        assert not store.messages(1)


def test_grounded_chat_excludes_private_source_data_and_proposal_is_separate(tmp_path):
    settings,db,store=setup(tmp_path,ai_provider='gemini',gemini_api_key='test-key')
    add_run(db)
    db.save_checkin(1,{'notes':'Private injury note'})
    plan_state(settings,db)
    conversation=store.new_conversation()
    store.append_message(conversation,'user','Propose a consistency plan.')
    seen=[]
    def handler(request):
        text=request.content.decode()
        seen.append(text)
        for private in ('My exact private route','Private note','Private injury note','secret-route','33.123456','test-key'):
            assert private not in text
        assert 'active_plan' in text and 'unuploaded_runs_are_unknown' in text
        result={'answer':'Based on the imported summary, keep the session comfortable.', 'proposal':deterministic_analysis([],1,'consistency').model_dump(mode='json')}
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':json.dumps(result)}]}}]})
    old=store.active()['id']
    reply,adjustments,failure=asyncio.run(answer_question(settings,db,store,conversation,httpx.MockTransport(handler)))
    assert len(seen)==1 and reply.proposal is not None and failure is None
    assert store.active()['id']==old


@pytest.mark.parametrize('status', [429,404,500])
def test_chat_provider_errors_do_not_cascade_or_mutate_plan(tmp_path,status):
    settings,db,store=setup(tmp_path,ai_provider='gemini',gemini_api_key='test-key')
    plan_state(settings,db)
    conversation=store.new_conversation()
    store.append_message(conversation,'user','What next?')
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(status,json={'error':'provider failure'})
    reply,_,failure=asyncio.run(answer_question(settings,db,store,conversation,httpx.MockTransport(handler)))
    assert len(calls)==1 and failure and reply.proposal is None


def test_invalid_and_timeout_chat_leave_local_plan_available(tmp_path,monkeypatch):
    settings,db,store=setup(tmp_path,ai_provider='gemini',gemini_api_key='test-key')
    plan_state(settings,db)
    conv=store.new_conversation()
    store.append_message(conv,'user','What next?')
    response=httpx.MockTransport(lambda request:httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'not-json'}]}}]}))
    assert asyncio.run(answer_question(settings,db,store,conv,response))[2]
    async def timeout(*args,**kwargs):
        raise TimeoutError()
    monkeypatch.setattr(GeminiProvider,'_request',timeout)
    assert asyncio.run(answer_question(settings,db,store,conv))[2]
    assert store.active()


def test_history_context_and_storage_caps(tmp_path,monkeypatch):
    messages=[{'role':'user' if i%2==0 else 'assistant','content':str(i)*1000} for i in range(40)]
    context=context_messages(messages)
    assert len(context)<=16 and sum(len(x['content']) for x in context)<=12000
    _,db,store=setup(tmp_path)
    monkeypatch.setattr(workflow_module,'CHAT_LIMIT',100)
    old=store.new_conversation()
    store.append_message(old,'user','a'*80)
    current=store.new_conversation()
    store.append_message(current,'user','b'*80)
    assert not store.messages(old) and store.messages(current)
    store.append_message(current,'assistant','c'*80)
    assert sum(len(x['content'].encode()) for x in store.messages(current))<=100


def test_generation_gate_blocks_concurrent_distinct_submissions(tmp_path):
    _,db,store=setup(tmp_path)
    assert store.claim('request-one','chat')
    assert not store.claim('request-two','plan')
    store.finish('request-one',1)
    assert store.claim('request-two','plan')
    assert not store.claim('request-one','chat')


def test_guardrails_check_next_week_and_all_stages(tmp_path):
    base=deterministic_analysis([],1,'consistency')
    unsafe=base.model_copy(deep=True)
    unsafe.next_week.sessions[0].guidance='All-out sprint at max effort'
    checked,reasons=enforce_plan_safety(unsafe,base,[],1)
    assert reasons and checked.next_week.sessions[0].guidance==base.next_week.sessions[0].guidance


def test_backup_contains_consistent_verified_sqlite(tmp_path,monkeypatch):
    client,settings,db,store=client_for(tmp_path,monkeypatch)
    with client:
        add_run(db)
        plan_state(settings,db)
        response=client.get('/api/backup')
        assert response.status_code==200
        archive=zipfile.ZipFile(io.BytesIO(response.content))
        restored=tmp_path/'restored.sqlite'
        restored.write_bytes(archive.read('db.sqlite'))
        with sqlite3.connect(restored) as conn:
            assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert conn.execute('SELECT COUNT(*) FROM activities').fetchone()[0]==1
            assert conn.execute('SELECT COUNT(*) FROM plan_versions').fetchone()[0]==1
