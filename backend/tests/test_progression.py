"""Tests for the Delivery 7 live-trading safety progression.

Covers: stage ordering (no skipping), evidence gating per stage, refusal reasons,
human confirmation per transition/trade (single-use, expiring, never auto-sized),
live being unreachable by default, bounded SCALE authorization, persistence
default = BACKTEST / live off, and notifications never breaking the flow.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from halaltrade.api import create_app
from halaltrade.config import Settings
from halaltrade.db import create_session, make_engine
from halaltrade.notifications.telegram import AlertType
from halaltrade.progression import (
    ConfirmationPurpose,
    ConfirmationStore,
    DbProgressionStore,
    NullProgressionStore,
    ProgressionMachine,
    ProgressionStage,
    StageEvidence,
    TradeConfirmationGate,
    live_execution_implemented,
)

BACKTEST_EVIDENCE = dict(
    backtest_run_id="bt-001",
    backtest_trades=40,
    backtest_profit_factor=1.6,
    backtest_max_drawdown=0.18,
    walk_forward_passed=True,
)
PAPER_EVIDENCE = dict(
    paper_days=31.0,
    paper_trades=42,
    paper_profit_factor=1.4,
    paper_max_drawdown=0.10,
)
EXTENDED_EVIDENCE = dict(
    extended_paper_days=31.0,
    extended_paper_trades=36,
    extended_paper_profit_factor=1.2,
    extended_paper_max_drawdown=0.10,
)
SMALL_LIVE_EVIDENCE = dict(
    small_live_days=31.0,
    small_live_trades=25,
    small_live_profit_factor=1.3,
    small_live_max_drawdown=0.12,
)


class RecordingNotifier:
    """Fake notifier: records (event, alert_type, text) triples."""

    def __init__(self, *, boom: bool = False) -> None:
        self.calls: list[tuple[AlertType, str]] = []
        self.events: list[str] = []
        self.boom = boom

    async def send(self, alert_type: AlertType, text: str) -> bool:
        if self.boom:
            raise RuntimeError("telegram exploded")
        self.calls.append((alert_type, text))
        self.events.append(text)
        return True


def live_ready_settings(**overrides) -> Settings:
    base = dict(
        trading_mode="live",
        live_enabled=True,
        api_key_restricted=True,
        system_healthy=True,
    )
    base.update(overrides)
    return Settings(**base)


def fresh_machine(*, settings: Settings | None = None, notifier=None, store=None, limits=None):
    return ProgressionMachine(
        settings=settings or live_ready_settings(),
        notifier=notifier if notifier is not None else RecordingNotifier(),
        store=store if store is not None else NullProgressionStore(),
        limits=limits,
    )


async def walk_to_stage(machine: ProgressionMachine, stage: ProgressionStage) -> None:
    """Drive the machine to ``stage`` through the public two-step API."""
    plan = [
        (ProgressionStage.PAPER, BACKTEST_EVIDENCE),
        (ProgressionStage.EXTENDED_PAPER_30D, PAPER_EVIDENCE),
        (ProgressionStage.SMALL_LIVE, EXTENDED_EVIDENCE),
        (ProgressionStage.SCALE, SMALL_LIVE_EVIDENCE),
    ]
    for target, evidence in plan:
        clip = 25.0 if target is ProgressionStage.SCALE else None
        request = await machine.request_transition(
            target, evidence_updates=evidence, clip_usdt=clip
        )
        assert request.status == "AWAITING_CONFIRMATION", request.failed
        applied = await machine.confirm_transition(
            request.token,
            clip_usdt=clip,
            human_authorized_scale=(target is ProgressionStage.SCALE),
        )
        assert applied.status == "APPLIED", applied.failed
        if target is stage:
            return
    raise AssertionError(f"stage {stage} not reachable by helper")


# --------------------------------------------------------------------------- defaults
def test_default_stage_is_backtest_with_live_off() -> None:
    machine = ProgressionMachine(settings=Settings(trading_mode="paper", live_enabled=False))
    assert machine.current_stage() is ProgressionStage.BACKTEST
    assert machine.state.live_enabled is False
    assert machine.live_authorized() is False


def test_live_execution_is_not_implemented() -> None:
    assert live_execution_implemented() is False
    machine = fresh_machine()
    assert machine.snapshot()["live_execution_implemented"] is False


def test_empty_database_defaults_to_backtest_and_live_off() -> None:
    session_factory = create_session(make_engine("sqlite://"))
    machine = ProgressionMachine(settings=live_ready_settings(), store=DbProgressionStore(session_factory))
    assert machine.current_stage() is ProgressionStage.BACKTEST
    assert machine.state.live_enabled is False
    assert machine.evidence == StageEvidence()
    assert machine.snapshot()["history"] == []


# --------------------------------------------------------------------------- ordering
@pytest.mark.asyncio
async def test_cannot_skip_stages() -> None:
    machine = fresh_machine()
    result = await machine.request_transition(ProgressionStage.SMALL_LIVE, evidence_updates=BACKTEST_EVIDENCE)
    assert result.status == "REFUSED"
    assert result.to_stage is ProgressionStage.BACKTEST
    assert any("one at a time" in reason for reason in result.failed)


@pytest.mark.asyncio
async def test_cannot_move_backwards() -> None:
    machine = fresh_machine()
    await walk_to_stage(machine, ProgressionStage.PAPER)
    result = await machine.request_transition(ProgressionStage.BACKTEST)
    assert result.status == "REFUSED"
    assert machine.current_stage() is ProgressionStage.PAPER


@pytest.mark.asyncio
async def test_unknown_stage_is_refused_with_reason() -> None:
    machine = fresh_machine()
    result = await machine.request_transition("MOON")
    assert result.status == "REFUSED"
    assert any("unknown progression stage" in reason for reason in result.failed)


@pytest.mark.asyncio
async def test_already_at_stage_is_refused() -> None:
    machine = fresh_machine()
    result = await machine.request_transition(ProgressionStage.BACKTEST)
    assert result.status == "REFUSED"
    assert any("already at stage" in reason for reason in result.failed)


# --------------------------------------------------------------------------- evidence gates
@pytest.mark.asyncio
async def test_paper_requires_backtest_evidence() -> None:
    machine = fresh_machine()
    result = await machine.request_transition(ProgressionStage.PAPER)
    assert result.status == "REFUSED"
    assert any("no backtest run recorded" in reason for reason in result.failed)
    assert any("profit factor" in reason for reason in result.failed)


@pytest.mark.asyncio
async def test_paper_refused_on_weak_backtest() -> None:
    machine = fresh_machine()
    result = await machine.request_transition(
        ProgressionStage.PAPER,
        evidence_updates=dict(BACKTEST_EVIDENCE, backtest_profit_factor=0.8, backtest_trades=3),
    )
    assert result.status == "REFUSED"
    assert any("backtest trades 3 <" in reason for reason in result.failed)
    assert any("backtest profit factor 0.80 <=" in reason for reason in result.failed)


@pytest.mark.asyncio
async def test_extended_paper_requires_thirty_days() -> None:
    machine = fresh_machine()
    await walk_to_stage(machine, ProgressionStage.PAPER)
    result = await machine.request_transition(
        ProgressionStage.EXTENDED_PAPER_30D,
        evidence_updates=dict(PAPER_EVIDENCE, paper_days=12.0),
    )
    assert result.status == "REFUSED"
    assert any("paper trading duration 12.0 days <" in reason for reason in result.failed)


@pytest.mark.asyncio
async def test_small_live_refused_when_live_readiness_not_ready() -> None:
    machine = fresh_machine(settings=Settings(trading_mode="paper", live_enabled=False))
    await walk_to_stage(machine, ProgressionStage.EXTENDED_PAPER_30D)
    result = await machine.request_transition(
        ProgressionStage.SMALL_LIVE, evidence_updates=EXTENDED_EVIDENCE
    )
    assert result.status == "REFUSED"
    # LiveReadinessCheck's own reason strings are surfaced verbatim (reuse, not re-implementation)
    assert any("user has NOT explicitly enabled live mode" in reason for reason in result.failed)
    assert any("API key withdrawal permission" in reason for reason in result.failed)


@pytest.mark.asyncio
async def test_small_live_requires_the_two_human_acknowledgements() -> None:
    machine = fresh_machine()
    await walk_to_stage(machine, ProgressionStage.EXTENDED_PAPER_30D)
    result = await machine.request_transition(
        ProgressionStage.SMALL_LIVE, evidence_updates=EXTENDED_EVIDENCE
    )
    assert result.status == "REFUSED"
    assert any("financial risk" in reason for reason in result.failed)
    assert any("Shariah compliance" in reason for reason in result.failed)


@pytest.mark.asyncio
async def test_small_live_reached_but_live_still_off() -> None:
    machine = fresh_machine()
    await walk_to_stage(
        machine,
        ProgressionStage.SMALL_LIVE,
    )
    assert machine.current_stage() is ProgressionStage.SMALL_LIVE
    assert machine.state.live_enabled is False
    assert machine.live_authorized() is False


# --------------------------------------------------------------------------- confirmation flow
@pytest.mark.asyncio
async def test_transition_needs_a_human_confirmation_token() -> None:
    machine = fresh_machine()
    result = await machine.request_transition(ProgressionStage.PAPER, evidence_updates=BACKTEST_EVIDENCE)
    assert result.status == "AWAITING_CONFIRMATION"
    assert result.token
    assert result.required_fields == ("stage",)
    assert machine.current_stage() is ProgressionStage.BACKTEST  # not applied yet


@pytest.mark.asyncio
async def test_confirmation_token_is_single_use() -> None:
    machine = fresh_machine()
    request = await machine.request_transition(ProgressionStage.PAPER, evidence_updates=BACKTEST_EVIDENCE)
    assert (await machine.confirm_transition(request.token)).status == "APPLIED"
    again = await machine.confirm_transition(request.token)
    assert again.status == "REFUSED"
    assert any("not recognised" in reason for reason in again.failed)


@pytest.mark.asyncio
async def test_confirmation_token_from_wrong_purpose_is_refused() -> None:
    store = ConfirmationStore()
    gate = TradeConfirmationGate(store)
    authorization = gate.request_authorization(client_order_id="c1", side="BUY", size=100.0)
    machine = fresh_machine()
    machine.confirmations = store
    machine.gate = TradeConfirmationGate(store)
    result = await machine.confirm_transition(authorization.token)
    assert result.status == "REFUSED"
    assert any("not recognised" in reason for reason in result.failed)


def test_confirmation_token_expires() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = ConfirmationStore(ttl_seconds=60, clock=lambda: now)
    pending = store.request(purpose=ConfirmationPurpose.TRANSITION, target="PAPER", required_fields=("stage",))
    later = store
    later._clock = lambda: now + timedelta(seconds=120)  # type: ignore[attr-defined]
    result = later.confirm(pending.token, payload={"stage": "PAPER"}, purpose=ConfirmationPurpose.TRANSITION)
    assert result.confirmed is False
    assert any("expired" in reason for reason in result.failed)


def test_confirmation_requires_user_supplied_fields() -> None:
    store = ConfirmationStore()
    pending = store.request(
        purpose=ConfirmationPurpose.TRANSITION, target="PAPER", required_fields=("size",)
    )
    result = store.confirm(pending.token, payload={}, purpose=ConfirmationPurpose.TRANSITION)
    assert result.confirmed is False
    assert any("required field 'size' missing" in reason for reason in result.failed)


# --------------------------------------------------------------------------- live enable
@pytest.mark.asyncio
async def test_live_enable_refused_before_small_live_stage() -> None:
    machine = fresh_machine()
    await walk_to_stage(machine, ProgressionStage.PAPER)
    result = await machine.request_live_enable()
    assert result.status == "REFUSED"
    assert any("not live-capable" in reason for reason in result.failed)
    assert machine.state.live_enabled is False


@pytest.mark.asyncio
async def test_live_enable_refused_when_readiness_fails() -> None:
    machine = fresh_machine(settings=Settings(trading_mode="paper", live_enabled=False))
    await walk_to_stage(machine, ProgressionStage.SMALL_LIVE)
    result = await machine.request_live_enable()
    assert result.status == "REFUSED"
    assert any("user has NOT explicitly enabled live mode" in reason for reason in result.failed)


@pytest.mark.asyncio
async def test_live_enable_is_two_step_and_bounded() -> None:
    machine = fresh_machine()
    await walk_to_stage(machine, ProgressionStage.SMALL_LIVE)
    request = await machine.request_live_enable()
    assert request.status == "AWAITING_CONFIRMATION"
    assert machine.state.live_enabled is False  # not enabled by the request alone
    applied = await machine.confirm_live_enable(request.token)
    assert applied.status == "APPLIED"
    assert machine.state.live_enabled is True
    assert applied.to_dict()["live_execution_implemented"] is False


@pytest.mark.asyncio
async def test_live_can_always_be_disabled() -> None:
    machine = fresh_machine()
    await walk_to_stage(machine, ProgressionStage.SMALL_LIVE)
    request = await machine.request_live_enable()
    await machine.confirm_live_enable(request.token)
    assert machine.state.live_enabled is True
    result = await machine.disable_live(reason="test")
    assert result.status == "APPLIED"
    assert machine.state.live_enabled is False


# --------------------------------------------------------------------------- SCALE
@pytest.mark.asyncio
async def test_scale_requires_explicit_clip_inside_hard_cap() -> None:
    machine = fresh_machine()
    await walk_to_stage(machine, ProgressionStage.SMALL_LIVE)
    result = await machine.request_transition(
        ProgressionStage.SCALE, evidence_updates=SMALL_LIVE_EVIDENCE
    )
    assert result.status == "REFUSED"
    assert any("no clip supplied" in reason for reason in result.failed)
    too_big = await machine.request_transition(
        ProgressionStage.SCALE, evidence_updates=SMALL_LIVE_EVIDENCE, clip_usdt=10_000.0
    )
    assert too_big.status == "REFUSED"
    assert any("hard" in reason for reason in too_big.failed)


@pytest.mark.asyncio
async def test_scale_requires_second_human_authorization() -> None:
    machine = fresh_machine()
    await walk_to_stage(machine, ProgressionStage.SMALL_LIVE)
    request = await machine.request_transition(
        ProgressionStage.SCALE, evidence_updates=SMALL_LIVE_EVIDENCE, clip_usdt=25.0
    )
    assert request.status == "AWAITING_CONFIRMATION"
    refused = await machine.confirm_transition(request.token, clip_usdt=25.0, human_authorized_scale=False)
    assert refused.status == "REFUSED"
    assert any("no additional explicit human authorization" in reason for reason in refused.failed)
    # token was deliberately left unused so the user can retry after reading the reasons
    applied = await machine.confirm_transition(request.token, clip_usdt=25.0, human_authorized_scale=True)
    assert applied.status == "APPLIED"
    assert machine.current_stage() is ProgressionStage.SCALE
    assert machine.state.clip_usdt == 25.0


@pytest.mark.asyncio
async def test_scale_requires_a_small_live_track_record() -> None:
    machine = fresh_machine()
    await walk_to_stage(machine, ProgressionStage.SMALL_LIVE)
    result = await machine.request_transition(
        ProgressionStage.SCALE, evidence_updates=dict(small_live_trades=0), clip_usdt=25.0
    )
    assert result.status == "REFUSED"
    assert any("small-live trades 0 <" in reason for reason in result.failed)


# --------------------------------------------------------------------------- persistence
@pytest.mark.asyncio
async def test_stage_and_evidence_survive_a_restart() -> None:
    store = DbProgressionStore(create_session(make_engine("sqlite://")))
    machine = ProgressionMachine(settings=live_ready_settings(), store=store)
    await walk_to_stage(machine, ProgressionStage.PAPER)
    reloaded = ProgressionMachine(settings=live_ready_settings(), store=store)
    assert reloaded.current_stage() is ProgressionStage.PAPER
    assert reloaded.evidence.backtest_run_id == "bt-001"
    assert reloaded.state.live_enabled is False


def test_unknown_evidence_key_is_refused() -> None:
    machine = fresh_machine()
    with pytest.raises(ValueError):
        machine.record_evidence(profit_factor=99.0)


# --------------------------------------------------------------------------- trade gate
def test_trade_requires_user_supplied_size() -> None:
    gate = TradeConfirmationGate(ConfirmationStore())
    refused = gate.request_authorization(client_order_id="c1", side="BUY", size=None)
    assert refused.status == "REFUSED"
    assert any("never chooses or defaults the size" in reason for reason in refused.failed)
    zero = gate.request_authorization(client_order_id="c1", side="BUY", size=0.0)
    assert zero.status == "REFUSED"


def test_trade_requires_and_consumes_confirmation_token() -> None:
    gate = TradeConfirmationGate(ConfirmationStore())
    assert gate.authorize(client_order_id="c1", size=100.0, token="").authorized is False
    request = gate.request_authorization(client_order_id="c1", side="BUY", size=100.0)
    authorized = gate.authorize(client_order_id="c1", size=100.0, token=request.token)
    assert authorized.authorized is True
    reused = gate.authorize(client_order_id="c1", size=100.0, token=request.token)
    assert reused.authorized is False


def test_trade_confirmation_must_match_size_and_order_id() -> None:
    gate = TradeConfirmationGate(ConfirmationStore())
    request = gate.request_authorization(client_order_id="c1", side="BUY", size=100.0)
    mismatched = gate.authorize(client_order_id="c1", size=90.0, token=request.token)
    assert mismatched.authorized is False
    assert any("does not match" in reason for reason in mismatched.failed)
    still_usable = gate.authorize(client_order_id="c1", size=100.0, token=request.token)
    assert still_usable.authorized is True


# --------------------------------------------------------------------------- notifications
@pytest.mark.asyncio
async def test_notification_failure_never_breaks_a_transition() -> None:
    machine = fresh_machine(notifier=None)
    machine.notifier = RecordingNotifier(boom=True)
    request = await machine.request_transition(ProgressionStage.PAPER, evidence_updates=BACKTEST_EVIDENCE)
    assert request.status == "AWAITING_CONFIRMATION"
    applied = await machine.confirm_transition(request.token)
    assert applied.status == "APPLIED"


@pytest.mark.asyncio
async def test_transitions_and_refusals_are_notified() -> None:
    notifier = RecordingNotifier()
    machine = fresh_machine(notifier=notifier)
    refused = await machine.request_transition(ProgressionStage.PAPER)
    assert refused.status == "REFUSED"
    assert any("PROGRESSION" in text for text in notifier.events)
    request = await machine.request_transition(ProgressionStage.PAPER, evidence_updates=BACKTEST_EVIDENCE)
    assert any("HUMAN CONFIRMATION REQUIRED" in text for text in notifier.events)
    await machine.confirm_transition(request.token)
    assert any("stage entered" in text for text in notifier.events)


@pytest.mark.asyncio
async def test_kill_switch_notification_never_raises() -> None:
    machine = fresh_machine()
    machine.notifier = RecordingNotifier(boom=True)
    assert await machine.notify_kill_switch(enabled=True, detail="test") is False


# --------------------------------------------------------------------------- API
def api_client(**settings_overrides):
    # A file-backed SQLite DB: TestClient runs the app in a worker thread, and an
    # in-memory DB would be a different database on that thread.
    settings = Settings(trading_mode="paper", live_enabled=False, **settings_overrides)
    db_path = f"{tempfile.mkdtemp()}/progression_api.db"
    session_factory = create_session(make_engine(f"sqlite:///{db_path}"))
    app = create_app(settings=settings, session_factory=session_factory)
    return TestClient(app), session_factory


def test_api_progression_defaults_to_backtest_live_off() -> None:
    client, _ = api_client()
    with client:
        body = client.get("/progression").json()
    assert body["stage"] == "BACKTEST"
    assert body["live_enabled"] is False
    assert body["live_execution_implemented"] is False
    assert body["required_evidence"]


def test_api_transition_is_refused_without_evidence() -> None:
    client, _ = api_client()
    with client:
        body = client.post("/progression/transition", json={"target_stage": "PAPER"}).json()
    assert body["status"] == "REFUSED"
    assert body["ok"] is False
    assert body["failed"]


def test_api_live_enable_is_refused_at_backtest_stage() -> None:
    client, _ = api_client()
    with client:
        body = client.post("/progression/live", json={"enabled": True}).json()
    assert body["status"] == "REFUSED"
    assert body["live_enabled"] is False
    assert any("not live-capable" in reason for reason in body["failed"])


def test_api_transition_then_confirm_applies_stage() -> None:
    client, _ = api_client()
    with client:
        request = client.post(
            "/progression/transition",
            json={"target_stage": "PAPER", "evidence": BACKTEST_EVIDENCE},
        ).json()
        assert request["status"] == "AWAITING_CONFIRMATION"
        applied = client.post("/progression/confirm", json={"token": request["token"]}).json()
        assert applied["status"] == "APPLIED"
        assert client.get("/progression").json()["stage"] == "PAPER"


def test_api_kill_switch_records_event_without_crashing() -> None:
    client, _ = api_client()
    with client:
        assert client.post("/kill", json={"enabled": True}).json()["kill_switch"] is True
        assert client.post("/kill", json={"enabled": False}).json()["trading_halted"] is False


def test_api_trade_authorize_returns_token_for_user_size() -> None:
    client, _ = api_client()
    with client:
        body = client.post(
            "/trade/authorize", json={"side": "BUY", "size": 150.0, "client_order_id": "coid-9"}
        ).json()
    assert body["status"] == "AWAITING_CONFIRMATION"
    assert body["token"]
    assert body["size"] == 150.0


def test_api_trade_refuses_an_invalid_confirmation_token() -> None:
    client, _ = api_client()
    with client:
        body = client.post(
            "/trade",
            json={
                "side": "BUY",
                "amount": 150.0,
                "stop_loss": 60_000.0,
                "client_order_id": "coid-9",
                "human_confirmation_token": "not-a-real-token",
            },
        ).json()
    assert body["executed"] is False
    assert "confirmation" in body["reason"].lower()


def test_api_trade_confirmation_can_be_required_by_settings() -> None:
    client, _ = api_client(require_trade_confirmation=True)
    with client:
        body = client.post(
            "/trade",
            json={"side": "BUY", "amount": 150.0, "stop_loss": 60_000.0, "client_order_id": "coid-9"},
        ).json()
    assert body["executed"] is False
    assert "human confirmation required" in body["reason"]
