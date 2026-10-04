"""Checks for the agent eval's scoring (pure logic, no Ollama or model needed)."""

from contextlib import closing

from test_db import db_path  # noqa: F401 - pytest fixture reused from test_db.py

from fleet_mcp import db
from fleet_mcp.agent_eval import CASES, score


def test_pass_needs_tools_facts_and_nothing_invented():
    required = [{"list_vehicles"}, {"search_fleet_knowledge", "search_similar_incidents"}]
    tool_text = "[{'registration': 'FM-0274'}, {'registration': 'FM-0480'}]"
    good = score(
        "q?",
        "FM‑0274 and FM-0480 are offline.",
        {"list_vehicles", "search_similar_incidents"},
        tool_text,
        required,
        ["FM-0274", "FM-0480"],
    )
    assert good["passed"]  # the typographic dash in "FM‑0274" is normalized

    invented = score(
        "q?",
        "FM-0274, FM-0480 and FM-0999.",
        {"list_vehicles", "search_fleet_knowledge"},
        tool_text,
        required,
        ["FM-0274"],
    )
    assert invented["invented"] == ["FM-0999"] and not invented["passed"]

    missing_tool = score("q?", "FM-0274, FM-0480", {"list_vehicles"}, tool_text, required, ["FM-0274"])
    assert not missing_tool["tools_ok"] and not missing_tool["passed"]

    missing_fact = score(
        "q?",
        "Only FM-0274.",
        {"list_vehicles", "search_fleet_knowledge"},
        tool_text,
        required,
        ["FM-0274", "FM-0480"],
    )
    assert missing_fact["missing"] == ["FM-0480"]


def test_plate_named_in_question_is_not_invented():
    run = score(
        "Where is FM-0977?",
        "FM-0977 is near Essen.",
        {"get_vehicle_status"},
        "Essen",
        [{"get_vehicle_status"}],
        ["Essen"],
    )
    assert run["passed"]


def test_every_case_has_facts_on_a_seeded_db(db_path):  # noqa: F811
    """Each question's expected facts can be computed (and are non-empty) from fresh data."""
    with closing(db.connect(db_path)) as conn:
        for case in CASES:
            facts = case.facts(conn)
            assert facts and all(isinstance(f, str) and f for f in facts), case.question
