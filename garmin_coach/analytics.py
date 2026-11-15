from __future__ import annotations
from datetime import date, timedelta
from statistics import mean, median, pstdev

METRICS = {
    'sleep_hours': ('Sleep duration', 'h'), 'sleep_score': ('Sleep score', '/100'),
    'hrv_ms': ('Overnight HRV', 'ms'), 'resting_hr_bpm': ('Resting heart rate', 'bpm'),
    'stress': ('Daily stress', '/100'), 'body_battery_wake': ('Body Battery at wake', '/100'),
    'steps': ('Daily steps', 'steps'), 'body_battery_high': ('Body Battery high', '/100'),
    'body_battery_low': ('Body Battery low', '/100'),
}


def analyze(db, start, end, today):
    anchor = min(end, today - timedelta(days=1))
    recent_start = anchor - timedelta(days=6)
    base_start = anchor - timedelta(days=34)
    rows = db.days(min(start, base_start).isoformat(), end.isoformat())
    visible = [r for r in rows if start.isoformat() <= r['day'] <= end.isoformat()]
    series = {r['day']: r for r in rows}
    metrics, insights = {}, []
    for key, (label, unit) in METRICS.items():
        recent = [r for r in rows if recent_start.isoformat() <= r['day'] <= anchor.isoformat() and isinstance(r.get(key), (float, int))]
        base = [r for r in rows if base_start.isoformat() <= r['day'] < recent_start.isoformat() and isinstance(r.get(key), (float, int))]
        current = mean([r[key] for r in recent]) if recent else None
        baseline = median([r[key] for r in base]) if base else None
        enough = len(recent) >= 4 and len(base) >= 14
        delta = current - baseline if current is not None and baseline is not None else None
        metrics[key] = {'label': label, 'unit': unit, 'current': rounded(current), 'baseline': rounded(baseline),
                        'delta': rounded(delta), 'recent_n': len(recent), 'baseline_n': len(base),
                        'ready': enough, 'recent_start': recent_start.isoformat(), 'recent_end': anchor.isoformat(),
                        'baseline_start': base_start.isoformat(), 'baseline_end': (recent_start - timedelta(days=1)).isoformat(),
                        'latest_date': recent[-1]['day'] if recent else None,
                        'evidence': [{'day': r['day'], 'value': r[key], 'raw_id': r['sources'].get(key)} for r in recent + base]}
        if key not in ('sleep_hours', 'hrv_ms', 'resting_hr_bpm', 'stress', 'body_battery_wake', 'steps'):
            continue
        if enough:
            variability = pstdev([r[key] for r in base])
            floor = {'sleep_hours': .5, 'hrv_ms': max(5, baseline * .1), 'resting_hr_bpm': 3,
                     'stress': 7, 'body_battery_wake': 10, 'steps': max(1500, baseline * .2)}[key]
            changed = abs(delta) >= max(floor, variability)
            lower = delta < 0
            caution = changed and ((key in ('hrv_ms', 'sleep_hours', 'body_battery_wake') and lower) or (key in ('stress', 'resting_hr_bpm') and not lower))
            action = {
                'sleep_hours': 'Allow a consistent sleep opportunity and compare how rested you feel.',
                'hrv_ms': 'If you also feel tired, consider an easier session and reassess after a few nights.',
                'resting_hr_bpm': 'Check sleep, recent exertion and how you feel before choosing session intensity.',
                'stress': 'Try a short quiet break and review whether recent days felt unusually demanding.',
                'body_battery_wake': 'Use your perceived energy alongside this watch estimate to plan today.',
                'steps': 'Keep movement comfortable and realistic for your schedule; steps are not a fitness diagnosis.',
            }[key]
            insights.append({'id': key, 'title': label + (' shifted from your baseline' if changed else ' is near your baseline'),
                             'level': 'notice' if caution else 'neutral',
                             'observation': f'7-day mean {current:.1f} {unit}; prior 28-day median {baseline:.1f} {unit} ({len(recent)}/7 and {len(base)}/28 days available).',
                             'action': action, 'evidence_metric': key,
                             'confidence': 'moderate' if len(recent) >= 6 and len(base) >= 21 else 'limited',
                             'method': 'Exploratory change flag: absolute difference exceeds both baseline standard deviation and a metric-specific floor. These are app heuristics, not medical thresholds.'})
    activities = db.activities(start.isoformat(), end.isoformat())
    week_activities = db.activities(recent_start.isoformat(), anchor.isoformat())
    previous_activities = db.activities((recent_start - timedelta(days=7)).isoformat(), (recent_start - timedelta(days=1)).isoformat())
    with db.connect() as c:
        coverage = [dict(r) for r in c.execute('SELECT resource,day,status FROM checkpoints WHERE day BETWEEN ? AND ? ORDER BY day,resource', (start.isoformat(), end.isoformat()))]
        activity_days = {r['day'] for r in c.execute('SELECT day FROM checkpoints WHERE resource="activities" AND status="ok" AND day BETWEEN ? AND ?', ((recent_start - timedelta(days=7)).isoformat(), anchor.isoformat()))}
    training = {'recent_count': len(week_activities), 'recent_minutes': rounded(sum(a.get('duration_seconds') or 0 for a in week_activities) / 60),
                'previous_minutes': rounded(sum(a.get('duration_seconds') or 0 for a in previous_activities) / 60),
                'recent_distance_km': rounded(sum(a.get('distance_m') or 0 for a in week_activities) / 1000),
                'recent_load': rounded(sum(a.get('training_load') or 0 for a in week_activities)) if any(a.get('training_load') is not None for a in week_activities) else None,
                'coverage_days': len(activity_days), 'expected_days': 14,
                'coverage_complete': len(activity_days) == 14,
                'note': 'Recorded parent activities only; missing activity sync days are not rest days. Child legs excluded to avoid double counting. Watch training load is reported only when supplied.'}
    if training['coverage_complete']:
        insights.append({'id': 'workouts', 'title': 'Your recorded training week', 'level': 'neutral',
                         'observation': f"{len(week_activities)} activities, {training['recent_minutes']} min; preceding week {training['previous_minutes']} min.",
                         'action': 'Review duration alongside effort, sleep and soreness before adding volume.',
                         'confidence': 'limited', 'evidence_metric': None,
                         'evidence': [{'activity_id': a['id'], 'raw_id': a['source_id'], 'day': a['day']} for a in week_activities + previous_activities],
                         'method': 'Sum of recorded durations; no inferred training intensity or injury prediction.'})
    measured = [r for r in visible if any(r.get(k) is not None for k in METRICS)]
    latest = measured[-1]['day'] if measured else None
    stale = latest is not None and (today - date.fromisoformat(latest)).days > 2
    warnings = ['Wearable estimates can be noisy. This app offers general wellbeing ideas, not diagnosis or treatment.']
    if not insights:
        warnings.append('Not enough observations for a personal baseline: need at least 4 of 7 recent days and 14 of the preceding 28 days per metric.')
    if stale:
        warnings.append('Your readings are stale. Trends refer to the displayed dates and cannot establish today’s readiness.')
    if end >= today:
        warnings.append('Today may be incomplete; comparisons exclude today and use the last seven completed calendar days.')
    return {'start': start.isoformat(), 'end': end.isoformat(), 'anchor': anchor.isoformat(),
            'metrics': metrics, 'days': visible, 'activities': activities, 'training': training,
            'insights': insights, 'warnings': warnings, 'latest_date': latest, 'stale': stale,
            'coverage': coverage, 'days_with_readings': len(measured), 'requested_days': (end - start).days + 1,
            'methodology': 'Recent window: mean over 7 calendar days ending at anchor. Baseline: median over the preceding 28 calendar days, excluding the recent window. No gap filling or population-based readiness score.'}


def rounded(value):
    return round(value, 2) if value is not None else None
