# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Config-store support for params that never reach argv (Task 4).

dsh's model and permission mode are ordinary Settings params, but they are
delivered through the per-spawn composition file rather than the command line.
They therefore carry ``flag_template=None``, which ``build_extra_args`` must
skip instead of ``.format``-ing.
"""

import os

import pytest

from agent_os.daemon_v2.sub_agent_config_store import (
    SCHEMA,
    SubAgentConfigError,
    SubAgentConfigStore,
    _ParamSchema,
    resolve_params,
)


@pytest.fixture
def store(tmp_path):
    return SubAgentConfigStore(os.path.join(str(tmp_path), "sub_agent_config.json"))


class TestFlaglessParams:
    def test_build_extra_args_skips_none_flag_template(self, store):
        """A UI-only param must contribute nothing to argv."""
        store.set("dsh", {"model": "deepseek-official/deepseek-v4-pro"})
        assert store.build_extra_args("dsh") == []

    def test_build_extra_args_skips_defaults_with_none_flag_template(self, store):
        """Defaults are applied even with nothing persisted — and still emit
        no argv."""
        assert resolve_params("dsh", store)["permission-mode"] == "workspace-write"
        assert store.build_extra_args("dsh") == []

    def test_flagless_params_do_not_disturb_flagged_agents(self, store):
        store.set("claude-code", {"model": "sonnet"})
        assert store.build_extra_args("claude-code") == ["--model", "sonnet"]

    def test_mixed_schema_emits_only_the_flagged_param(self, store, monkeypatch):
        monkeypatch.setitem(SCHEMA, "mixed", {
            "flagged": _ParamSchema(
                name="flagged", allowed=None, flag_template="--flagged {value}",
            ),
            "silent": _ParamSchema(
                name="silent", allowed=None, flag_template=None,
            ),
        })
        store.set("mixed", {"flagged": "a", "silent": "b"})
        assert store.build_extra_args("mixed") == ["--flagged", "a"]

    def test_flagless_params_are_still_validated(self, store):
        with pytest.raises(SubAgentConfigError):
            store.set("dsh", {"model": "gpt-4"})

    def test_flagless_params_are_surfaced_to_the_ui(self, store):
        schema = store.schema_for("dsh")
        # The static fallback; the route widens it with dsh's live list.
        assert schema["model"]["allowed"] == [
            "deepseek-official/deepseek-flash", "deepseek-official/deepseek-v4-pro",
        ]
        # Unset follows the default model picked in DSH Desktop.
        assert schema["model"]["default"] is None
        assert schema["permission-mode"]["allowed"] == [
            "read-only", "workspace-write", "danger-full-access",
        ]
        assert schema["permission-mode"]["default"] == "workspace-write"

    def test_flagless_params_round_trip_through_the_store(self, store):
        store.set("dsh", {"model": "deepseek-official/deepseek-v4-pro",
                          "permission-mode": "danger-full-access"})
        assert store.get("dsh") == {
            "model": "deepseek-official/deepseek-v4-pro",
            "permission-mode": "danger-full-access",
        }

    def test_a_live_model_is_accepted_through_extra_allowed(self, store):
        store.set("dsh", {"model": "opencode-go/deepseek-v4-pro"},
                  extra_allowed={"model": ["opencode-go/deepseek-v4-pro"]})
        assert store.get("dsh")["model"] == "opencode-go/deepseek-v4-pro"


class TestResolveParams:
    def test_defaults_apply_when_nothing_persisted(self, store):
        assert resolve_params("dsh", store) == {"permission-mode": "workspace-write"}

    def test_persisted_overrides_beat_defaults(self, store):
        store.set("dsh", {"model": "deepseek-official/deepseek-v4-pro"})
        assert resolve_params("dsh", store) == {
            "model": "deepseek-official/deepseek-v4-pro",
            "permission-mode": "workspace-write",
        }

    def test_works_without_a_store(self):
        """Dispatch must still get the SCHEMA defaults when no store is wired."""
        assert resolve_params("dsh") == {"permission-mode": "workspace-write"}

    def test_unknown_slug_is_empty(self, store):
        assert resolve_params("nope", store) == {}


class TestDshSchema:
    def test_dsh_params_carry_no_argv_flag(self):
        for param in SCHEMA["dsh"].values():
            assert param.flag_template is None

    def test_model_allowlist(self):
        assert SCHEMA["dsh"]["model"].allowed == (
            "deepseek-official/deepseek-flash", "deepseek-official/deepseek-v4-pro",
        )
        assert SCHEMA["dsh"]["model"].default is None

    def test_permission_mode_is_dsh_env_with_all_three_modes(self):
        schema = SCHEMA["dsh"]["permission-mode"]
        assert schema.allowed == ("read-only", "workspace-write", "danger-full-access")
        assert schema.default == "workspace-write"
        assert schema.env_var == "DSH_PERMISSION_MODE"

    def test_effort_is_dshs_reasoning_option(self, tmp_path):
        from agent_os.daemon_v2.sub_agent_config_store import SubAgentConfigStore
        schema = SCHEMA["dsh"]["effort"]
        assert schema.allowed == ("off", "low", "high", "max")
        assert schema.default is None
        assert schema.acp_config_option == "reasoning_effort"
        store = SubAgentConfigStore(str(tmp_path / "cfg.json"))
        assert store.build_acp_config_options("dsh") == {}
        store.set("dsh", {"effort": "max"})
        assert store.build_acp_config_options("dsh") == {"reasoning_effort": "max"}
        assert store.build_extra_args("dsh") == []


class TestCodeBuddySchema:
    """CodeBuddy is an ACP sub-agent like cursor: model is free-text (the
    catalogue is account- and region-dependent) and permission-mode is
    Orbital's own ACP policy flag, which the transport strips from argv."""

    def test_model_is_free_text_and_argv_delivered(self):
        assert SCHEMA["codebuddy"]["model"].allowed is None
        assert SCHEMA["codebuddy"]["model"].flag_template == "--model {value}"

    def test_permission_mode_is_the_acp_policy(self):
        schema = SCHEMA["codebuddy"]["permission-mode"]
        assert schema.allowed == ("auto", "ask")
        assert schema.default == "auto"
        assert schema.flag_template == "--orbital-permission-mode {value}"

    def test_defaults_without_a_store(self):
        assert resolve_params("codebuddy") == {"permission-mode": "auto"}


class TestKimiCodeSchema:
    """Kimi Code: free-text model alias plus Orbital's ACP permission policy."""

    def test_model_is_free_text_and_argv_delivered(self):
        assert SCHEMA["kimi-code"]["model"].allowed is None
        assert SCHEMA["kimi-code"]["model"].flag_template == "--model {value}"

    def test_permission_mode_is_the_acp_policy(self):
        schema = SCHEMA["kimi-code"]["permission-mode"]
        assert schema.allowed == ("auto", "ask")
        assert schema.default == "auto"
        assert schema.flag_template == "--orbital-permission-mode {value}"

    def test_defaults_without_a_store(self):
        assert resolve_params("kimi-code") == {"permission-mode": "auto"}


class TestMiniMaxCodeSchema:
    """MiniMax Code: free-text internal model id plus the ACP permission policy."""

    def test_model_is_free_text_and_argv_delivered(self):
        assert SCHEMA["minimax-code"]["model"].allowed is None
        assert SCHEMA["minimax-code"]["model"].flag_template == "--model {value}"

    def test_permission_mode_is_the_acp_policy(self):
        schema = SCHEMA["minimax-code"]["permission-mode"]
        assert schema.allowed == ("auto", "ask")
        assert schema.default == "auto"
        assert schema.flag_template == "--orbital-permission-mode {value}"

    def test_defaults_without_a_store(self):
        assert resolve_params("minimax-code") == {"permission-mode": "auto"}
