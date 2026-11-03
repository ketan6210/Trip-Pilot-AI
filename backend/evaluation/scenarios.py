SCENARIOS = [
    {"name": "Budget NYC", "budget": "1600", "interests": ["nature", "food", "landmarks"], "policy": {}},
    {
        "name": "Family trip",
        "budget": "5000",
        "interests": ["museums", "nature", "food"],
        "policy": {"youngest_age": 10, "veto_categories": ["nightlife"], "return_by_minute": 1260},
    },
    {
        "name": "Nightlife trip",
        "budget": "5000",
        "interests": ["nightlife", "food", "entertainment"],
        "policy": {"must_categories": ["nightlife"], "return_by_minute": 1500},
    },
    {
        "name": "Accessibility-sensitive",
        "budget": "5000",
        "interests": ["museums", "food", "nature"],
        "policy": {"wheelchair_required": True, "max_daily_travel_minutes": 75, "dietary_requirements": ["vegetarian"]},
    },
    {
        "name": "Conflicting member interests",
        "budget": "5000",
        "interests": ["museums", "food"],
        "policy": {},
        "second_interests": {"nightlife": 10, "nature": 9, "food": 8},
    },
    {
        "name": "Impossible budget",
        "budget": "1",
        "interests": ["nature", "food"],
        "policy": {},
        "expected_rejection": True,
    },
]
