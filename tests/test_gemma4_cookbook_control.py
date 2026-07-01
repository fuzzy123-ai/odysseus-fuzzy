import pytest

from src.gemma4_cookbook_control import (
    Gemma4CookbookAction,
    Gemma4CookbookControlError,
    plan_gemma4_cookbook_control,
)


def test_status_is_read_only_and_uses_cookbook_native_tool():
    plan = plan_gemma4_cookbook_control(action=Gemma4CookbookAction.STATUS).to_dict()

    assert plan["tool_name"] == "list_served_models"
    assert plan["args"] == {}
    assert plan["live_go_required"] is False
    assert plan["operator_confirmation_required"] is False
    assert plan["shell_allowed"] is False
    assert plan["ssh_tmux_bypass_allowed"] is False


def test_serve_requires_operator_and_live_go_before_confirmed_args():
    waiting = plan_gemma4_cookbook_control(action="serve").to_dict()
    ready = plan_gemma4_cookbook_control(action="serve", operator_go=True, live_go=True).to_dict()

    assert waiting["tool_name"] == "serve_preset"
    assert waiting["args"]["name"] == "gemma4-e4b-maintenance"
    assert waiting["args"]["confirmed"] is False
    assert waiting["reason"] == "awaiting_operator_live_go"
    assert ready["args"]["confirmed"] is True
    assert ready["args"]["operator_go"] is True
    assert ready["args"]["maintenance_model_ref"] == "gemma4:e4b"


def test_stop_and_adopt_are_gated_and_safe():
    stop = plan_gemma4_cookbook_control(
        action="stop",
        session_id="gemma4-e4b-maintenance",
        operator_go=True,
        live_go=True,
    ).to_dict()
    adopt = plan_gemma4_cookbook_control(
        action="adopt",
        host="ajax",
        tmux_session="gemma4-e4b",
        port=11434,
        operator_go=True,
        live_go=True,
    ).to_dict()

    assert stop["tool_name"] == "stop_served_model"
    assert stop["args"]["confirmed"] is True
    assert adopt["tool_name"] == "adopt_served_model"
    assert adopt["args"]["model"] == "gemma4:e4b"
    assert adopt["args"]["port"] == 11434


def test_rejects_secret_markers_and_invalid_adopt_port():
    with pytest.raises(Gemma4CookbookControlError):
        plan_gemma4_cookbook_control(action="serve", preset_name="token=abc123")

    with pytest.raises(Gemma4CookbookControlError):
        plan_gemma4_cookbook_control(action="adopt", host="ajax", tmux_session="gemma", port=0)
