"""plan_view.build_plan_data: what the band draws next to the table."""

from app.services.plan_view import build_plan_data

LEGS = [
    {"from_name": "A", "to_name": "B", "from_km": 10.0, "to_km": 35.0, "km": 25.0, "time_s": 4 * 3600, "alert": True},
    {"from_name": "B", "to_name": "C", "from_km": 35.0, "to_km": 42.0, "km": 7.0, "time_s": 3600, "alert": False},
]


def test_autonomy_keeps_only_alert_legs():
    data = build_plan_data([], 0, False, 10.0, autonomy=LEGS)
    assert len(data["autonomy"]) == 1
    leg = data["autonomy"][0]
    assert leg == {"start_km": 10.0, "end_km": 35.0, "km": 25.0, "time_s": 4 * 3600, "from_name": "A", "to_name": "B"}


def test_autonomy_defaults_to_empty():
    assert build_plan_data([], 0, False, 10.0)["autonomy"] == []
    assert build_plan_data([], 0, False, 10.0, autonomy=None)["autonomy"] == []
