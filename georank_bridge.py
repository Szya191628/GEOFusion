"""Minimal complete() interface for the licensed GEORank keyword service."""
import os
import httpx

class AIClient:
    async def complete(self, system, user, temperature=0.45, provider_override=None):
        base, key, model = [os.environ.get(k) for k in ('MODEL_BASE_URL', 'MODEL_API_KEY', 'MODEL_NAME')]
        if not all((base, key, model)):
            raise RuntimeError('Model not configured')
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
            response = await client.post(base.rstrip('/') + '/chat/completions',
                headers={'Authorization': 'Bearer ' + key}, json={'model': model,
                'temperature': temperature, 'max_tokens': 5000,
                'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]})
            response.raise_for_status()
            return response.json()['choices'][0]['message']['content']

ai_client = AIClient()
