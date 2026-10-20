# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Provider resolution and OpenRouter derivation contracts."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import config, preflight


class ResolverTests(unittest.TestCase):
    def test_openrouter_splits_provider_from_api_model(self):
        ref = config.resolve_model("openrouter/anthropic/claude-sonnet-4")
        self.assertEqual(ref.provider, "openrouter")
        self.assertEqual(ref.opencode_id, "openrouter/anthropic/claude-sonnet-4")
        self.assertEqual(ref.api_model, "anthropic/claude-sonnet-4")
        self.assertTrue(ref.is_openrouter)
        self.assertFalse(ref.is_vertex)

    def test_vertex_splits_provider_from_api_model(self):
        ref = config.resolve_model("google-vertex/gemini-3.5-flash")
        self.assertEqual(ref.provider, "google-vertex")
        self.assertEqual(ref.api_model, "gemini-3.5-flash")
        self.assertTrue(ref.is_vertex)

    def test_unknown_provider_keeps_opencode_id(self):
        ref = config.resolve_model("openai/gpt-5")
        self.assertEqual(ref.provider, "openai")
        self.assertEqual(ref.opencode_id, "openai/gpt-5")
        self.assertEqual(ref.api_model, "gpt-5")

    def test_bare_id_is_preserved(self):
        ref = config.resolve_model("gemini-3.5-flash")
        self.assertEqual(ref.provider, "")
        self.assertEqual(ref.opencode_id, "gemini-3.5-flash")
        self.assertEqual(ref.api_model, "gemini-3.5-flash")

    def test_codex_alias_keeps_openrouter_prefix(self):
        self.assertEqual(
            config.codex_alias(config.resolve_model("openrouter/openai/gpt-5")),
            "openrouter/openai/gpt-5",
        )
        self.assertEqual(
            config.codex_alias(config.resolve_model("google-vertex/gemini-3.5-flash")),
            "gemini-3.5-flash",
        )


class BackendTests(unittest.TestCase):
    def test_openrouter_backend(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-key"}, clear=False):
            backend = config.model_backend("openrouter/anthropic/claude-sonnet-4")
        self.assertEqual(backend.url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(backend.api_model, "anthropic/claude-sonnet-4")
        self.assertEqual(backend.key, "or-key")
        self.assertEqual(backend.key_command, "")
        self.assertFalse(backend.thought_signature)

    def test_openrouter_backend_requires_key(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}, clear=False):
            with self.assertRaisesRegex(config.ConfigError, "OPENROUTER_API_KEY"):
                config.model_backend("openrouter/anthropic/claude-sonnet-4")

    def test_vertex_backend_uses_gcloud_and_thought_signatures(self):
        with patch.object(config, "VERTEX_PROJECT", "proj"), patch.object(
            config, "VERTEX_OPENAI_URL", "https://vertex.example/v1"
        ):
            backend = config.model_backend("google-vertex/gemini-3.5-flash")
        self.assertEqual(backend.url, "https://vertex.example/v1/chat/completions")
        self.assertEqual(backend.api_model, "google/gemini-3.5-flash")
        self.assertEqual(backend.key, "")
        self.assertEqual(backend.key_command, config.GCLOUD_TOKEN_COMMAND)
        self.assertTrue(backend.thought_signature)

    def test_unsupported_backend_provider_is_rejected(self):
        with self.assertRaisesRegex(config.ConfigError, "Unsupported backend provider"):
            config.model_backend("openai/gpt-5")


class JudgeTests(unittest.TestCase):
    def test_openrouter_judge_target(self):
        with patch.dict(
            os.environ,
            {"OPENROUTER_API_KEY": "or-key", "HARNESS2_JUDGE_BASE_URL": "", "HARNESS2_JUDGE_API_KEY": ""},
            clear=False,
        ):
            model = "openrouter/openai/gpt-5"
            self.assertEqual(config.judge_api_base(model), "https://openrouter.ai/api/v1")
            self.assertEqual(config.judge_api_key(model), "or-key")
            self.assertEqual(config.jobbench_judge_model(model), "openai/gpt-5")
            self.assertEqual(config.lab_judge_model(model), model)

    def test_explicit_judge_override_wins(self):
        with patch.dict(
            os.environ,
            {
                "HARNESS2_JUDGE_BASE_URL": "https://proxy.example/v1",
                "HARNESS2_JUDGE_API_KEY": "explicit-key",
            },
            clear=False,
        ):
            model = "openrouter/openai/gpt-5"
            self.assertEqual(config.judge_api_base(model), "https://proxy.example/v1")
            self.assertEqual(config.judge_api_key(model), "explicit-key")

    def test_legacy_bare_gemini_judge_still_maps_to_vertex(self):
        with patch.dict(os.environ, {"HARNESS2_JUDGE_BASE_URL": ""}, clear=False):
            self.assertEqual(
                config.lab_judge_model("gemini-3.5-flash"), "vertex/gemini-3.5-flash"
            )
            self.assertEqual(
                config.jobbench_judge_model("gemini-3.5-flash"),
                "google/gemini-3.5-flash",
            )

    def test_litellm_model_mapping(self):
        self.assertEqual(
            config.litellm_model(config.resolve_model("openrouter/anthropic/claude-sonnet-4")),
            "openrouter/anthropic/claude-sonnet-4",
        )
        self.assertEqual(
            config.litellm_model(config.resolve_model("google-vertex/gemini-3.5-flash")),
            "vertex_ai/gemini-3.5-flash",
        )
        with self.assertRaisesRegex(config.ConfigError, "Unsupported Responses API provider"):
            config.litellm_model(config.resolve_model("openai/gpt-5"))


class ValidateModelsTests(unittest.TestCase):
    def test_explicit_model_arguments_need_no_model_env(self):
        with patch.dict(
            os.environ,
            {
                "HARNESS2_SOLVER_MODEL": "",
                "HARNESS2_JUDGE_MODEL": "",
                "OPENROUTER_API_KEY": "or-key",
            },
            clear=False,
        ):
            config.validate_models(
                solver="openrouter/a/b",
                improver="openrouter/a/b",
                judge=True,
                judge_model="openrouter/c/d",
            )

    def test_bare_gemini_judge_requires_vertex_project(self):
        with patch.dict(
            os.environ,
            {
                "HARNESS2_SOLVER_MODEL": "",
                "HARNESS2_JUDGE_MODEL": "",
                "OPENROUTER_API_KEY": "or-key",
                "GOOGLE_CLOUD_PROJECT": "",
                "HARNESS2_VERTEX_PROJECT": "",
            },
            clear=False,
        ), patch.object(config, "VERTEX_PROJECT", ""):
            with self.assertRaisesRegex(config.ConfigError, "GOOGLE_CLOUD_PROJECT"):
                config.validate_models(
                    solver="openrouter/a/b",
                    improver="openrouter/a/b",
                    judge=True,
                    judge_model="gemini-3.5-flash",
                )


class CredentialRequirementTests(unittest.TestCase):
    def test_credential_requirements_only_include_configured_roles(self):
        with patch.object(
            config, "SOLVER_MODEL_OC", "openrouter/openai/gpt-5"
        ), patch.object(
            config, "IMPROVER_MODEL", "openrouter/openai/gpt-5"
        ), patch.object(config, "JUDGE_MODEL", "gemini-3.5-flash"):
            self.assertEqual(
                config.credential_requirements(judge=True),
                {"vertex": True, "openrouter": True},
            )
            self.assertEqual(
                config.credential_requirements(judge=False),
                {"vertex": False, "openrouter": True},
            )

    def test_workbuddy_with_openrouter_judge_needs_openrouter_only(self):
        with patch.object(
            config, "SOLVER_MODEL_OC", "openrouter/openai/gpt-5"
        ), patch.object(
            config, "IMPROVER_MODEL", "openrouter/openai/gpt-5"
        ), patch.object(config, "JUDGE_MODEL", "openrouter/openai/gpt-5"):
            self.assertEqual(
                config.credential_requirements(workbuddy=True),
                {"vertex": False, "openrouter": True},
            )

    def test_workbuddy_with_vertex_roles_needs_vertex_only(self):
        with patch.object(
            config, "SOLVER_MODEL_OC", "google-vertex/gemini-3.5-flash"
        ), patch.object(
            config, "IMPROVER_MODEL", "google-vertex/gemini-3.5-flash"
        ), patch.object(config, "JUDGE_MODEL", "google-vertex/gemini-3.5-flash"):
            self.assertEqual(
                config.credential_requirements(workbuddy=True),
                {"vertex": True, "openrouter": False},
            )

    def test_explicit_models_override_module_globals(self):
        with patch.object(
            config, "SOLVER_MODEL_OC", "openrouter/solver"
        ), patch.object(
            config, "IMPROVER_MODEL", "openrouter/improver"
        ), patch.object(config, "JUDGE_MODEL", "openrouter/judge"):
            self.assertEqual(
                config.credential_requirements(
                    judge=True,
                    solver="google-vertex/gemini-3.5-flash",
                    improver="google-vertex/gemini-3.5-flash",
                    judge_model="gemini-3.5-flash",
                ),
                {"vertex": True, "openrouter": False},
            )

    def test_workbuddy_without_judge_ignores_judge_ref(self):
        with patch.object(
            config, "SOLVER_MODEL_OC", "openrouter/openai/gpt-5"
        ), patch.object(
            config, "IMPROVER_MODEL", "openrouter/openai/gpt-5"
        ), patch.object(config, "JUDGE_MODEL", ""):
            self.assertEqual(
                config.credential_requirements(workbuddy=True),
                {"vertex": False, "openrouter": True},
            )


class WorkBuddyRouteTests(unittest.TestCase):
    def test_openrouter_solver_route(self):
        from harness2 import install

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-key"}, clear=False):
            payload = install.workbuddy_route(
                "solver", model="openrouter/anthropic/claude-sonnet-4"
            )
        model = payload["model"]
        self.assertEqual(model["name"], "anthropic/claude-sonnet-4")
        self.assertEqual(model["backend_url_env"], "HARNESS2_WB_SOLVER_URL")
        self.assertEqual(model["backend_key_env"], "HARNESS2_WB_SOLVER_KEY")
        self.assertNotIn("backend_key_command", model)
        self.assertNotIn("thought_signature_passback", model)

    def test_vertex_judge_route_keeps_dynamic_key(self):
        from harness2 import install

        with patch.object(config, "VERTEX_PROJECT", "proj"), patch.object(
            config, "VERTEX_OPENAI_URL", "https://vertex.example/v1"
        ):
            payload = install.workbuddy_route(
                "judge", model="google-vertex/gemini-3.5-flash"
            )
        model = payload["model"]
        self.assertEqual(model["name"], "google/gemini-3.5-flash")
        self.assertEqual(model["backend_url_env"], "HARNESS2_WB_JUDGE_URL")
        self.assertEqual(model["backend_key_command"], config.GCLOUD_TOKEN_COMMAND)
        self.assertTrue(model["thought_signature_passback"])

    def test_workbuddy_env_carries_static_keys_only(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-key"}, clear=False):
            env = config.workbuddy_env(
                solver="openrouter/openai/gpt-5",
                judge="openrouter/anthropic/claude-sonnet-4",
            )
        self.assertEqual(
            env["HARNESS2_WB_SOLVER_URL"],
            "https://openrouter.ai/api/v1/chat/completions",
        )
        self.assertEqual(env["HARNESS2_WB_SOLVER_KEY"], "or-key")
        self.assertEqual(env["HARNESS2_WB_JUDGE_KEY"], "or-key")

    def test_bare_vertex_judge_id_still_builds_a_vertex_route(self):
        from harness2 import install

        with patch.object(config, "VERTEX_PROJECT", "proj"), patch.object(
            config, "VERTEX_OPENAI_URL", "https://vertex.example/v1"
        ):
            payload = install.workbuddy_route("judge", model="gemini-3.5-flash")
        self.assertEqual(payload["model"]["name"], "google/gemini-3.5-flash")
        self.assertEqual(
            payload["model"]["backend_key_command"], config.GCLOUD_TOKEN_COMMAND
        )

    def test_workbuddy_env_mixed_providers(self):
        with patch.object(
            config, "SOLVER_MODEL_OC", "google-vertex/gemini-3.5-flash"
        ), patch.object(config, "VERTEX_PROJECT", "proj"), patch.object(
            config, "VERTEX_OPENAI_URL", "https://vertex.example/v1"
        ), patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-key"}, clear=False):
            env = config.workbuddy_env(judge="openrouter/anthropic/claude-sonnet-4")
        self.assertEqual(
            env["HARNESS2_WB_SOLVER_URL"],
            "https://vertex.example/v1/chat/completions",
        )
        self.assertNotIn("HARNESS2_WB_SOLVER_KEY", env)
        self.assertEqual(
            env["HARNESS2_WB_JUDGE_URL"],
            "https://openrouter.ai/api/v1/chat/completions",
        )
        self.assertEqual(env["HARNESS2_WB_JUDGE_KEY"], "or-key")

    def test_invalid_role_is_rejected(self):
        from harness2 import install

        with self.assertRaises(ValueError):
            install.workbuddy_route("selector", model="openrouter/a/b")

    def test_vertex_claude_is_rejected(self):
        from harness2 import install

        with self.assertRaisesRegex(config.ConfigError, "Gemini only"):
            install.workbuddy_route("solver", model="google-vertex/claude-sonnet-4")

    def test_missing_model_names_the_env_var(self):
        from harness2 import install

        with patch.dict(os.environ, {"HARNESS2_JUDGE_MODEL": ""}, clear=False), patch.object(
            config, "JUDGE_MODEL", ""
        ):
            with self.assertRaisesRegex(config.ConfigError, "HARNESS2_JUDGE_MODEL"):
                install.workbuddy_route("judge")

    def test_workbuddy_route_errors_detects_stale_and_missing(self):
        with tempfile.TemporaryDirectory() as td, patch.object(
            config, "SOLVER_MODEL_OC", "openrouter/openai/gpt-5"
        ), patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-key"}, clear=False):
            tmp = Path(td)
            route_dir = tmp / "configs/models/harness2"
            route_dir.mkdir(parents=True)
            (route_dir / "solver.yaml").write_text(
                json.dumps({"model": {"name": "stale"}}), encoding="utf-8"
            )
            self.assertIn("stale", preflight.workbuddy_route_errors(tmp, "solver")[0])
            self.assertIn("missing", preflight.workbuddy_route_errors(tmp, "judge")[0])


class WorkBuddyPreflightTests(unittest.TestCase):
    def test_matching_route_reports_no_errors(self):
        from harness2 import install

        model = "openrouter/openai/gpt-5"
        with tempfile.TemporaryDirectory() as td, patch.object(
            config, "SOLVER_MODEL_OC", model
        ), patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-key"}, clear=False):
            tmp = Path(td)
            route_dir = tmp / "configs/models/harness2"
            route_dir.mkdir(parents=True)
            (route_dir / "solver.yaml").write_text(
                json.dumps(install.workbuddy_route("solver", model=model)),
                encoding="utf-8",
            )
            self.assertEqual(preflight.workbuddy_route_errors(tmp, "solver"), [])

    def test_generator_error_is_reported_not_as_missing_configuration(self):
        with tempfile.TemporaryDirectory() as td, patch.object(
            config, "SOLVER_MODEL_OC", "openrouter/openai/gpt-5"
        ), patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}, clear=False):
            tmp = Path(td)
            route_dir = tmp / "configs/models/harness2"
            route_dir.mkdir(parents=True)
            (route_dir / "solver.yaml").write_text(
                json.dumps({"model": {"name": "openrouter/openai/gpt-5"}}),
                encoding="utf-8",
            )
            errors = preflight.workbuddy_route_errors(tmp, "solver")
            self.assertTrue(errors)
            self.assertIn("OPENROUTER_API_KEY", errors[0])
