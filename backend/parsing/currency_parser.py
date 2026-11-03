from __future__ import annotations

import re

from pydantic import BaseModel, Field

CURRENCY_ALIASES = {
    "$": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "usd": "USD",
    "us dollar": "USD",
    "us dollars": "USD",
    "₹": "INR",
    "rupee": "INR",
    "rupees": "INR",
    "rs": "INR",
    "rs.": "INR",
    "inr": "INR",
    "£": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "gbp": "GBP",
    "€": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "eur": "EUR",
    "¥": "JPY",
    "yen": "JPY",
    "jpy": "JPY",
    "cad": "CAD",
    "aud": "AUD",
    "chf": "CHF",
}
SYMBOLS = "$₹£€¥"
WORDS = sorted((key for key in CURRENCY_ALIASES if key not in SYMBOLS), key=len, reverse=True)
WEAK_DESTINATION_HINTS = {
    "india": "INR",
    "delhi": "INR",
    "new delhi": "INR",
    "mumbai": "INR",
    "chennai": "INR",
    "london": "GBP",
    "england": "GBP",
    "united kingdom": "GBP",
    "paris": "EUR",
    "rome": "EUR",
    "berlin": "EUR",
    "madrid": "EUR",
    "barcelona": "EUR",
    "new york": "USD",
    "new york city": "USD",
    "chicago": "USD",
    "boston": "USD",
    "los angeles": "USD",
}


class CurrencyResult(BaseModel):
    amount: float | None = Field(default=None, gt=0)
    currency: str | None = None
    source_text: str
    confidence: float = Field(ge=0, le=1)
    explicit: bool = False
    needs_clarification: bool = False
    alternatives: list[str] = Field(default_factory=list)


def _amount(value: str, multiplier: str | None = None) -> float:
    parsed = float(value.replace(",", ""))
    if multiplier and multiplier.lower() == "k":
        parsed *= 1000
    return parsed


def parse_currency(text: str, destination: str | None = None) -> CurrencyResult:
    """Parse an explicitly stated budget without guessing its currency.

    Destination is used only to order clarification choices. It never assigns a
    currency by itself.
    """
    source = " ".join(text.strip().split())
    lowered = source.casefold()
    if re.search(
        r"(?:budget\s*(?:is|of)?\s*|[$₹£€¥]\s*)-\s*\d|-[\d,]+\s*(?:rupees|inr|usd|dollars|eur|gbp)\b", lowered
    ):
        raise ValueError("Budget must be positive")
    amount_pattern = r"(?P<amount>\d[\d,]*(?:\.\d{1,2})?)\s*(?P<multiplier>k)?"

    symbol_before = re.search(rf"(?P<token>[{re.escape(SYMBOLS)}])\s*{amount_pattern}", source, re.IGNORECASE)
    if symbol_before:
        token = symbol_before.group("token")
        return CurrencyResult(
            amount=_amount(symbol_before.group("amount"), symbol_before.group("multiplier")),
            currency=CURRENCY_ALIASES[token],
            source_text=symbol_before.group(0),
            confidence=1.0,
            explicit=True,
        )

    word_pattern = "|".join(re.escape(word) for word in WORDS)
    word_after = re.search(rf"{amount_pattern}\s*(?P<token>{word_pattern})\b", lowered, re.IGNORECASE)
    if word_after:
        token = word_after.group("token").casefold().rstrip(".")
        return CurrencyResult(
            amount=_amount(word_after.group("amount"), word_after.group("multiplier")),
            currency=CURRENCY_ALIASES[token],
            source_text=source[word_after.start() : word_after.end()],
            confidence=0.99,
            explicit=True,
        )

    word_before = re.search(rf"(?P<token>{word_pattern})\s*{amount_pattern}", lowered, re.IGNORECASE)
    if word_before:
        token = word_before.group("token").casefold().rstrip(".")
        return CurrencyResult(
            amount=_amount(word_before.group("amount"), word_before.group("multiplier")),
            currency=CURRENCY_ALIASES[token],
            source_text=source[word_before.start() : word_before.end()],
            confidence=0.99,
            explicit=True,
        )

    budget_phrase = re.search(rf"\bbudget\s*(?:is|of|around|about|:)??\s*{amount_pattern}", lowered)
    ambiguous = budget_phrase or re.fullmatch(
        rf"(?:about|around|under|approximately|budget(?:\s+(?:is|of|around))?)?\s*{amount_pattern}",
        lowered,
        re.IGNORECASE,
    )
    if ambiguous:
        hint = WEAK_DESTINATION_HINTS.get((destination or "").casefold())
        alternatives = [item for item in (hint, "INR", "USD", "GBP", "EUR") if item]
        alternatives = list(dict.fromkeys(alternatives))
        return CurrencyResult(
            amount=_amount(ambiguous.group("amount"), ambiguous.group("multiplier")),
            source_text=source,
            confidence=0.35 if hint else 0.2,
            needs_clarification=True,
            alternatives=alternatives,
        )
    return CurrencyResult(source_text=source, confidence=0)
