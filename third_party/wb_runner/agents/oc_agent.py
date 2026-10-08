"""OcAgent -- an OpenCode BaseInstalledAgent for the external bench: generated
openai-compatible provider block, bun-run CLI, event stream mapped to ATIF."""

from __future__ import annotations

import base64
import copy
import json
import shlex
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories.agent import Agent
from harbor.models.trajectories.final_metrics import FinalMetrics
from harbor.models.trajectories.metrics import Metrics
from harbor.models.trajectories.step import Step
from harbor.models.trajectories.tool_call import ToolCall
from harbor.models.trajectories.trajectory import Trajectory

from workbuddy_bench.agents._agent_user import ensure_agent_user

_OUTPUT_FILENAME = "oc-output.txt"

# Must not collide with a real OpenCode provider id, or the config merge would override
# that provider's own npm + options instead of defining a new one.
_PROVIDER_ID = "wbbench"


class OcAgent(BaseInstalledAgent):
    """OpenCode CLI agent (installed-CLI harness) for harbor."""

    SUPPORTS_ATIF: bool = True

    def __init__(self, logs_dir: Path, *args, **kwargs):
        self._oc_version: str | None = kwargs.pop("OPENCODE_VERSION", None)
        # OpenCode has no --max-turns; the cap is advisory and recorded only, since the
        # real bound is the per-trial timeout.
        mt = kwargs.pop("OPENCODE_MAX_TURNS", None)
        self._max_turns: int | None = int(mt) if mt is not None else None
        model_params = kwargs.pop("model_params", None) or {}
        self._max_output_tokens = model_params.get("max_output_tokens")
        self._temperature = model_params.get("temperature")
        # Native-provider mode uses one of OpenCode's own providers instead of the
        # generated openai-compatible one, so the request never traverses the bench
        # proxy: no proxy request log, no extra_body injection, and reasoning depth
        # comes from --variant.
        self._native_provider = bool(kwargs.pop("OPENCODE_NATIVE_PROVIDER", False))
        variant = kwargs.pop("OPENCODE_VARIANT", None)
        if variant is None:
            extra_body = model_params.get("extra_body") or {}
            variant = extra_body.get("reasoning_effort")
        self._variant: str | None = str(variant) if variant else None
        connection = kwargs.pop("connection", None) or {}
        self._conn_mode = str(connection.get("mode") or "direct")
        self._proxy_url = str(connection.get("proxy_url") or "")
        self._mount_path = str(kwargs.pop("mount_path", None) or "/opt/opencode")
        # Running without the deny list would silently re-enable websearch, the one tool
        # that can leak the benchmark's own rubrics into the rollout, so fail fast.
        sp = kwargs.pop("settings_preset", None)
        if not (isinstance(sp, dict) and sp):
            raise ValueError(
                "opencode settings_preset is missing/empty — the harness version "
                "config must set `harness.settings_file` to its mapped preset "
                "(each versions/<v>.yaml). Running without a deny list would "
                "silently re-enable websearch/question."
            )
        self._settings_preset: dict = dict(sp)
        kwargs.pop("models_preset", None)
        cw = kwargs.pop("context_window", None)
        self._context_window: int | None = int(cw) if cw is not None else None
        pct = kwargs.pop("context_compact_pct", None)
        self._compact_pct: int | None = int(pct) if pct is not None else None
        kwargs.pop("instance_id", None)
        self._session_id = self._trial_id_from_logs_dir(logs_dir)
        super().__init__(logs_dir, *args, version=self._oc_version, **kwargs)

    @staticmethod
    def _trial_id_from_logs_dir(logs_dir: Path | None) -> str:
        """The per-trial id, used to prefix the bearer token so proxy logs split by trial."""
        try:
            p = Path(logs_dir)
        except TypeError:
            return ""
        return p.parent.name if p.name == "agent" else ""

    @staticmethod
    def name() -> str:
        return "oc"

    def get_version_command(self) -> str | None:
        return 'export PATH="/usr/local/bin:$PATH"; opencode --version'

    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(
            environment,
            command=(
                "command -v curl >/dev/null 2>&1 && command -v ps >/dev/null 2>&1 && exit 0; "
                "if command -v apk &> /dev/null; then"
                "  apk add --no-cache curl bash nodejs npm procps;"
                " elif command -v apt-get &> /dev/null; then"
                "  apt-get update && apt-get install -y curl procps;"
                " elif command -v yum &> /dev/null; then"
                "  yum install -y curl procps-ng;"
                " fi;"
                " true"
            ),
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        # The mount also carries the `bun` runtime; the launcher shim execs it by
        # absolute path, so bun does not need to be on PATH itself.
        ver = self._oc_version or ""
        pkg = "opencode-ai" + (f"@{ver}" if ver else "")
        mount_bin = f"{self._mount_path.rstrip('/')}/bin"
        await self.exec_as_root(
            environment,
            command=(
                'export PATH="/usr/local/bin:$PATH"; '
                f'[ -x "{mount_bin}/opencode" ] && '
                f'ln -sf "{mount_bin}/opencode" "/usr/local/bin/opencode"; '
                # ripgrep must be on PATH before the first glob/grep call, or OpenCode
                # downloads it at run time, which an egress-controlled container blocks.
                f'command -v rg >/dev/null 2>&1 || '
                f'{{ [ -x "{mount_bin}/rg" ] && ln -sf "{mount_bin}/rg" "/usr/local/bin/rg"; }}; '
                "true; "
                "if command -v opencode >/dev/null 2>&1; then "
                "echo 'opencode present (split-mount/image), skipping npm install'; "
                # A build that is not the pinned artifact must fail loudly, not score:
                # the npm build lacks the baked-in ripgrep, so glob/grep fail all trial.
                "elif [ \"${WBBENCH_OC_ALLOW_NPM_FALLBACK:-0}\" = \"1\" ]; then "
                f"echo 'WARNING: split-mount absent; installing {pkg} from npm "
                "(NOT the pinned mount image — tooling may differ)' >&2; "
                f"npm install -g --force {pkg}; "
                "else "
                f'echo "ERROR: opencode split-mount not found at {mount_bin}." >&2; '
                'echo "  The harness mount was not attached to this container, so the'
                ' pinned CLI (with its baked-in ripgrep) is unavailable." >&2; '
                'echo "  Build it:  scripts/harness/build-harness-mounts.sh '
                '--harness opencode/<version>" >&2; '
                'echo "  Override (degraded, not comparable): '
                'WBBENCH_OC_ALLOW_NPM_FALLBACK=1" >&2; '
                "exit 1; fi && "
                "opencode --version && "
                '{ command -v rg >/dev/null 2>&1 || '
                '{ echo "ERROR: ripgrep (rg) not on PATH; OpenCode glob/grep would '
                'attempt a run-time download and fail." >&2; exit 1; }; } && '
                "rg --version | head -1"
            ),
        )
        await ensure_agent_user(self, environment)

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        instruction = self.render_instruction(instruction)
        escaped_instruction = shlex.quote(instruction)

        route = self.model_name  # route slug under local_proxy; real id under direct

        if self._conn_mode == "local_proxy":
            base_url = self._api_base_url(self._proxy_url)
            api_key = self._get_env("OPENCODE_API_KEY") or "dummy-for-proxy"
            # ``{trial}::{route}``: the proxy splits on ``::``, logs the trial half for
            # per-trial attribution and matches the route half.
            if self._session_id:
                api_key = f"{self._session_id}::{route}"
        else:
            base_url = self._api_base_url(self._get_env("OPENCODE_BASE_URL") or "")
            api_key = self._get_env("OPENCODE_API_KEY") or ""

        # In native-provider mode no provider block is written: `route` is already the
        # full provider-qualified id OpenCode resolves against its own registry.
        if self._native_provider:
            config: dict = copy.deepcopy(self._settings_preset)
            config_b64 = base64.b64encode(json.dumps(config).encode()).decode()
            return await self._run_opencode(
                environment, config_b64, model_arg=route,
                escaped_instruction=escaped_instruction,
            )

        model_entry: dict = {"id": route, "name": route, "tool_call": True, "reasoning": True}
        # OpenCode reads the compaction threshold off the model's declared context limit.
        if self._context_window is not None:
            model_entry["limit"] = {"context": self._context_window}
        if self._max_output_tokens is not None:
            model_entry.setdefault("limit", {})["output"] = int(self._max_output_tokens)

        # Only the provider block is overlaid: the permission/tools maps are never
        # touched here, so nothing can reorder the permission keys (later key wins).
        config: dict = copy.deepcopy(self._settings_preset)
        config["provider"] = {
            _PROVIDER_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "WorkBuddy Bench backend",
                "options": {"baseURL": base_url, "apiKey": api_key},
                "models": {route: model_entry},
            }
        }
        config_b64 = base64.b64encode(json.dumps(config).encode()).decode()
        return await self._run_opencode(
            environment, config_b64, model_arg=f"{_PROVIDER_ID}/{route}",
            escaped_instruction=escaped_instruction,
        )

    async def _run_opencode(
        self, environment: BaseEnvironment, config_b64: str, *,
        model_arg: str, escaped_instruction: str,
    ) -> None:
        """Write opencode.json into the container and run one headless task."""
        # `docker exec -u <user>` inherits a baked ENV HOME the agent user may not be able
        # to write, so HOME is re-derived from the running user's passwd entry.
        home_fix = 'export HOME="$(getent passwd "$(id -u)" | cut -d: -f6)"; '
        # An explicit OPENCODE_CONFIG path avoids auto-discovery, which would also pick up
        # an opencode.json inside the task's own workspace.
        config_export = home_fix + 'export OPENCODE_CONFIG="$HOME/.config/opencode/opencode.json"; '
        setup_cmd = (
            f"{config_export}"
            'mkdir -p "$(dirname "$OPENCODE_CONFIG")" && '
            f'echo {config_b64} | base64 -d > "$OPENCODE_CONFIG"'
        )
        await self.exec_as_agent(environment, command=setup_cmd)

        output_path = f"/logs/agent/{_OUTPUT_FILENAME}"
        # --thinking is required for reasoning events to be emitted at all.
        # --dangerously-skip-permissions auto-approves anything not explicitly denied; it
        # does not weaken the deny list, and without it every permission ask is rejected.
        flags = [
            "run",
            "--format", "json",
            "--thinking",
            "--dangerously-skip-permissions",
            "--model", shlex.quote(model_arg),
        ]
        if self._variant:
            flags += ["--variant", shlex.quote(self._variant)]
        env_prefix = config_export
        if self._max_output_tokens is not None:
            # Raise OpenCode's 32k internal output clamp.
            env_prefix += (
                f"export OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX={int(self._max_output_tokens)}; "
            )
        run_cmd = (
            'export PATH="/usr/local/bin:$PATH"; '
            f"{env_prefix}"
            f"opencode {' '.join(flags)} -- {escaped_instruction} "
            f"2>&1 </dev/null | tee {output_path}"
        )
        await self.exec_as_agent(environment, command=run_cmd)

    def populate_context_post_run(self, context: AgentContext) -> None:
        events = self._read_events()
        if events is None:
            return
        trajectory = self._build_trajectory(events)
        if trajectory:
            traj_path = self.logs_dir / "trajectory.json"
            try:
                with open(traj_path, "w", encoding="utf-8") as fh:
                    json.dump(trajectory.to_json_dict(), fh, indent=2, ensure_ascii=False)
            except (OSError, AttributeError) as exc:
                self.logger.debug("Failed writing trajectory.json: %s", exc)
            fm = trajectory.final_metrics
            if fm:
                context.n_input_tokens = fm.total_prompt_tokens or 0
                context.n_output_tokens = fm.total_completion_tokens or 0
                context.n_cache_tokens = fm.total_cached_tokens or 0
                if fm.total_cost_usd is not None:
                    context.cost_usd = fm.total_cost_usd

    def _read_events(self) -> list[dict] | None:
        out = self.logs_dir / _OUTPUT_FILENAME
        if not out.is_file():
            self.logger.warning(
                "opencode output not found: %s — token/cost context will be empty "
                "(check the /logs/agent mount)",
                out,
            )
            return None
        events: list[dict] = []
        try:
            for line in out.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError as exc:
            self.logger.debug("Failed reading opencode output: %s", exc)
            return None
        return events

    def _build_trajectory(self, events: list[dict]) -> Trajectory | None:
        """Map opencode's ``--format json`` event stream to an ATIF Trajectory.

        OpenCode emits flat, independent parts and no run-level total, so one Step is
        assembled per ``step_finish`` boundary from the parts seen since the previous one
        and totals are summed across boundaries. Trailing parts with no closing
        step_finish still become a final Step, with no metrics.
        """
        steps: list[Step] = []
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        session_id: str | None = None
        total_in = total_out = total_reason = total_cache = 0
        total_cost = 0.0
        have_cost = False
        errors: list = []
        tool_errors = 0
        step_id = 1

        def flush(metrics: Metrics | None, model_name: str | None) -> None:
            nonlocal text_parts, reasoning_parts, tool_calls, step_id
            # llm_call_count == 0 forbids both metrics and reasoning_content on the step,
            # so a trailing step without metrics must drop its reasoning too.
            reasoning = "\n".join(p for p in reasoning_parts if p) or None
            steps.append(Step(
                step_id=step_id,
                source="agent",
                model_name=model_name or self.model_name,
                message="\n".join(p for p in text_parts if p),
                reasoning_content=reasoning if metrics else None,
                tool_calls=tool_calls or None,
                metrics=metrics,
                llm_call_count=1 if metrics else 0,
            ))
            text_parts, reasoning_parts, tool_calls = [], [], []
            step_id += 1

        for ev in events:
            etype = ev.get("type")
            session_id = session_id or ev.get("sessionID")
            part = ev.get("part") if isinstance(ev.get("part"), dict) else {}

            if etype == "text":
                text_parts.append(str(part.get("text", "")))
            elif etype == "reasoning":
                reasoning_parts.append(str(part.get("text", "")))
            elif etype == "tool_use":
                state = part.get("state") if isinstance(part.get("state"), dict) else {}
                args = state.get("input")
                tool_calls.append(ToolCall(
                    tool_call_id=str(part.get("callID") or part.get("id") or ""),
                    function_name=str(part.get("tool", "")),
                    arguments=args if isinstance(args, dict) else {},
                ))
                # tool_use is emitted for both completed and error states.
                if state.get("status") == "error":
                    tool_errors += 1
            elif etype == "step_finish":
                tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
                cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
                t_in = int(tokens.get("input") or 0)
                t_out = int(tokens.get("output") or 0)
                # OpenCode reports reasoning separately; ATIF completion_tokens includes it.
                t_reason = int(tokens.get("reasoning") or 0)
                t_cache = int(cache.get("read") or 0)
                total_in += t_in
                total_out += t_out
                total_reason += t_reason
                total_cache += t_cache
                if isinstance(part.get("cost"), (int, float)):
                    total_cost += float(part["cost"])
                    have_cost = True
                flush(
                    Metrics(prompt_tokens=t_in, completion_tokens=t_out + t_reason),
                    part.get("modelID"),
                )
            elif etype == "error":
                err = ev.get("error")
                if err:
                    errors.append(err)

        if text_parts or reasoning_parts or tool_calls:
            flush(None, None)

        if not steps and not errors:
            return None
        if not steps:
            # ATIF requires >= 1 step, so a run that failed before producing one records
            # why in a synthesized step rather than raising during validation.
            steps.append(Step(
                step_id=1,
                source="agent",
                model_name=self.model_name,
                message=f"run produced no steps; {len(errors)} session error(s): "
                        + "; ".join(json.dumps(e)[:300] for e in errors[:3]),
                llm_call_count=0,
            ))

        extra: dict = {
            "steps": len(steps),
            "reasoning_tokens": total_reason,
            "tool_errors": tool_errors,
        }
        if errors:
            extra["opencode_errors"] = errors

        final_metrics = FinalMetrics(
            total_prompt_tokens=total_in,
            total_completion_tokens=total_out + total_reason,
            total_cached_tokens=total_cache,
            total_cost_usd=total_cost if have_cost else None,
            total_steps=len(steps),
            extra=extra,
        )

        notes = (
            "OpenCode emits no run-level total event, so totals are summed across "
            "step_finish events. completion_tokens = tokens.output + tokens.reasoning "
            "(OpenCode reports reasoning separately; the raw split is in "
            "extra.reasoning_tokens). One Step = one step_finish boundary."
        )
        if errors:
            notes += f" RUN REPORTED {len(errors)} session error(s); see extra.opencode_errors."

        return Trajectory(
            session_id=session_id,
            agent=Agent(name="oc", version=self._version or "unknown",
                        model_name=self.model_name),
            steps=steps,
            final_metrics=final_metrics,
            notes=notes,
        )

    @staticmethod
    def _api_base_url(url: str) -> str:
        """Normalize to the ``/v1`` API base the AI SDK expects.

        ``@ai-sdk/openai-compatible`` appends ``/chat/completions`` itself, and the bench
        proxy classifies a request as OpenAI only when the path ends that way.
        """
        u = (url or "").strip().rstrip("/")
        if not u:
            return u
        if u.endswith("/chat/completions"):
            u = u[: -len("/chat/completions")]
        if not u.endswith("/v1"):
            u = f"{u}/v1"
        return u
