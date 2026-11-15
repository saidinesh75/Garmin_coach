"""Optional on-device AI selects among evidence-backed coaching cards.
No cloud service, arbitrary URL, or generated health claims.
"""
import json
import httpx


def prioritize(settings, analysis):
    cards = analysis['insights']
    if not settings.llm_model:
        return {'mode': 'rules', 'message': 'Transparent Python analysis. Local AI is optional and currently off.', 'focus_ids': [c['id'] for c in cards]}
    if not cards:
        return {'mode': 'insufficient_data', 'message': 'No evidence-backed cards to prioritize yet.', 'focus_ids': []}
    candidates = [{k: c[k] for k in ('id', 'title', 'observation', 'action', 'confidence')} for c in cards]
    try:
        # Fixed loopback destination; no external model provider or health data upload.
        with httpx.Client(timeout=90, trust_env=False) as client:
            response = client.post(f'http://127.0.0.1:{settings.llm_port}/api/generate', json={
                'model': settings.llm_model, 'stream': False,
                'format': {'type': 'object', 'properties': {'focus_ids': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['focus_ids']},
                'prompt': 'Select up to three coaching card IDs to highlight. Prefer coherent recovery signals and cautious actions. Return JSON with focus_ids only. Do not invent IDs. Cards:\n' + json.dumps(candidates),
                'options': {'temperature': 0}})
            response.raise_for_status()
            result = json.loads(response.json()['response'])
            ids = result['focus_ids']
            valid = {c['id'] for c in cards}
            if not isinstance(ids, list) or not ids or len(ids) > 3 or any(not isinstance(x, str) or x not in valid for x in ids) or len(set(ids)) != len(ids):
                raise ValueError()
            return {'mode': 'local_ai', 'model': settings.llm_model, 'focus_ids': ids,
                    'message': 'On-device AI selected these cards. All numbers, evidence and advice come from the audited Python layer.'}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {'mode': 'fallback', 'message': 'Local AI unavailable or returned invalid references. Showing Python analysis.', 'focus_ids': [c['id'] for c in cards]}
