from __future__ import annotations


def exchange_rate(source: str, target: str) -> float | None:
    from backend.currency import currency_service

    result = currency_service.rate(source, target)
    return result[0] if result else None


def convert_amount(amount: float, source: str, target: str) -> float | None:
    from backend.currency import currency_service

    result = currency_service.convert(amount, source, target)
    return result.converted_amount if result else None
