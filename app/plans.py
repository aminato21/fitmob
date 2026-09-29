from __future__ import annotations

import json
from pathlib import Path

from app.ai import CoachAnalysis, deterministic_analysis, enforce_plan_safety
from app.analysis import build_run_rows
from app.workflow import Workflow


def plan_state(settings, database):
    store = Workflow(database)
    preferences = database.get_preferences()
    count = int(preferences.get('preferred_sessions_per_week', settings.preferred_sessions_per_week))
    goal = str(preferences.get('training_goal', settings.training_goal))
    rows, _ = build_run_rows(database.all_activities(), settings.with_preferences(preferences))
    active = store.active()
    if not active:
        analysis = deterministic_analysis(rows, count, goal).model_dump(mode='json')
        source, model = 'deterministic', None
        legacy = Path(settings.export_dir) / 'latest_coach_result.json'
        if legacy.exists():
            try:
                result = json.loads(legacy.read_text('utf-8'))
                candidate = CoachAnalysis.model_validate(result['analysis'])
                checked, _ = enforce_plan_safety(candidate, deterministic_analysis(rows, count, goal), rows, count)
                analysis = checked.model_dump(mode='json')
                source, model = result.get('provider_used', 'deterministic'), result.get('model')
            except (ValueError, KeyError, OSError, TypeError):
                pass
        active = store.ensure_active(analysis, source, model)
    sessions = store.sessions(active['id'])
    pending = [session for session in sessions if session['activity_id'] is None]
    return {
        'active_plan': active, 'analysis': active['analysis'], 'plan_sessions': sessions,
        'next_session': pending[0] if pending else None,
        'completed_history': store.sessions(completed_only=True),
        'pending_proposals': store.proposals(),
        'sessions_per_week': count, 'training_goal': goal,
        'plan_source': active['source'], 'plan_model': active['model'],
        'plan_stale': store.is_stale(active),
        'safety_adjustments': active['adjustments'],
        'latest_run_date': rows[-1]['_date'].isoformat() if rows else None,
        'latest_upload': store.latest_import(),
    }
