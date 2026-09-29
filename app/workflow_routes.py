from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.ai import deterministic_analysis, run_ai_analysis
from app.coach import answer_question
from app.plans import plan_state
from app.security import require_csrf
from app.web import _base, _state, templates
from app.analysis import build_run_rows
from app.workflow import Workflow

router = APIRouter()
csrf = [Depends(require_csrf)]


def store_for(request):
    settings, database = _state(request)
    return settings, database, Workflow(database)


def form_id():
    return secrets.token_urlsafe(24)


def claim(store, request_id, kind):
    if not 16 <= len(request_id) <= 64 or not store.claim(request_id, kind):
        raise HTTPException(409, 'This request was already submitted or another generation is running. Reload to review the result.')


async def generate_proposal(request: Request, local=False):
    await require_csrf(request)
    form = await request.form()
    settings, database, store = store_for(request)
    state = plan_state(settings, database)
    claim(store, str(form.get('request_id', '')), 'plan')
    revision = store.revision()
    try:
        if local:
            rows, _ = build_run_rows(database.all_activities(), settings.with_preferences(database.get_preferences()))
            result = {'analysis': deterministic_analysis(rows, state['sessions_per_week'], state['training_goal']).model_dump(mode='json'), 'provider_used': 'deterministic'}
        else:
            result = await run_ai_analysis(settings, database)
        source = 'deterministic' if result['provider_used'] in {'none', 'deterministic'} else result['provider_used']
        proposal_id = store.propose(result['analysis'], source, result.get('model'), revision,
                                    state['active_plan']['id'], result.get('safety_adjustments', []),
                                    note=result.get('fallback_reason'))
        store.finish(str(form['request_id']), proposal_id)
        return proposal_id
    except BaseException:
        store.finish(str(form['request_id']), -1)
        raise


async def create_proposal(request: Request, local=False):
    proposal_id = await generate_proposal(request, local)
    return RedirectResponse(f'/plan/proposals/{proposal_id}', status_code=303)


@router.post('/plan/local', dependencies=csrf)
async def local_proposal(request: Request):
    return await create_proposal(request, local=True)


@router.get('/plan/proposals/{proposal_id}')
def review_proposal(request: Request, proposal_id: int):
    settings, database, store = store_for(request)
    proposal = store.plan(proposal_id)
    if not proposal or proposal['status'] != 'proposal':
        raise HTTPException(404, 'Proposal not found.')
    active = store.active()
    stale = store.is_stale(proposal, active['id'] if active else None)
    return templates.TemplateResponse(request=request, name='proposal.html', context=_base(request, 'plan', proposal=proposal, stale=stale))


@router.post('/plan/proposals/{proposal_id}/accept', dependencies=csrf)
def accept_proposal(request: Request, proposal_id: int):
    _, _, store = store_for(request)
    try:
        store.accept(proposal_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse('/plan?accepted=1', status_code=303)


@router.get('/plan/matches')
def review_matches(request: Request):
    settings, database, store = store_for(request)
    state = plan_state(settings, database)
    linked_ids = {row['activity_id'] for row in state['completed_history']}
    with database.connect() as conn:
        activities = [dict(row) for row in conn.execute('SELECT id,name,start_date_local,distance_m FROM activities ORDER BY start_date_local DESC') if row['id'] not in linked_ids]
    return templates.TemplateResponse(request=request, name='matches.html', context=_base(request, 'plan', activities=activities, **state))


@router.post('/plan/sessions/{session_id}/match', dependencies=csrf)
def match_session(request: Request, session_id: int, activity_id: int = Form(...)):
    _, _, store = store_for(request)
    try:
        store.link(session_id, activity_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse('/plan/matches?matched=1', status_code=303)


@router.post('/plan/sessions/{session_id}/unmatch', dependencies=csrf)
def unmatch_session(request: Request, session_id: int):
    _, _, store = store_for(request)
    store.unlink(session_id)
    return RedirectResponse('/plan/matches', status_code=303)


@router.get('/coach')
def coach_page(request: Request, conversation_id: int | None = None):
    settings, database, store = store_for(request)
    conversations = store.conversations()
    if conversation_id is None and conversations:
        conversation_id = conversations[0]['id']
    if conversation_id is not None and not any(item['id'] == conversation_id for item in conversations):
        raise HTTPException(404, 'Conversation not found.')
    messages = store.messages(conversation_id) if conversation_id else []
    last_question = next((item['content'] for item in reversed(messages) if item['role'] == 'user'), None)
    return templates.TemplateResponse(request=request, name='coach.html', context=_base(request, 'coach',
        conversations=conversations, conversation_id=conversation_id,
        messages=messages, last_question=last_question,
        request_id=form_id(), key_configured=bool(settings.gemini_api_key), **plan_state(settings, database)))


@router.post('/coach/new', dependencies=csrf)
def new_conversation(request: Request):
    _, _, store = store_for(request)
    return RedirectResponse(f'/coach?conversation_id={store.new_conversation()}', status_code=303)


@router.post('/coach/{conversation_id}/delete', dependencies=csrf)
def delete_conversation(request: Request, conversation_id: int):
    _, _, store = store_for(request)
    store.delete_conversation(conversation_id)
    return RedirectResponse('/coach', status_code=303)


@router.post('/coach/message', dependencies=csrf)
async def send_message(request: Request, message: str = Form(..., min_length=1, max_length=2000),
                       request_id: str = Form(..., min_length=16, max_length=64),
                       conversation_id: int | None = Form(None)):
    settings, database, store = store_for(request)
    if not message.strip():
        raise HTTPException(422, 'Write a question first.')
    if conversation_id and not any(item['id'] == conversation_id for item in store.conversations()):
        raise HTTPException(404, 'Conversation not found.')
    state = plan_state(settings, database)
    claim(store, request_id, 'chat')
    conversation_id = conversation_id or store.new_conversation()
    revision = store.revision()
    try:
        store.append_message(conversation_id, 'user', message.strip())
        reply, adjustments, failure = await answer_question(settings, database, store, conversation_id)
        proposal_id = None
        if reply.proposal:
            proposal_id = store.propose(reply.proposal.model_dump(mode='json'), 'gemini', settings.ai_model,
                                        revision, state['active_plan']['id'], adjustments)
        store.append_message(conversation_id, 'assistant', reply.answer, proposal_id)
        store.finish(request_id, conversation_id)
        return RedirectResponse(f'/coach?conversation_id={conversation_id}', status_code=303)
    except BaseException:
        store.finish(request_id, -1)
        raise


@router.post('/coach/{conversation_id}/retry', dependencies=csrf)
async def retry_message(request: Request, conversation_id: int, request_id: str = Form(..., min_length=16, max_length=64)):
    _, _, store = store_for(request)
    message = next((item['content'] for item in reversed(store.messages(conversation_id)) if item['role'] == 'user'), None)
    if not message:
        raise HTTPException(404, 'No question is available to retry.')
    return await send_message(request, message=message, request_id=request_id, conversation_id=conversation_id)
