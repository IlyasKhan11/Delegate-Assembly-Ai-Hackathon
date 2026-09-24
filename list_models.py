"""List models from the selected provider without making a completion request."""
from engine import client
from ai_config import get_provider, provider_key

if not provider_key():
    raise SystemExit('Add the selected provider API key to .env first.')

try:
    models = client.models.list()
    print(f'Available {get_provider()} models:')
    for model in sorted(models.data, key=lambda item: item.id):
        print(f'  {model.id}')
except Exception as error:
    # Do not print SDK error bodies, which could contain credential information.
    raise SystemExit(f'Model listing failed ({type(error).__name__}). Check your key and provider connection.')
