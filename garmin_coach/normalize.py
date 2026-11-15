"""Documented Garmin DTO fields -> explicit units; invalid sentinels become null."""
from __future__ import annotations
import math


def number(value, low=0, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value if value >= low and (high is None or value <= high) else None


def normalize(resource, body):
    if body is None or body == {} or body == []:
        return {}
    if resource == 'body_battery':
        if not isinstance(body, list):
            raise ValueError('Body Battery response must be a list')
        return {}  # Intraday report preserved raw; daily summary is authoritative for extrema/wake value.
    if not isinstance(body, dict):
        raise ValueError('Expected a Garmin DTO object')
    if resource == 'summary':
        fields = {
            'steps': ('totalSteps', 0, None), 'resting_hr_bpm': ('restingHeartRate', 1, 250),
            'stress': ('averageStressLevel', 0, 100), 'body_battery_high': ('bodyBatteryHighestValue', 0, 100),
            'body_battery_low': ('bodyBatteryLowestValue', 0, 100),
            'body_battery_wake': ('bodyBatteryAtWakeTime', 0, 100),
            'body_battery_latest': ('bodyBatteryMostRecentValue', 0, 100),
            'body_battery_charged': ('bodyBatteryChargedValue', 0, None),
            'body_battery_drained': ('bodyBatteryDrainedValue', 0, None),
            'moderate_minutes': ('moderateIntensityMinutes', 0, None),
            'vigorous_minutes': ('vigorousIntensityMinutes', 0, None),
            'active_kcal': ('activeKilocalories', 0, None),
        }
        return {out: number(body.get(src), low, high) for out, (src, low, high) in fields.items()}
    if resource == 'sleep':
        seconds = number(body.get('sleepTimeSeconds'), 1, 86400)
        scores = body.get('sleepScores') or {}
        overall = scores.get('overall') or {}
        return {'sleep_hours': seconds / 3600 if seconds is not None else None,
                'sleep_score': number(overall.get('value'), 0, 100),
                **{out: number(body.get(src), 0, 86400) for out, src in {
                    'deep_sleep_seconds': 'deepSleepSeconds', 'rem_sleep_seconds': 'remSleepSeconds',
                    'light_sleep_seconds': 'lightSleepSeconds', 'awake_seconds': 'awakeSleepSeconds',
                    'nap_seconds': 'napTimeSeconds'}.items()}}
    if resource == 'hrv':
        summary = body.get('hrvSummary') or {}
        return {'hrv_ms': number(summary.get('lastNightAvg'), 1, 1000), 'hrv_status': summary.get('status')}
    if resource == 'heart_rate':
        # Resting HR comes from the daily summary to avoid competing sources.
        return {'min_hr_bpm': number(body.get('minHeartRate'), 1, 250),
                'max_hr_bpm': number(body.get('maxHeartRate'), 1, 250)}
    raise ValueError('Unknown resource')


def activity(item):
    if not isinstance(item, dict) or not isinstance(item.get('activityId'), int):
        raise ValueError('Activity ID is missing')
    start = item.get('startTimeLocal')
    if not isinstance(start, str) or len(start) < 10:
        raise ValueError('Activity local date is missing')
    kind = item.get('activityType') or {}
    return item['activityId'], start[:10], {
        'name': str(item.get('activityName') or 'Activity'), 'type': kind.get('typeKey', 'unknown'),
        'start_local': start, 'start_utc': item.get('startTimeGMT'),
        'duration_seconds': number(item.get('duration')),
        'distance_m': number(item.get('distance')), 'average_hr_bpm': number(item.get('averageHR'), 1, 250),
        'aerobic_effect': number(item.get('aerobicTrainingEffect'), 0, 5),
        'anaerobic_effect': number(item.get('anaerobicTrainingEffect'), 0, 5),
        'training_load': number(item.get('activityTrainingLoad')),
    }
