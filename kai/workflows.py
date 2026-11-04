"""Temporal workflows. Branching calls the pure engine. Side effects are activities."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from kai.engine.advance import drive, initial_state, reply_port, reply_slots
    from kai.engine.prompts import build_prompt
    from kai.engine.slots import decide_band, grounded_reply, idle_line, missing_slot, slot_question

TASK_TIMEOUT = timedelta(seconds=30)


def _public(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_id": state.get("step_id"),
        "status": state.get("status"),
        "slots": state.get("slots"),
        "trace": state.get("trace"),
        "messages": state.get("messages"),
        "result": state.get("result"),
        "pending": state.get("pending"),
        "move_id": state.get("move_id"),
    }


@workflow.defn
class RunWorkflow:
    def __init__(self) -> None:
        self.reply_text: str | None = None
        self.view: dict[str, Any] = {}
        self._spoken_count = 0

    @workflow.signal
    def reply(self, text: str) -> None:
        self.reply_text = text

    @workflow.query
    def state(self) -> dict[str, Any]:
        return self.view

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        graph = payload["graph"]
        steps = {step["id"]: step for step in graph["steps"]}
        state = initial_state(graph, payload["origin"], payload.get("slots") or {})
        timings: dict[str, float] = {}
        events: list[dict[str, Any]] = []
        replies: list[str] = []
        self.view = _public(state)
        while state["status"] not in {"completed", "blocked"}:
            step = steps[state["step_id"]]
            started = workflow.now()
            if state["pending"] == "fetch":
                result = await workflow.execute_activity(
                    "fetch_step",
                    {
                        "tenant_id": payload["tenant_id"],
                        "run_id": payload["run_id"],
                        "step": step,
                        "slots": state["slots"],
                        "ledger": payload.get("ledger") or {},
                    },
                    start_to_close_timeout=TASK_TIMEOUT,
                    activity_id=f"{payload['run_id']}:{step['id']}:fetch",
                )
                events.append(result["event"])
                state = drive(state, graph, {"step_id": step["id"], "port": "continue", "data": result["data"], "event": result["event"]})
            elif state["pending"] == "action":
                result = await workflow.execute_activity(
                    "act_step",
                    {
                        "tenant_id": payload["tenant_id"],
                        "run_id": payload["run_id"],
                        "step": step,
                        "slots": state["slots"],
                        "ledger": payload.get("ledger") or {},
                    },
                    start_to_close_timeout=TASK_TIMEOUT,
                    activity_id=f"{payload['run_id']}:{step['id']}:attempt",
                )
                events.append(result["event"])
                if isinstance(result.get("ledger"), dict) and (result["ledger"].get("accounts") or {}):
                    payload["ledger"] = result["ledger"]
                state = drive(state, graph, {"step_id": step["id"], "port": "continue", "data": result["data"], "event": result["event"]})
            elif state["pending"] == "wait":
                self.view = _public(state)
                self.view["prompt"] = build_prompt(step, state.get("slots") or {}, graph)
                await self._persist(payload, state, timings, events, replies)
                await self._speak_new(payload, state)
                timeout = int((step.get("config") or {}).get("ttl") or (30 if step["kind"] == "approval" else 86400))
                try:
                    await workflow.wait_condition(lambda: self.reply_text is not None, timeout=timedelta(seconds=timeout))
                    text = self.reply_text
                    self.reply_text = None
                except asyncio.TimeoutError:
                    text = None
                if text is not None:
                    replies.append(text)
                port = reply_port(step, text, state["slots"], graph)
                state = drive(
                    state,
                    graph,
                    {
                        "step_id": step["id"],
                        "port": port,
                        "data": reply_slots(step, text, state["slots"], graph),
                        "event": {"type": "reply" if text is not None else "timeout", "step_id": step["id"], "text": text, "port": port},
                    },
                )
            else:
                state["status"] = "blocked"
                break
            timings[step["id"]] = max(timings.get(step["id"], 0), (workflow.now() - started).total_seconds() * 1000)
            self.view = _public(state)
            self.view["prompt"] = None
            await self._speak_new(payload, state)
        await self._persist(payload, state, timings, events, replies)
        return self.view

    async def _speak_new(self, payload: dict[str, Any], state: dict[str, Any]) -> None:
        spoken = state.get("messages") or []
        fresh = spoken[self._spoken_count :]
        self._spoken_count = len(spoken)
        if not payload.get("conversation_id"):
            return
        for index, message in enumerate(fresh):
            text = message.get("text") if isinstance(message, dict) else ""
            if not text:
                continue
            await workflow.execute_activity(
                "append_message",
                {
                    "tenant_id": payload["tenant_id"],
                    "conversation_id": payload.get("conversation_id"),
                    "role": "kai",
                    "text": text,
                },
                activity_id=f"{payload['run_id']}:line:{self._spoken_count}:{index}",
                start_to_close_timeout=TASK_TIMEOUT,
            )

    async def _persist(self, payload: dict[str, Any], state: dict[str, Any], timings: dict[str, float], events: list[dict], replies: list[str]) -> None:
        await workflow.execute_activity(
            "persist_run",
            {
                "tenant_id": payload["tenant_id"],
                "run_id": payload["run_id"],
                "workflow_id": payload.get("workflow_id"),
                "version_id": payload.get("version_id"),
                "origin": payload.get("origin"),
                "conversation_id": payload.get("conversation_id"),
                "status": "completed" if state["status"] == "completed" else state["status"],
                "step_id": state["step_id"],
                "slots": state["slots"],
                "trace": state["trace"],
                "result": state.get("result"),
                "ledger": payload.get("ledger") or {},
                "messages": state.get("messages") or [],
                "timings_ms": timings,
                "user_replies": replies,
                "slots_from_message": payload.get("slots_from_message") or {},
                "missing_slot_count": payload.get("missing_slot_count") or 0,
                "customer_id": payload.get("customer_id"),
                "rule_id": payload.get("rule_id"),
                "events": events,
                "graph": payload["graph"] if state["status"] == "completed" else None,
                "contract": payload.get("contract") if state["status"] == "completed" else None,
                "eval_config": payload.get("eval_config") if state["status"] == "completed" else None,
            },
            start_to_close_timeout=TASK_TIMEOUT,
        )


@workflow.defn
class ConversationWorkflow:
    def __init__(self) -> None:
        self.inbox: list[str] = []
        self.view: dict[str, Any] = {
            "status": "idle",
            "band": None,
            "confidence": None,
            "slots": {},
            "slot_from": {},
            "trace": [],
            "messages": [],
            "waiting_on": None,
            "child_id": None,
            "workflow_key": None,
        }
        self.child_id: str | None = None
        self.phase = "idle"
        self.pending_slot: str | None = None
        self.partial: dict[str, Any] = {}
        self.slot_from: dict[str, str] = {}
        self.signal_count = 0
        self.chosen: dict[str, Any] | None = None
        self.objection_count = 0
        self.objection_step = ""

    @workflow.signal
    def user_message(self, text: str) -> None:
        self.inbox.append(text)

    @workflow.query
    def state(self) -> dict[str, Any]:
        return self.view

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> None:
        self.child_id = payload.get("child_id")
        self.phase = payload.get("phase") or "idle"
        self.pending_slot = payload.get("pending_slot")
        self.partial = payload.get("partial") or {}
        self.slot_from = payload.get("slot_from") or {}
        self.signal_count = int(payload.get("signal_count") or 0)
        self.chosen = payload.get("chosen")
        self.objection_count = int(payload.get("objection_count") or 0)
        self.objection_step = str(payload.get("objection_step") or "")
        self._refresh_view()
        while True:
            await workflow.wait_condition(lambda: len(self.inbox) > 0)
            text = self.inbox.pop(0)
            self.signal_count += 1
            await self._on_message(payload, text)
            if self.signal_count >= 50:
                workflow.continue_as_new(
                    {
                        **payload,
                        "child_id": self.child_id,
                        "phase": self.phase,
                        "pending_slot": self.pending_slot,
                        "partial": self.partial,
                        "slot_from": self.slot_from,
                        "signal_count": 0,
                        "chosen": self.chosen,
                        "objection_count": self.objection_count,
                        "objection_step": self.objection_step,
                    }
                )

    async def _on_message(self, payload: dict[str, Any], text: str) -> None:
        if self.phase == "in_run" and self.child_id:
            waiting = await workflow.execute_activity(
                "waiting_prompt",
                {"run_id": self.child_id, "graph": (self.chosen or {}).get("graph") or {}},
                start_to_close_timeout=TASK_TIMEOUT,
            )
            if waiting.get("finished"):
                self.child_id = None
                self.phase = "idle"
                self.objection_count = 0
                self.objection_step = ""
                self.view["prompt"] = None
                self._refresh_view()
            else:
                prompt = waiting.get("prompt") or {}
                step_id = str(prompt.get("step_id") or "")
                if step_id and step_id != self.objection_step:
                    self.objection_count = 0
                    self.objection_step = step_id
                decision = await self._interpret(payload, text, prompt)
                if decision.get("strike"):
                    self.objection_count = int(decision["strike"])
                    self.objection_step = step_id
                if decision.get("action") == "choice" and (decision.get("text") or decision.get("port")):
                    self.objection_count = 0
                    self.objection_step = ""
                    if await self._forward_if_busy(decision.get("text") or decision.get("port")):
                        return
                if decision.get("action") == "start" and decision.get("workflow_key"):
                    self.objection_count = 0
                    self.objection_step = ""
                    await self._leave_run(payload, prompt)
                    loaded = await workflow.execute_activity(
                        "load_contracts",
                        {"tenant_id": payload["tenant_id"], "workflow_id": payload.get("workflow_id") or ""},
                        start_to_close_timeout=TASK_TIMEOUT,
                    )
                    await self._follow(payload, loaded, decision)
                    return
                await self._say(payload, self._spoken({"contracts": []}, decision) or prompt.get("text") or "")
                return
        loaded = await workflow.execute_activity(
            "load_contracts",
            {"tenant_id": payload["tenant_id"], "workflow_id": payload.get("workflow_id") or ""},
            start_to_close_timeout=TASK_TIMEOUT,
        )
        decision = await self._interpret(payload, text, None, loaded["contracts"])
        await self._follow(payload, loaded, decision)

    async def _leave_run(self, payload: dict[str, Any], prompt: dict[str, Any]) -> None:
        """Drop the open run so a different published workflow can start."""
        ports = set(prompt.get("ports") or [])
        for choice in prompt.get("choices") or []:
            if isinstance(choice, dict) and choice.get("port"):
                ports.add(str(choice["port"]))
        leave = next((name for name in ("objection", "not_now", "no", "cancel") if name in ports), "")
        if leave and self.child_id:
            await self._forward_if_busy(leave)
        elif self.child_id:
            handle = workflow.get_external_workflow_handle_for(RunWorkflow.run, self.child_id)
            try:
                await handle.cancel()
            except Exception:
                pass
        self.child_id = None
        self.phase = "idle"
        self.chosen = None
        self.partial = {}
        self.slot_from = {}
        self.pending_slot = None
        self.objection_count = 0
        self.objection_step = ""
        self.view["prompt"] = None
        self.view["workflow_key"] = None
        self._refresh_view()

    async def _interpret(self, payload: dict[str, Any], text: str, prompt: dict[str, Any] | None, contracts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        try:
            return await workflow.execute_activity(
                "interpret_turn",
                {
                    "text": text,
                    "contracts": contracts or [],
                    "phase": self.phase,
                    "pending_slot": self.pending_slot or "",
                    "workflow_key": (self.chosen or {}).get("workflow_key") or "",
                    "pinned_workflow_id": payload.get("workflow_id") or "",
                    "filled": self.partial,
                    "prompt": prompt or {},
                    "conversation_id": payload.get("conversation_id") or "",
                    "tenant_id": payload.get("tenant_id") or "",
                    "objection_count": self.objection_count,
                    "objection_step": self.objection_step,
                },
                start_to_close_timeout=TASK_TIMEOUT,
            )
        except Exception:
            return {"action": "chat", "workflow_key": "", "confidence": 0, "slots": {}, "needs": "none", "reply": idle_line(contracts), "port": ""}

    def _match(self, loaded: dict[str, Any], workflow_key: str) -> dict[str, Any] | None:
        return next((item for item in loaded.get("contracts") or [] if item.get("workflow_key") == workflow_key), None)

    def _spoken(self, loaded: dict[str, Any], decision: dict[str, Any]) -> str:
        match = self.chosen or self._match(loaded, decision.get("workflow_key") or "")
        if match is None and decision.get("needs") not in {None, "", "none"}:
            match = next((item for item in loaded.get("contracts") or [] if (item.get("ledger") or {}).get("accounts")), None)
        ledger = decision.get("ledger") or (match or {}).get("ledger") or {}
        pending = self.pending_slot if self.phase == "slot" else ""
        line = grounded_reply(decision, ledger, pending or "")
        idle = idle_line([self.chosen] if self.chosen else [])
        return line or decision.get("reply") or (slot_question(pending, ((self.chosen or {}).get("contract") or {}).get("slots")) if pending else idle)

    async def _follow(self, payload: dict[str, Any], loaded: dict[str, Any], decision: dict[str, Any]) -> None:
        if decision.get("needs") not in {None, "", "none"} or decision.get("action") == "answer":
            await self._say(payload, self._spoken(loaded, decision))
            return
        if decision.get("action") == "chat" or not decision.get("workflow_key"):
            await self._say(payload, decision.get("reply") or idle_line(loaded.get("contracts")))
            return
        match = self._match(loaded, decision["workflow_key"])
        if match is None:
            await self._say(payload, decision.get("reply") or idle_line(loaded.get("contracts")))
            return
        contract = match.get("contract") or {}
        band = decide_band(float(decision.get("confidence") or 0), contract.get("bands") or {})
        self.view["band"] = band
        self.view["confidence"] = decision.get("confidence")
        self.view["workflow_key"] = match["workflow_key"]
        switching = self.chosen is not None and self.chosen.get("workflow_key") != match["workflow_key"]
        if self.chosen is None or (switching and band == "start"):
            if self.chosen is None and band == "not_this":
                await self._say(payload, decision.get("reply") or idle_line(loaded.get("contracts")))
                return
            if self.chosen is None and band == "ask" and not decision.get("slots"):
                entry = ((match.get("graph") or {}).get("entries") or {}).get("chat") or "chat_request"
                await self._say(payload, (contract.get("lines") or {}).get(entry) or contract.get("setup") or "Do you want to top an account up?")
                return
            if switching:
                self.partial = {}
                self.slot_from = {}
            self.chosen = match
        for name, item in (decision.get("slots") or {}).items():
            if item.get("value") in (None, ""):
                continue
            if name in self.partial and name != self.pending_slot and self.phase == "slot":
                continue
            self.partial[name] = item.get("value")
            self.slot_from[name] = item.get("from") or "the message"
        await self._continue_slots(payload)

    async def _continue_slots(self, payload: dict[str, Any]) -> None:
        contract = (self.chosen or {}).get("contract") or {}
        missing = missing_slot(contract.get("slots") or [], self.partial)
        if missing:
            self.phase = "slot"
            self.pending_slot = missing["key"]
            await self._say(payload, slot_question(missing["key"], contract.get("slots")))
            self._refresh_view()
            return
        self.phase = "in_run"
        self.pending_slot = None
        await self._start_child(payload)

    async def _start_child(self, payload: dict[str, Any]) -> None:
        chosen = self.chosen or {}
        run_id = f"run:{payload['tenant_id']}:{workflow.uuid4()}"
        self.child_id = run_id
        missing_count = sum(1 for key, source in self.slot_from.items() if source == "asked")
        await workflow.start_child_workflow(
            RunWorkflow.run,
            {
                "tenant_id": payload["tenant_id"],
                "run_id": run_id,
                "workflow_id": chosen.get("workflow_id"),
                "version_id": chosen.get("version_id"),
                "graph": chosen.get("graph"),
                "contract": chosen.get("contract"),
                "eval_config": chosen.get("eval_config") or {},
                "origin": "chat",
                "slots": self.partial,
                "ledger": chosen.get("ledger") or {},
                "conversation_id": payload["conversation_id"],
                "slots_from_message": {key: value for key, value in self.partial.items() if self.slot_from.get(key) == "the message"},
                "missing_slot_count": missing_count,
            },
            id=run_id,
            parent_close_policy=workflow.ParentClosePolicy.ABANDON,
        )
        self._refresh_view()

    def _reply_code(self, decision: dict[str, Any]) -> str:
        for key in ("source", "target", "account"):
            item = (decision.get("slots") or {}).get(key) or {}
            value = item.get("value") if isinstance(item, dict) else item
            if isinstance(value, str) and value:
                return value
        return ""

    async def _forward_if_busy(self, text: str) -> bool:
        if not self.child_id or self.phase != "in_run":
            return False
        handle = workflow.get_external_workflow_handle_for(RunWorkflow.run, self.child_id)
        try:
            await handle.signal(RunWorkflow.reply, text)
        except Exception:
            self.child_id = None
            self.phase = "idle"
            self.view["child_id"] = None
            return False
        self.view["child_id"] = self.child_id
        self.view["status"] = self.phase
        return True

    async def _say(self, payload: dict[str, Any], text: str) -> None:
        if not text:
            return
        self.view.setdefault("messages", []).append(text)
        await workflow.execute_activity(
            "append_message",
            {
                "tenant_id": payload["tenant_id"],
                "conversation_id": payload["conversation_id"],
                "role": "kai",
                "text": text,
            },
            activity_id=f"{payload['conversation_id']}:kai:{self.signal_count}:{len(self.view.get('messages') or [])}",
            start_to_close_timeout=TASK_TIMEOUT,
        )

    def _refresh_view(self) -> None:
        self.view["slots"] = self.partial
        self.view["slot_from"] = self.slot_from
        self.view["child_id"] = self.child_id
        self.view["status"] = self.phase
        self.view["waiting_on"] = self.pending_slot
