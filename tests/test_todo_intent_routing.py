from src.chat_agent_tool_discovery_map import keyword_hint_pairs
from src.agent_loop_intent import _classify_agent_request
from src.todo_intent import (
    is_clear_todo_intent,
    is_todo_memory_payload,
    route_todo_toolset,
)
from src.tool_index import ALWAYS_AVAILABLE, ToolIndex
from src.tool_schema_definitions import FUNCTION_TOOL_SCHEMAS


def test_clear_english_and_german_todo_intents_are_detected():
    for text in (
        "Add a todo: prepare the release",
        "Mark todo prepare the release done",
        "Neue To-do: Aufgabe Alpha",
        "Aufgabe erledigen: Evidence pruefen",
        "Todo wieder öffnen: Evidence pruefen",
        "Add a tdoo: prepare the release",
        "Complete the complte todo list item",
        "Neue Aufgane:\n- Alpha\n- Beta",
        "Todo für Freitag: Videos speichern und Tobi schicken.",
        "Todos für Freitag: Videos speichern und Tobi schicken.",
        "Aufgabe für Freitag: Videos speichern und Tobi schicken.",
        "Aufgaben für Freitag: Videos speichern und Tobi schicken.",
        "Zu erledigen bis Freitag: Videos speichern und Tobi schicken.",
    ):
        assert is_clear_todo_intent(text), text


def test_todo_mentions_inside_ordinary_preferences_are_not_routed():
    for text in (
        "I prefer compact todo lists",
        "We discussed a task yesterday",
        "My favorite checklist app is simple",
        "Remember that I prefer short summaries",
    ):
        assert not is_clear_todo_intent(text), text


def test_clear_todo_turn_has_one_persistent_domain_facade():
    routed = route_todo_toolset(
        {"manage_memory", "manage_notes", "ask_user"},
        "Neue Aufgabe: synthetischen Test ausfuehren",
    )

    assert routed == {"manage_todos", "ask_user"}


def test_non_todo_turn_preserves_memory_and_notes_tools():
    selected = {"manage_memory", "manage_notes", "ask_user"}
    assert route_todo_toolset(selected, "My name is Alice") == selected


def test_todo_memory_payload_gate_catches_explicit_domain_payloads_only():
    assert is_todo_memory_payload(text="Task: prepare release", category="fact")
    assert is_todo_memory_payload(text="Prepare release", category="todo")
    assert not is_todo_memory_payload(
        text="User prefers compact todo lists", category="preference"
    )


def test_discovery_backstop_maps_todo_keywords_to_manage_todos():
    pairs = list(keyword_hint_pairs())
    assert any("neue aufgabe" in keywords and tools == {"manage_todos"} for keywords, tools in pairs)


def test_flexible_german_todo_labels_select_the_todo_domain():
    for text in (
        "Todo: Videos speichern",
        "Todos für morgen: Tobi schreiben",
        "Aufgabe: Paket abholen",
        "Aufgaben bis Freitag: Bericht senden",
        "Zu erledigen: Rechnung bezahlen",
    ):
        assert "todos" in _classify_agent_request([], text)["domains"], text
        assert route_todo_toolset({"manage_memory", "manage_notes"}, text) == {
            "manage_todos"
        }


def test_real_keyword_fallback_advertises_only_the_todo_persistence_facade():
    query = "Neue To-do: Aufgabe Alpha"
    selected = set(ALWAYS_AVAILABLE)
    for keywords, tools in ToolIndex._KEYWORD_HINTS.items():
        if any(keyword in query.casefold() for keyword in keywords):
            selected.update(tools)
    selected = route_todo_toolset(selected, query)
    schema_names = {
        schema["function"]["name"]
        for schema in FUNCTION_TOOL_SCHEMAS
        if schema["function"]["name"] in selected
    }

    assert "manage_todos" in schema_names
    assert "manage_memory" not in schema_names
    assert "manage_notes" not in schema_names
