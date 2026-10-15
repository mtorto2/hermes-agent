"""agent-lights plugin: Hermes session activity for the Agent Lights menu-bar app (macOS).

Each interactive CLI/TUI session (and each Kanban worker) claims a slot in the
shared ``~/.hermes/agent-lights/v2`` pool and writes its lifecycle state there;
the menu-bar app draws one dot per slot. Hook mapping:

- ``pre_llm_call``                         -> working
- ``on_human_input_request`` (clarify,
  approval, sudo prompts)                  -> human_intervention, back to working
                                              when every open request resolves;
                                              only for the open root turn
- ``on_session_end`` (fires once per turn) -> error if failed, idle if
  interrupted, final_answer if ``post_llm_call`` saw a response, else idle
- ``kanban_task_blocked`` / ``_completed`` -> human_intervention / final_answer
  in the worker running that task, kept through the turn's end (only a
  failed turn overrides it, with error)

Hermes builds without the ``on_human_input_*`` hooks fall back to
``pre_tool_call``/``post_tool_call`` for the clarify tool and
``pre_approval_request``/``post_approval_response`` for approvals.

The slot is claimed on the first turn, not at launch: no hook fires before
it, and plugins load in every Hermes process (gateway, cron, ...), so only a
turn on an interactive platform proves this process is a session to show.
Delegated subagents run in-process and are ignored. On macOS only.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
import threading
from typing import Any, Callable

from .protocol import InstalledAppLauncher, LightCueEvent, SlotStatusFileBackend

logger = logging.getLogger(__name__)

_INTERACTIVE_PLATFORMS = frozenset({"cli", "tui"})
_CONTEXT_PREVIEW_CHARS = 160
_HUMAN_INPUT_HOOKS = ("on_human_input_request", "on_human_input_resolved")


class AgentLightsProducer:
    """Turns lifecycle hook calls into Agent Lights slot writes for this process."""

    def __init__(
        self,
        *,
        backend_factory: Callable[[], Any] | None = None,
        launcher: Any | None = None,
    ):
        self._lock = threading.RLock()
        self._backend_factory = backend_factory or (lambda: SlotStatusFileBackend.from_env(auto_assign=True))
        self._launcher = launcher if launcher is not None else InstalledAppLauncher()
        self._backend: Any | None = None
        self._launch_started = False
        # Set by the first turn on an interactive platform (or in a Kanban
        # worker). Hooks that carry no platform act only once it is set.
        self._active = False
        self._model: str | None = None
        # The root session's open turn: human-input events count only while
        # it is open and only from that session (never a subagent's).
        self._turn_open = False
        self._root_session: str | None = None
        # This Kanban worker's own task outcome ("blocked" / "completed").
        self._task_outcome: str | None = None
        self._child_sessions: set[str] = set()
        self._open_human_requests: set[str] = set()
        self._responded: set[str] = set()

    # -- relevance -----------------------------------------------------------

    @staticmethod
    def _kanban_task() -> str | None:
        return os.environ.get("HERMES_KANBAN_TASK") or None

    def _activates(self, platform: Any) -> bool:
        return bool(self._kanban_task()) or str(platform or "").strip().lower() in _INTERACTIVE_PLATFORMS

    def _is_root_session(self, session_id: Any) -> bool:
        return not session_id or session_id not in self._child_sessions

    # -- slot writes ---------------------------------------------------------

    def _ensure_backend(self) -> Any | None:
        if self._backend is None:
            try:
                self._backend = self._backend_factory()
            except Exception as exc:
                logger.debug("Agent Lights slot claim failed: %s", exc)
                self._backend = None
            if self._backend is not None:
                self._start_launch()
        if self._backend is not None and self._model:
            self._backend.model_name = self._model
        return self._backend

    def _start_launch(self) -> None:
        # Opening the app can wait up to a few seconds; never on the agent thread.
        if self._launch_started:
            return
        self._launch_started = True
        threading.Thread(target=self._launch, name="agent-lights-launch", daemon=True).start()

    def _launch(self) -> None:
        try:
            self._launcher.ensure_running()
        except Exception as exc:
            logger.debug("Agent Lights app launch failed: %s", exc)

    def _emit(self, event: LightCueEvent) -> bool:
        with self._lock:
            backend = self._ensure_backend()
            if backend is None:
                # Pool full or the claim lock is busy: retried on the next event.
                return False
            try:
                return bool(backend.emit_event(event))
            except Exception as exc:
                logger.debug("Agent Lights failed to write %s: %s", event.value, exc)
                return False

    def _write_context(self, session_id: Any, model: Any, user_message: Any) -> None:
        if isinstance(user_message, str):
            text = " ".join(user_message.split())
            if len(text) > _CONTEXT_PREVIEW_CHARS:
                text = text[: _CONTEXT_PREVIEW_CHARS - 1] + "…"
            current = f"Working on: {text}" if text else None
        else:
            current = "Working on multimodal input" if user_message else None
        with self._lock:
            backend = self._backend
            emit_context = getattr(backend, "emit_context", None)
            if not callable(emit_context):
                return
            try:
                emit_context(
                    session_id=str(session_id) if session_id else None,
                    current_context=current,
                    chat_summary="Turn in progress",
                    model_name=str(model) if model else None,
                )
            except Exception as exc:
                logger.debug("Agent Lights context write failed: %s", exc)

    # -- turn lifecycle ------------------------------------------------------

    def on_pre_llm_call(self, session_id: Any = None, platform: Any = None, model: Any = None,
                        user_message: Any = None, **_: Any) -> None:
        if not self._activates(platform) or not self._is_root_session(session_id):
            return None
        with self._lock:
            self._active = True
            self._turn_open = True
            self._root_session = str(session_id) if session_id else None
            self._task_outcome = None
            if model:
                self._model = str(model)
            self._responded.discard(session_id)
            self._open_human_requests.clear()
        self._emit(LightCueEvent.WORKING)
        self._write_context(session_id, model, user_message)
        # Never inject context into the turn: returning None keeps the prompt
        # (and its cache) untouched.
        return None

    def on_post_llm_call(self, session_id: Any = None, platform: Any = None,
                         assistant_response: Any = None, **_: Any) -> None:
        if not self._active or not self._activates(platform) or not self._is_root_session(session_id):
            return
        if isinstance(assistant_response, str) and assistant_response.strip():
            with self._lock:
                self._responded.add(session_id)

    def on_session_end(self, session_id: Any = None, platform: Any = None, completed: Any = False,
                       failed: Any = False, interrupted: Any = False, **_: Any) -> None:
        """Per-turn end (Hermes fires it once per ``run_conversation``)."""
        if not self._active or not self._activates(platform) or not self._is_root_session(session_id):
            return
        with self._lock:
            responded = session_id in self._responded
            self._responded.discard(session_id)
            self._open_human_requests.clear()
            self._turn_open = False
            task_outcome = self._task_outcome
        # A Kanban worker's task outcome outlives its turn: a blocked task
        # stays yellow and a completed one red even if the turn then ends
        # without a reply or is interrupted. Only a failed turn overrides it.
        if failed:
            self._emit(LightCueEvent.ERROR)
        elif task_outcome == "blocked":
            self._emit(LightCueEvent.HUMAN_INTERVENTION)
        elif task_outcome == "completed":
            self._emit(LightCueEvent.FINAL_ANSWER)
        elif interrupted:
            self._emit(LightCueEvent.IDLE)
        elif responded:
            self._emit(LightCueEvent.FINAL_ANSWER)
        else:
            self._emit(LightCueEvent.IDLE)

    def on_subagent_start(self, child_session_id: Any = None, **_: Any) -> None:
        if child_session_id:
            with self._lock:
                self._child_sessions.add(child_session_id)

    # -- waiting on a human --------------------------------------------------

    def _human_input_applies(self, session_id: Any) -> bool:
        """Whether a human-input event belongs to the root session's open turn.

        Subagents (including background ones that outlive the parent's turn)
        fire the same hooks; they must not change the parent's dot.
        """
        if not self._active or not self._turn_open:
            return False
        if session_id and session_id in self._child_sessions:
            return False
        if session_id and self._root_session and str(session_id) != self._root_session:
            return False
        return True

    def human_input_requested(self, request_id: str, session_id: Any = None) -> None:
        with self._lock:
            if not self._human_input_applies(session_id):
                return
            self._open_human_requests.add(request_id)
        self._emit(LightCueEvent.HUMAN_INTERVENTION)

    def human_input_resolved(self, request_id: str, session_id: Any = None) -> None:
        with self._lock:
            if not self._human_input_applies(session_id):
                return
            if request_id not in self._open_human_requests:
                return
            self._open_human_requests.discard(request_id)
            still_waiting = bool(self._open_human_requests)
        if not still_waiting:
            self._emit(LightCueEvent.WORKING)

    def on_human_input_request(self, request_id: Any = None, session_id: Any = None, **_: Any) -> None:
        self.human_input_requested(str(request_id or "human-input"), session_id)

    def on_human_input_resolved(self, request_id: Any = None, session_id: Any = None, **_: Any) -> None:
        self.human_input_resolved(str(request_id or "human-input"), session_id)

    # Fallbacks for Hermes builds without on_human_input_*.

    def on_pre_tool_call(self, tool_name: Any = None, tool_call_id: Any = None,
                         session_id: Any = None, **_: Any) -> None:
        if tool_name == "clarify":
            self.human_input_requested(f"clarify:{tool_call_id or ''}", session_id)

    def on_post_tool_call(self, tool_name: Any = None, tool_call_id: Any = None,
                          session_id: Any = None, **_: Any) -> None:
        if tool_name == "clarify":
            self.human_input_resolved(f"clarify:{tool_call_id or ''}", session_id)

    def on_pre_approval_request(self, surface: Any = None, session_id: Any = None, **_: Any) -> None:
        # "smart" approvals are decided by an auxiliary model, not a person.
        if surface != "smart":
            self.human_input_requested("approval", session_id)

    def on_post_approval_response(self, surface: Any = None, session_id: Any = None, **_: Any) -> None:
        if surface != "smart":
            self.human_input_resolved("approval", session_id)

    # -- Kanban workers ------------------------------------------------------

    def _is_own_task(self, task_id: Any) -> bool:
        own = self._kanban_task()
        return bool(own) and str(task_id or "") == own

    def on_kanban_task_blocked(self, task_id: Any = None, **_: Any) -> None:
        if self._is_own_task(task_id):
            with self._lock:
                self._task_outcome = "blocked"
            self._emit(LightCueEvent.HUMAN_INTERVENTION)

    def on_kanban_task_completed(self, task_id: Any = None, **_: Any) -> None:
        if self._is_own_task(task_id):
            with self._lock:
                self._task_outcome = "completed"
            self._emit(LightCueEvent.FINAL_ANSWER)


def hook_table(producer: AgentLightsProducer, valid_hooks: set[str] | frozenset[str]) -> dict[str, Callable[..., Any]]:
    """The hooks to register, choosing human-input hooks this Hermes supports."""
    hooks: dict[str, Callable[..., Any]] = {
        "pre_llm_call": producer.on_pre_llm_call,
        "post_llm_call": producer.on_post_llm_call,
        "on_session_end": producer.on_session_end,
        "subagent_start": producer.on_subagent_start,
        "kanban_task_blocked": producer.on_kanban_task_blocked,
        "kanban_task_completed": producer.on_kanban_task_completed,
    }
    if all(name in valid_hooks for name in _HUMAN_INPUT_HOOKS):
        hooks["on_human_input_request"] = producer.on_human_input_request
        hooks["on_human_input_resolved"] = producer.on_human_input_resolved
    else:
        hooks["pre_tool_call"] = producer.on_pre_tool_call
        hooks["post_tool_call"] = producer.on_post_tool_call
        hooks["pre_approval_request"] = producer.on_pre_approval_request
        hooks["post_approval_response"] = producer.on_post_approval_response
    return {name: callback for name, callback in hooks.items() if name in valid_hooks}


def fork_producer_present() -> bool:
    """True when Hermes core already writes the current (v2) Agent Lights pool.

    That is the Hermes fork Agent Lights started in. An older core patch
    (pre-v2) writes a pool the current app ignores, so the plugin still runs
    beside it rather than leave the session without a dot.
    """
    try:
        if importlib.util.find_spec("agent.light_cues") is None:
            return False
        module = importlib.import_module("agent.light_cues")
    except Exception:
        return False
    return getattr(module, "_AGENT_LIGHTS_POOL", None) == "v2"


def register(ctx) -> None:
    if sys.platform != "darwin":
        logger.info("agent-lights: the menu-bar app is macOS only; plugin inactive.")
        return
    if fork_producer_present():
        # Two producers in one process would claim two slots for one session.
        logger.info("agent-lights: this Hermes writes Agent Lights (v2) from core; plugin inactive.")
        return
    from hermes_cli.plugins import VALID_HOOKS

    producer = AgentLightsProducer()
    for name, callback in hook_table(producer, VALID_HOOKS).items():
        ctx.register_hook(name, callback)
