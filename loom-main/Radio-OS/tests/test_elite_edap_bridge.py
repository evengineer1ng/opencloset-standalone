import json

from elite_edap_bridge import build_edap_waypoints, compute_route_signature, summarize_route_progress


def test_build_edap_waypoints_preserves_trade_loop_shape():
    route_plan = {
        "repeat": True,
        "global_buy_commodities": {"Palladium": 12},
        "legs": [
            {
                "key": "buy",
                "system_name": "LHS 20",
                "station_name": "Ohm City",
                "buy_commodities": {"Gold": 64},
                "sell_commodities": {},
                "system_bookmark_type": "Fav",
                "system_bookmark_number": 1,
            },
            {
                "key": "sell",
                "system_name": "Diaguandri",
                "station_name": "Ray Gateway",
                "buy_commodities": {},
                "sell_commodities": {"Gold": 64},
            },
        ],
    }

    waypoints = build_edap_waypoints(route_plan)

    assert waypoints["GlobalShoppingList"]["BuyCommodities"] == {"Palladium": 12}
    assert waypoints["buy"]["SystemName"] == "LHS 20"
    assert waypoints["buy"]["StationName"] == "Ohm City"
    assert waypoints["buy"]["BuyCommodities"] == {"Gold": 64}
    assert waypoints["buy"]["SystemBookmarkType"] == "Fav"
    assert waypoints["sell"]["SellCommodities"] == {"Gold": 64}
    assert waypoints["rep"]["SystemName"] == "REPEAT"


def test_summarize_route_progress_finds_next_leg():
    waypoints = {
        "GlobalShoppingList": {"BuyCommodities": {}, "UpdateCommodityCount": False, "Skip": False, "Completed": False},
        "1": {
            "SystemName": "LHS 20",
            "StationName": "Ohm City",
            "BuyCommodities": {"Gold": 64},
            "SellCommodities": {},
            "Skip": False,
            "Completed": True,
        },
        "2": {
            "SystemName": "Diaguandri",
            "StationName": "Ray Gateway",
            "BuyCommodities": {},
            "SellCommodities": {"Gold": 64},
            "Skip": False,
            "Completed": False,
        },
        "rep": {
            "SystemName": "REPEAT",
            "StationName": "",
            "BuyCommodities": {},
            "SellCommodities": {},
            "Skip": False,
            "Completed": False,
        },
    }

    summary = summarize_route_progress(waypoints)

    assert summary["completed"] == 1
    assert summary["actionable"] == 2
    assert summary["repeat_enabled"] is True
    assert summary["next_leg"]["system_name"] == "Diaguandri"
    assert summary["progress_pct"] == 50.0


def test_compute_route_signature_changes_when_plan_changes():
    plan_a = {"legs": [{"system_name": "A", "station_name": "B"}]}
    plan_b = {"legs": [{"system_name": "A", "station_name": "C"}]}

    assert compute_route_signature(plan_a) != compute_route_signature(plan_b)