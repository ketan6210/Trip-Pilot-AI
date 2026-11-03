from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.models import BudgetSnapshot, ExpenseLineItem

TOLERANCE = 0.02


def _amount(value) -> float:
    return round(float(value or 0), 2)


def make_expense(
    *,
    plan_id: int,
    trip_id: int,
    category: str,
    title: str,
    currency: str,
    expected: float,
    minimum: float | None = None,
    maximum: float | None = None,
    subcategory: str = "general",
    day_number: int | None = None,
    itinerary_item_id: str | None = None,
    description: str = "",
    quantity: float = 1,
    unit: str = "group",
    shared_or_personal: str = "SHARED",
    traveler_count: int = 1,
    assumptions: list[str] | None = None,
    source_type: str = "ESTIMATED",
    source_reference: str | None = None,
    source_quality: str | None = None,
    metadata: dict | None = None,
) -> ExpenseLineItem:
    quantity = max(float(quantity), 1)
    minimum = expected if minimum is None else minimum
    maximum = expected if maximum is None else maximum
    unit_min, unit_expected, unit_max = minimum / quantity, expected / quantity, maximum / quantity
    return ExpenseLineItem(
        plan_id=plan_id,
        trip_id=trip_id,
        category=category,
        subcategory=subcategory,
        day_number=day_number,
        itinerary_item_id=itinerary_item_id,
        title=title,
        description=description,
        quantity=quantity,
        unit=unit,
        unit_cost_min=round(unit_min, 2),
        unit_cost_expected=round(unit_expected, 2),
        unit_cost_max=round(unit_max, 2),
        subtotal_min=round(minimum, 2),
        subtotal_expected=round(expected, 2),
        subtotal_max=round(maximum, 2),
        shared_or_personal=shared_or_personal,
        traveler_count=traveler_count,
        taxes=0,
        fees=0,
        tips=0,
        total_min=round(minimum, 2),
        total_expected=round(expected, 2),
        total_max=round(maximum, 2),
        currency=currency,
        calculation_expression=f"{quantity:g} {unit} × {currency} {unit_expected:,.2f} = {currency} {expected:,.2f}",
        assumptions=assumptions or [],
        source_type=source_type,
        source_reference=source_reference,
        source_quality=source_quality or source_type,
        metadata_json=metadata or {},
    )


def serialize_expense(row: ExpenseLineItem) -> dict:
    return {
        "id": row.id,
        "trip_id": row.trip_id,
        "plan_id": row.plan_id,
        "category": row.category,
        "subcategory": row.subcategory,
        "day_number": row.day_number,
        "itinerary_item_id": row.itinerary_item_id,
        "title": row.title,
        "description": row.description,
        "quantity": row.quantity,
        "unit": row.unit,
        "unit_cost_min": _amount(row.unit_cost_min),
        "unit_cost_expected": _amount(row.unit_cost_expected),
        "unit_cost_max": _amount(row.unit_cost_max),
        "subtotal_min": _amount(row.subtotal_min),
        "subtotal_expected": _amount(row.subtotal_expected),
        "subtotal_max": _amount(row.subtotal_max),
        "shared_or_personal": row.shared_or_personal,
        "traveler_count": row.traveler_count,
        "taxes": _amount(row.taxes),
        "fees": _amount(row.fees),
        "tips": _amount(row.tips),
        "total_min": _amount(row.total_min),
        "total_expected": _amount(row.total_expected),
        "total_max": _amount(row.total_max),
        "currency": row.currency,
        "calculation_expression": row.calculation_expression,
        "assumptions": row.assumptions,
        "source_type": row.source_type,
        "source_reference": row.source_reference,
        "source_quality": row.source_quality,
        "metadata": row.metadata_json,
    }


def reconcile_expenses(rows: list[ExpenseLineItem], expected_total: float | None = None) -> dict:
    category_totals: dict[str, float] = defaultdict(float)
    day_totals: dict[str, float] = defaultdict(float)
    arithmetic_valid = True
    personal_math_valid = True
    for row in rows:
        total = _amount(row.total_expected)
        category_totals[row.category] += total
        if row.day_number is not None:
            day_totals[str(row.day_number)] += total
        computed = _amount(row.subtotal_expected) + _amount(row.taxes) + _amount(row.fees) + _amount(row.tips)
        arithmetic_valid = arithmetic_valid and abs(computed - total) <= TOLERANCE
        if row.shared_or_personal == "PER_PERSON" and row.unit in {"person", "adult", "traveler"}:
            personal_math_valid = personal_math_valid and abs(
                _amount(row.unit_cost_expected) * row.quantity - total
            ) <= max(TOLERANCE, 0.02 * row.quantity)
    category_totals = {key: round(value, 2) for key, value in sorted(category_totals.items())}
    day_totals = {key: round(value, 2) for key, value in sorted(day_totals.items(), key=lambda item: int(item[0]))}
    total = round(sum(category_totals.values()), 2)
    total_matches = expected_total is None or abs(total - round(expected_total, 2)) <= TOLERANCE
    food_rows = [row for row in rows if row.category == "food"]
    meal_types_by_day: dict[int, set[str]] = defaultdict(set)
    for row in food_rows:
        if row.day_number and row.subcategory in {"breakfast", "lunch", "dinner"}:
            meal_types_by_day[row.day_number].add(row.subcategory)
    meals_complete = not meal_types_by_day or all(
        types == {"breakfast", "lunch", "dinner"} for types in meal_types_by_day.values()
    )
    checks = {
        "line_item_arithmetic": arithmetic_valid,
        "personal_group_math": personal_math_valid,
        "category_sum_equals_overall": total_matches,
        "food_days_have_three_meals": meals_complete,
    }
    return {
        "valid": all(checks.values()),
        "checks": checks,
        "expected_total": total,
        "category_totals": category_totals,
        "day_totals": day_totals,
        "rounding_tolerance": TOLERANCE,
    }


def persist_expenses(db: Session, plan_id: int, rows: list[ExpenseLineItem]) -> dict:
    db.execute(delete(BudgetSnapshot).where(BudgetSnapshot.plan_id == plan_id))
    db.execute(delete(ExpenseLineItem).where(ExpenseLineItem.plan_id == plan_id))
    db.add_all(rows)
    db.flush()
    reconciliation = reconcile_expenses(rows)
    if not reconciliation["valid"]:
        raise ValueError("expense line items did not reconcile")
    db.add(
        BudgetSnapshot(
            plan_id=plan_id,
            expected_total=Decimal(str(reconciliation["expected_total"])),
            category_totals=reconciliation["category_totals"],
            reconciliation=reconciliation,
        )
    )
    db.commit()
    return reconciliation


def _rows(
    db: Session, plan_id: int, category: str | None = None, day_number: int | None = None
) -> list[ExpenseLineItem]:
    statement = select(ExpenseLineItem).where(ExpenseLineItem.plan_id == plan_id)
    if category:
        statement = statement.where(ExpenseLineItem.category == category)
    if day_number is not None:
        statement = statement.where(ExpenseLineItem.day_number == day_number)
    return list(db.scalars(statement.order_by(ExpenseLineItem.day_number.nulls_last(), ExpenseLineItem.id)))


def expense_dashboard(db: Session, plan_id: int) -> dict:
    rows = _rows(db, plan_id)
    if not rows:
        raise LookupError("expense breakdown has not been generated")
    reconciliation = reconcile_expenses(rows)
    grouped: dict[str, list[ExpenseLineItem]] = defaultdict(list)
    for row in rows:
        grouped[row.category].append(row)
    categories = []
    for category, items in sorted(grouped.items()):
        by_day: dict[str, float] = defaultdict(float)
        for item in items:
            if item.day_number is not None:
                by_day[str(item.day_number)] += _amount(item.total_expected)
        categories.append(
            {
                "category": category,
                "minimum": round(sum(_amount(item.total_min) for item in items), 2),
                "expected": round(sum(_amount(item.total_expected) for item in items), 2),
                "maximum": round(sum(_amount(item.total_max) for item in items), 2),
                "day_totals": {
                    key: round(value, 2) for key, value in sorted(by_day.items(), key=lambda pair: int(pair[0]))
                },
                "line_count": len(items),
            }
        )
    return {
        "plan_id": plan_id,
        "currency": rows[0].currency,
        "expected_total": reconciliation["expected_total"],
        "categories": categories,
        "reconciliation": reconciliation,
    }


def expense_breakdown(db: Session, plan_id: int, category: str | None = None, day_number: int | None = None) -> dict:
    rows = _rows(db, plan_id, category, day_number)
    if not rows:
        raise LookupError("no matching expense lines were found")
    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_day[str(row.day_number) if row.day_number is not None else "trip"].append(serialize_expense(row))
    return {
        "plan_id": plan_id,
        "category": category,
        "day_number": day_number,
        "currency": rows[0].currency,
        "minimum": round(sum(_amount(row.total_min) for row in rows), 2),
        "expected": round(sum(_amount(row.total_expected) for row in rows), 2),
        "maximum": round(sum(_amount(row.total_max) for row in rows), 2),
        "days": [
            {"day": key, "expected": round(sum(item["total_expected"] for item in items), 2), "items": items}
            for key, items in by_day.items()
        ],
    }
