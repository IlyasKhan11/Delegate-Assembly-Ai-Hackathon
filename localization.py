"""Shared, offline UI and guardrail translations; no translation API calls."""
import json
from pathlib import Path

MESSAGES = json.loads((Path(__file__).parent / 'frontend' / 'locales.json').read_text())


def translate(language, key, **values):
    if language == 'Portuguese':
        language = 'Portuguese (Brazil)'
    template = MESSAGES.get(language, MESSAGES['English'])[key]
    return template.format(**values)


# Currency symbols for the units the rule extractor returns. Anything not
# listed is shown after the number ("3000 PKR") rather than guessing a symbol.
CURRENCY_SYMBOLS = {
    "usd": "$", "dollar": "$", "dollars": "$", "us$": "$", "$": "$",
    "brl": "R$", "real": "R$", "reais": "R$", "r$": "R$",
    "eur": "€", "euro": "€", "euros": "€", "€": "€",
    "gbp": "£", "pound": "£", "pounds": "£", "£": "£",
    # South Asian currencies, where amounts are often spoken as lakh and crore.
    "pkr": "Rs", "rs": "Rs", "rupee": "Rs", "rupees": "Rs", "₨": "Rs",
    "inr": "₹", "₹": "₹",
}


def money(amount, unit=None):
    """Formats an amount in its own currency: $20, R$50, or '3000 PKR'.

    A Brazilian user setting a limit in reais should not be shown dollars.
    """
    unit = str(unit or "usd").strip()
    symbol = CURRENCY_SYMBOLS.get(unit.lower())
    return f"{symbol}{amount:g}" if symbol else f"{amount:g} {unit}"


def model_language(language):
    return 'Brazilian Portuguese (pt-BR)' if language in ('Portuguese', 'Portuguese (Brazil)') else language
