from __future__ import annotations

import asyncio
import csv
import io
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal
from urllib.parse import quote

import httpx
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .arbitrage import calculate_arbitrage, evaluate_bundle
from .auth import Principal, get_principal
from .backtest import BacktestConfig, HistoricalSnapshot, run_backtest
from .config import settings
from .database import connection_factory
from .demo import demo_opportunity
from .domain import (
    AlertCreate,
    AlertUpdate,
    ArbitrageResult,
    CancellationCreate,
    FillCreate,
    MatchStatus,
    PaperPositionCreate,
    PositionCreate,
    PositionStatus,
    PriceKind,
    ScenarioSimulationRequest,
    SettlementCreate,
    SimulateRequest,
    WatchlistCreate,
)
from .ledger import PaperLedger
from .local_persistence import (
    LOCAL_SCHEMA_VERSION,
    MAX_BACKUP_BYTES,
    LocalPaperLedger,
    LocalPersistenceError,
    LocalSavedOpportunityStore,
    LocalStateDatabase,
    LocalUserStore,
)
from .paper_persistence import PostgresPaperRepository
from .private_persistence import PostgresPrivateRepository
from .providers import (
    DemoAdapter,
    EventQuery,
    KalshiAdapter,
    MarketQuery,
    PolymarketInternationalAdapter,
    PolymarketUSAdapter,
    ProviderCode,
    ProviderState,
    TheOddsAPIAdapter,
)
from .provider_connections_api import router as provider_connections_router
from .research_persistence import OpportunityFilters, PostgresResearchRepository
from .store import UserStore


def wire(value: Any) -> Any:
    """Preserve Decimal precision in every JSON response."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return wire(value.model_dump(mode="python"))
    if is_dataclass(value) and not isinstance(value, type):
        return wire(asdict(value))
    if isinstance(value, dict):
        return {str(key): wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [wire(item) for item in value]
    return value


def json(value: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(wire(value), status_code=status_code)


def _local_state_database() -> LocalStateDatabase | None:
    if bool(settings.local_data_path) != bool(settings.local_data_root):
        raise LocalPersistenceError(
            "desktop local persistence requires both data-path environment settings"
        )
    if settings.local_data_path and settings.local_data_root:
        return LocalStateDatabase(
            settings.local_data_path,
            allowed_root=settings.local_data_root,
        )
    return None


local_state_database = _local_state_database()
ledger = LocalPaperLedger(local_state_database) if local_state_database else PaperLedger()
user_store = LocalUserStore(local_state_database) if local_state_database else UserStore()
saved_opportunity_store = LocalSavedOpportunityStore(local_state_database)
research_repository = (
    PostgresResearchRepository(
        lambda: connection_factory(application_name="edgeos-api-research")
    )
    if settings.database_url
    else None
)
paper_repository = (
    PostgresPaperRepository(
        lambda: connection_factory(application_name="edgeos-api-paper")
    )
    if settings.database_url
    else None
)
private_repository = (
    PostgresPrivateRepository(
        lambda: connection_factory(application_name="edgeos-api-private")
    )
    if settings.database_url
    else None
)
RECENT_AUTH_WINDOW_SECONDS = 15 * 60


def _require_persistent_private_store(principal: Principal) -> None:
    if not principal.is_demo:
        raise HTTPException(
            503,
            detail={
                "code": "persistent_store_not_configured",
                "message": "Live private-data persistence must be connected to the RLS-protected Supabase repository.",
            },
        )


def _private_store(principal: Principal) -> Any:
    """Select demo memory or live durable private storage without fallback."""
    if principal.is_demo:
        return user_store
    if private_repository is None:
        raise HTTPException(
            503,
            detail={
                "code": "persistent_store_not_configured",
                "message": "Live private-data persistence requires DATABASE_URL.",
            },
        )
    return private_repository


def _require_recent_auth(principal: Principal, *, now: datetime | None = None) -> None:
    """Require a recently issued access token for destructive account deletion."""
    current_timestamp = int((now or datetime.now(UTC)).timestamp())
    if (
        principal.authenticated_at is None
        or principal.authenticated_at > current_timestamp + 60
        or current_timestamp - principal.authenticated_at > RECENT_AUTH_WINDOW_SECONDS
    ):
        raise HTTPException(
            403,
            detail={
                "code": "recent_authentication_required",
                "message": "Sign out, sign in again, and retry within 15 minutes.",
            },
        )


def _require_match_reviewer(principal: Principal) -> None:
    """Global settlement decisions require an explicit server-side allowlist."""
    if principal.is_demo or principal.user_id not in settings.reviewer_user_ids:
        raise HTTPException(
            403,
            detail={
                "code": "reviewer_permission_required",
                "message": "This account is not authorized to change global settlement decisions.",
            },
        )


def _idempotency_key(body_value: str | None, header_value: str | None) -> str:
    """Require one retry key and reject ambiguous browser/server contracts."""
    if body_value and header_value and body_value != header_value:
        raise HTTPException(409, detail={"code": "idempotency_key_mismatch"})
    value = body_value or header_value
    if not value:
        raise HTTPException(422, detail={"code": "idempotency_key_required"})
    if len(value) < 8 or len(value) > 200:
        raise HTTPException(422, detail={"code": "invalid_idempotency_key"})
    return value


def _safe_user_record(record: dict[str, object]) -> dict[str, object]:
    """Never return internal ownership identifiers from private DTOs."""
    return {key: value for key, value in record.items() if key != "user_id"}


def _paper_position_dto(user_id: str, position: Any, *, is_demo: bool) -> dict[str, object]:
    simulation = position.request.simulation
    fills_by_leg: dict[str, list[Any]] = {}
    for fill in position.fills:
        fills_by_leg.setdefault(fill.leg_id, []).append(fill)

    committed = Decimal("0")
    legs: list[dict[str, object]] = []
    all_filled = bool(simulation.legs)
    any_filled = False
    for leg in simulation.legs:
        leg_fills = fills_by_leg.get(leg.offer_id, [])
        filled_quantity = sum((fill.quantity for fill in leg_fills), Decimal("0"))
        any_filled = any_filled or filled_quantity > 0
        all_filled = all_filled and filled_quantity == leg.filled_quantity
        notional = sum(
            (
                fill.quantity
                if leg.price_kind is PriceKind.DECIMAL_ODDS
                else fill.quantity * fill.price
            ) + fill.fee
            for fill in leg_fills
        )
        committed += notional
        average = (
            sum((fill.quantity * fill.price for fill in leg_fills), Decimal("0")) / filled_quantity
            if filled_quantity else None
        )
        legs.append({
            "id": leg.offer_id,
            "provider": leg.provider,
            "outcome": leg.outcome_id,
            "planned_quantity": leg.filled_quantity,
            "filled_quantity": filled_quantity,
            "average_fill_price": average,
            "status": "filled" if filled_quantity == leg.filled_quantity else ("partially_filled" if filled_quantity else "pending"),
        })

    if position.status is PositionStatus.SETTLED:
        status_value = "settled"
    elif position.status is PositionStatus.CANCELED:
        status_value = "cancelled"
    elif all_filled:
        status_value = "filled"
    elif any_filled:
        status_value = "partially_filled"
    else:
        status_value = "open"

    outcome_results: list[Decimal] = []
    for result in simulation.outcomes:
        payout = Decimal("0")
        for leg in simulation.legs:
            if leg.outcome_id != result.outcome_id:
                continue
            for fill in fills_by_leg.get(leg.offer_id, []):
                payout += fill.quantity * fill.price if leg.price_kind is PriceKind.DECIMAL_ODDS else fill.quantity
        outcome_results.append(payout - committed)
    worst_case_exposure = max(Decimal("0"), -min(outcome_results, default=Decimal("0")))
    position_entries = tuple(entry for entry in ledger.entries(user_id) if entry.position_id == position.position_id)
    realized = sum((entry.amount for entry in position_entries), Decimal("0")) if status_value == "settled" else Decimal("0")
    title = "Paper research bundle"
    if position.request.opportunity_id == "demo-opportunity-001":
        title = "[DEMO] Harbor Hawks vs Metro Comets"
    return {
        "id": position.position_id,
        "opportunity_id": position.request.opportunity_id,
        "title": title,
        "strategy": position.request.strategy,
        "sport": position.request.sport,
        "status": status_value,
        "planned_allocation": simulation.requested_allocation,
        "committed_capital": committed,
        "realized_pnl": realized,
        "unrealized_pnl": Decimal("0"),
        "worst_case_exposure": worst_case_exposure,
        "notes": position.request.notes,
        "created_at": position.created_at,
        "opened_at": position.created_at,
        "settled_at": position.settled_at,
        "currency": simulation.currency,
        "is_demo": is_demo,
        "calculation_version": simulation.calculation_version,
        "legs": legs,
    }


async def delete_supabase_account(
    *,
    supabase_url: str,
    secret_key: str,
    user_id: str,
    access_token: str,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Revoke the caller's sessions, then delete exactly that authenticated user."""
    owned_client = client is None
    active_client = client or httpx.AsyncClient(timeout=10, follow_redirects=False)
    base = supabase_url.rstrip("/")
    try:
        revoke = await active_client.post(
            f"{base}/auth/v1/logout",
            params={"scope": "global"},
            headers={"apikey": secret_key, "Authorization": f"Bearer {access_token}"},
        )
        if revoke.status_code not in {200, 204}:
            raise RuntimeError(f"session revocation failed with HTTP {revoke.status_code}")
        deleted = await active_client.delete(
            f"{base}/auth/v1/admin/users/{quote(user_id, safe='')}",
            headers={"apikey": secret_key, "Authorization": f"Bearer {secret_key}"},
        )
        if deleted.status_code not in {200, 204}:
            raise RuntimeError(f"account deletion failed with HTTP {deleted.status_code}")
    finally:
        if owned_client:
            await active_client.aclose()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(
    title="EDGEOS API",
    version="0.1.0",
    description="Read-only market research and simulated paper-trading API",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization", "Content-Type", "X-Idempotency-Key", "X-EDGEOS-Client-Mode",
        "X-EDGEOS-Confirm-Restore",
    ],
    expose_headers=["X-EDGEOS-Mode", "X-EDGEOS-Mode-Contract"],
)
app.include_router(provider_connections_router)


@app.middleware("http")
async def publish_runtime_contract(request: Request, call_next: Any) -> Response:
    """Publish the API data mode on every response for fail-closed clients."""
    mode = "demo" if settings.demo_mode else "live"
    client_mode = request.headers.get("X-EDGEOS-Client-Mode")
    if client_mode is not None and client_mode != mode:
        response: Response = json({
            "detail": {
                "code": "mode_mismatch",
                "message": "The client and API data modes disagree; results are blocked.",
                "expected": mode,
                "actual": client_mode,
            }
        }, status_code=409)
    else:
        response = await call_next(request)
    response.headers["X-EDGEOS-Mode"] = mode
    response.headers["X-EDGEOS-Mode-Contract"] = "1"
    return response


def _adapters() -> dict[ProviderCode, Any]:
    return {
        ProviderCode.KALSHI: KalshiAdapter(),
        ProviderCode.POLYMARKET_INTERNATIONAL: PolymarketInternationalAdapter(),
        ProviderCode.POLYMARKET_US: PolymarketUSAdapter(),
        ProviderCode.THE_ODDS_API: TheOddsAPIAdapter(api_key=settings.odds_api_key),
        ProviderCode.DEMO: DemoAdapter(),
    }


async def _close_all(adapters: dict[ProviderCode, Any]) -> None:
    await asyncio.gather(*(adapter.aclose() for adapter in adapters.values()), return_exceptions=True)


@app.get("/api/v1/health", tags=["system"])
async def health() -> JSONResponse:
    return json({
        "status": "ok",
        "service": "edgeos-api",
        "version": app.version,
        "mode": "demo" if settings.demo_mode else "live",
        "mode_contract_version": 1,
        "is_demo": settings.demo_mode,
        "auth": "demo_local_identity" if settings.demo_mode else ("supabase_jwks" if settings.auth_configured else "not_configured"),
        "desktop_local_storage": {
            "backend": "sqlite" if local_state_database is not None else "memory",
            "durable": local_state_database is not None,
            "status": "ready",
            "schema_version": LOCAL_SCHEMA_VERSION if local_state_database is not None else None,
        },
        "paper_trading_only": True,
        "real_money_execution": False,
        "timestamp": datetime.now(UTC),
    })


@app.get("/api/v1/dashboard", tags=["dashboard"])
async def dashboard(principal: Principal = Depends(get_principal)) -> JSONResponse:
    if principal.is_demo:
        _require_persistent_private_store(principal)
        summary = ledger.summary(principal.user_id)
        opportunity_count = 1
        verified_count = 1
        data_origin = "demo"
        is_demo = True
        currency = "USD"
    else:
        if paper_repository is None or research_repository is None:
            raise HTTPException(
                503,
                detail={
                    "code": "persistent_store_not_configured",
                    "message": "Live dashboard data requires DATABASE_URL and applied migrations.",
                },
            )
        try:
            portfolio_data = paper_repository.portfolio(principal.user_id)
            durable_summary = portfolio_data.get("summary")
            if not isinstance(durable_summary, dict):
                raise RuntimeError("paper_portfolio_summary_unavailable")
            summary = durable_summary
            currency_value = portfolio_data.get("currency")
            if not isinstance(currency_value, str):
                raise RuntimeError("paper_portfolio_currency_unavailable")
            currency = currency_value
            _, opportunity_count = research_repository.list_opportunities(
                OpportunityFilters(verified_only=False, page=1, limit=1)
            )
            _, verified_count = research_repository.list_opportunities(
                OpportunityFilters(verified_only=True, page=1, limit=1)
            )
        except (ConnectionError, RuntimeError) as exc:
            raise HTTPException(503, detail={"code": "dashboard_store_unavailable"}) from exc
        data_origin = "live_poll"
        is_demo = False
    return json({
        "data_origin": data_origin,
        "is_demo": is_demo,
        "currency": currency,
        "simulated_portfolio_value": summary["portfolio_value"],
        "realized_simulated_pnl": summary["realized_pnl"],
        "unrealized_simulated_pnl": summary["unrealized_pnl"],
        "net_roi": summary["roi"] if is_demo else summary["net_roi"],
        "active_simulated_positions": summary["active_positions"],
        "opportunities_detected": opportunity_count,
        "verified_opportunities": verified_count,
        "historical_capture_rate": None,
        "empty_performance_history": not bool(summary["settled_positions"]),
    })


_SORTS = {
    "theoretical_roi": "gross_roi",
    "theoretical_profit": "potential_net_profit",
    "execution_risk": "quote_age_seconds",
    "match_confidence": "settlement_status",
    "liquidity": "available_liquidity",
    "quote_time": "observed_at",
    "research_score": "research_score",
    "net_roi": "net_roi",
}


@app.get("/api/v1/opportunities", tags=["scanner"])
async def opportunities(
    search: str | None = None,
    sport: str | None = None,
    league: str | None = None,
    provider: str | None = None,
    market_type: str | None = None,
    minimum_net_roi: Decimal | None = None,
    minimum_theoretical_profit: Decimal | None = None,
    minimum_liquidity: Decimal | None = None,
    maximum_quote_age: int | None = None,
    verified_only: bool = True,
    phase: Literal["prematch", "live", "all"] = "all",
    sort: str = "research_score",
    descending: bool = True,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> JSONResponse:
    if sort not in _SORTS:
        raise HTTPException(422, f"sort must be one of {', '.join(_SORTS)}")
    if not settings.demo_mode:
        if research_repository is None:
            raise HTTPException(
                503,
                detail={
                    "code": "persistent_store_not_configured",
                    "message": "Live opportunity research requires DATABASE_URL and the applied Supabase migrations.",
                },
            )
        try:
            durable_rows, total = research_repository.list_opportunities(
                OpportunityFilters(
                    search=search, sport=sport, league=league, provider=provider,
                    market_type=market_type, minimum_net_roi=minimum_net_roi,
                    minimum_theoretical_profit=minimum_theoretical_profit,
                    minimum_liquidity=minimum_liquidity,
                    maximum_quote_age=maximum_quote_age,
                    verified_only=verified_only, phase=phase, sort=sort,
                    descending=descending, page=page, limit=limit,
                )
            )
        except (ConnectionError, RuntimeError) as exc:
            raise HTTPException(
                503, detail={"code": "research_store_unavailable"}
            ) from exc
        return json({
            "items": durable_rows, "page": page, "limit": limit,
            "total": total, "has_more": page * limit < total,
        })

    rows: list[dict[str, Any]] = [demo_opportunity()]

    def keep(row: dict[str, Any]) -> bool:
        if search and search.lower() not in f"{row['event']} {row['market']}".lower():
            return False
        if sport and str(row["sport"]).casefold() != sport.casefold():
            return False
        if league and str(row["league"]).casefold() != league.casefold():
            return False
        if provider and provider.casefold() not in {
            str(row["platform_a"]).casefold(), str(row["platform_b"]).casefold()
        }:
            return False
        if market_type and str(row["market_type"]).casefold() != market_type.casefold():
            return False
        if verified_only and row["settlement_status"] != "verified":
            return False
        if minimum_net_roi is not None and row["net_roi"] < minimum_net_roi:
            return False
        if minimum_theoretical_profit is not None and row["potential_net_profit"] < minimum_theoretical_profit:
            return False
        if minimum_liquidity is not None and row["available_liquidity"] < minimum_liquidity:
            return False
        if maximum_quote_age is not None and row["quote_age_seconds"] > maximum_quote_age:
            return False
        if phase != "all" and row.get("phase") != phase:
            return False
        return True

    rows = [row for row in rows if keep(row)]
    rows.sort(key=lambda item: item[_SORTS[sort]], reverse=descending)
    start = (page - 1) * limit
    # Keep the list compact; heavy drawer-only fields live in the detail route.
    compact = [{key: value for key, value in row.items() if key not in {"simulation", "settlement_audit", "failure_reasons", "price_history"}} for row in rows[start:start + limit]]
    return json({"items": compact, "page": page, "limit": limit, "total": len(rows), "has_more": start + limit < len(rows)})


@app.get("/api/v1/opportunities/{opportunity_id}", tags=["scanner"])
async def opportunity_detail(opportunity_id: str) -> JSONResponse:
    if settings.demo_mode and opportunity_id == "demo-opportunity-001":
        return json(demo_opportunity())
    if not settings.demo_mode:
        if research_repository is None:
            raise HTTPException(503, detail={"code": "persistent_store_not_configured"})
        try:
            opportunity = research_repository.get_opportunity(opportunity_id)
        except (ConnectionError, RuntimeError) as exc:
            raise HTTPException(503, detail={"code": "research_store_unavailable"}) from exc
        if opportunity is not None:
            return json(opportunity)
    raise HTTPException(404, "Opportunity not found")


@app.post("/api/v1/simulate", tags=["simulator"])
async def simulate(request: SimulateRequest) -> JSONResponse:
    try:
        result = calculate_arbitrage(
            request.offers, request.total_capital,
            # A caller-provided label is not proof. Only a server-side persisted
            # match review may eventually supply VERIFIED here.
            settlement_status=MatchStatus.REVIEW_REQUIRED,
            now=request.now,
            maximum_quote_age_seconds=request.maximum_quote_age_seconds,
            maximum_quote_timestamp_skew_seconds=request.maximum_quote_timestamp_skew_seconds,
            provider_capital_limits=request.provider_capital_limits,
            expected_lockup_seconds=request.expected_lockup_seconds,
            allow_fee_assumptions=request.allow_fee_assumptions,
            require_observed_depth=request.require_observed_depth,
            expected_outcome_ids=request.expected_outcome_ids,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return json(result)


@app.post("/api/v1/simulate/scenarios", tags=["simulator"])
async def simulate_scenarios(request: ScenarioSimulationRequest) -> JSONResponse:
    """Enumerate an explicit payout matrix for research; never mark it executable."""
    try:
        result = evaluate_bundle(request.legs, request.outcome_ids)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return json({
        "evaluation": result,
        "settlement_status": MatchStatus.REVIEW_REQUIRED,
        "execution_eligible": False,
        "research_only": True,
        "warning": "Scenario completeness and settlement equivalence have not been server-verified.",
    })


@app.get("/api/v1/providers/health", tags=["providers"])
async def provider_health(probe: bool = False) -> JSONResponse:
    if probe and not settings.demo_mode:
        raise HTTPException(
            403,
            detail={
                "code": "direct_provider_probe_disabled",
                "message": (
                    "Shared live health routes never issue user-triggered provider requests. "
                    "Use the authenticated API Connections test or inspect worker telemetry."
                ),
            },
        )
    if not settings.demo_mode:
        if research_repository is None:
            raise HTTPException(
                503,
                detail={
                    "code": "persistent_store_not_configured",
                    "message": "Provider health requires the centrally ingested research database.",
                },
            )
        try:
            persisted_providers = await asyncio.to_thread(
                research_repository.list_provider_health
            )
        except (ConnectionError, RuntimeError) as exc:
            raise HTTPException(
                503,
                detail={
                    "code": "research_store_unavailable",
                    "message": "Persisted provider health is temporarily unavailable.",
                },
            ) from exc
        return json(
            {
                "providers": persisted_providers,
                "items": [],
                "probed": False,
                "source": "persisted_worker_telemetry",
                "checked_at": datetime.now(UTC),
            }
        )
    adapters = _adapters()
    try:
        rows = []
        for code, adapter in adapters.items():
            if settings.demo_mode and code is not ProviderCode.DEMO:
                health_record = adapter.health_snapshot(
                    state=ProviderState.UNKNOWN,
                    message=(
                        "External provider probing and browsing are disabled in demo mode. "
                        "Use a separately configured live deployment to request provider data."
                    ),
                )
            elif code is ProviderCode.DEMO or probe:
                try:
                    health_record = await asyncio.wait_for(adapter.check_health(), timeout=12)
                except TimeoutError:
                    health_record = adapter.health_snapshot(state=ProviderState.DISCONNECTED, message="Health probe timed out")
            else:
                health_record = adapter.health_snapshot(
                    state=ProviderState.UNKNOWN,
                    message=(
                        "Shared health reads do not contact providers. Use the authenticated "
                        "API Connections test or inspect centrally ingested worker telemetry."
                    ),
                )
            rows.append({"descriptor": adapter.descriptor, "health": health_record})
        providers = []
        for row in rows:
            descriptor = row["descriptor"]
            record = row["health"]
            providers.append({
                "id": descriptor.code.value,
                "name": descriptor.display_name,
                "status": record.state.value,
                "feed_type": ",".join(sorted(feed.value for feed in descriptor.feed_types)),
                "last_successful_update": record.last_success_at,
                "last_error": record.last_error,
                "known_refresh_interval_seconds": record.known_refresh_interval_seconds,
                "api_quota_usage": record.rate_limit,
                "market_coverage": descriptor.product_scope,
                "active_instruments": None,
                "rate_limit_status": "limited" if record.rate_limit_events else "ok",
                "message": record.message,
            })
        return json({"providers": providers, "items": rows, "probed": probe, "checked_at": datetime.now(UTC)})
    finally:
        await _close_all(adapters)


def _provider_code(value: str) -> ProviderCode:
    try:
        return ProviderCode(value)
    except ValueError as exc:
        raise HTTPException(404, "Unknown provider") from exc


def _browsable_provider_code(value: str) -> ProviderCode:
    code = _provider_code(value)
    if settings.demo_mode and code is not ProviderCode.DEMO:
        raise HTTPException(
            403,
            detail={
                "code": "external_provider_disabled_in_demo_mode",
                "message": (
                    "External provider browsing is disabled in demo mode. "
                    "Only explicitly labeled demonstration records are available."
                ),
            },
        )
    if not settings.demo_mode:
        raise HTTPException(
            503,
            detail={
                "code": "direct_provider_browser_disabled",
                "message": (
                    "Live browser reads must come from centrally ingested, persisted snapshots. "
                    "Direct user-triggered provider polling is disabled to protect quotas and provenance."
                ),
            },
        )
    return code


@app.get("/api/v1/providers/{provider_code}/events", tags=["providers"])
async def provider_events(
    provider_code: str,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    sport: str | None = None,
) -> JSONResponse:
    # Resolve and reject before adapters are constructed so a demo process can
    # never issue an outbound provider request through this route.
    code = _browsable_provider_code(provider_code)
    adapters = _adapters()
    try:
        filters = {"sport": sport} if sport else {}
        page = await adapters[code].list_events(EventQuery(limit=limit, cursor=cursor, filters=filters))
        return json({**asdict(page), "is_demo": code is ProviderCode.DEMO})
    finally:
        await _close_all(adapters)


@app.get("/api/v1/providers/{provider_code}/markets", tags=["providers"])
async def provider_markets(
    provider_code: str,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    sport: str | None = None,
) -> JSONResponse:
    # Resolve and reject before adapters are constructed so a demo process can
    # never issue an outbound provider request through this route.
    code = _browsable_provider_code(provider_code)
    adapters = _adapters()
    try:
        filters = {"sport": sport} if sport else {}
        page = await adapters[code].list_markets(MarketQuery(limit=limit, cursor=cursor, filters=filters))
        return json({**asdict(page), "is_demo": code is ProviderCode.DEMO})
    finally:
        await _close_all(adapters)


class MatchReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["verified", "review_required", "rejected"]
    rationale: str = Field(min_length=8, max_length=4000)
    checks: dict[str, object]
    source_evidence: dict[str, object]
    expected_version: int = Field(ge=1)


class SavedOpportunityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    favorited: bool = False


@app.get("/api/v1/match-reviews", tags=["matching"])
async def match_review_queue(
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(get_principal),
) -> JSONResponse:
    # The demo dataset contains no operator decisions; never fabricate a queue.
    if principal.is_demo:
        return json({"items": [], "mode": "demo", "message": "No persisted demo review records."})
    if research_repository is None:
        raise HTTPException(503, detail={"code": "persistent_store_not_configured"})
    try:
        items = research_repository.list_match_reviews(limit=limit)
    except (ConnectionError, RuntimeError) as exc:
        raise HTTPException(503, detail={"code": "research_store_unavailable"}) from exc
    return json({"items": items, "mode": "live"})


@app.patch("/api/v1/match-reviews/{match_id}", tags=["matching"])
async def decide_match_review(
    match_id: str,
    request: MatchReviewDecisionRequest,
    principal: Principal = Depends(get_principal),
) -> JSONResponse:
    _require_match_reviewer(principal)
    if research_repository is None:
        raise HTTPException(503, detail={"code": "persistent_store_not_configured"})
    try:
        return json(research_repository.decide_match(
            match_id=match_id, reviewer_id=principal.user_id,
            decision=request.decision, rationale=request.rationale,
            checks=request.checks, source_evidence=request.source_evidence,
            expected_version=request.expected_version,
        ))
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "match_not_found"}) from exc
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "incomplete_review_evidence", "message": str(exc)}) from exc
    except (ConnectionError, RuntimeError) as exc:
        if str(exc) == "review_version_conflict":
            raise HTTPException(409, detail={"code": "review_version_conflict"}) from exc
        raise HTTPException(503, detail={"code": "research_store_unavailable"}) from exc


@app.get("/api/v1/saved-opportunities", tags=["scanner"])
async def saved_opportunities(principal: Principal = Depends(get_principal)) -> JSONResponse:
    if principal.is_demo:
        try:
            return json({
                "mode": "demo",
                "items": saved_opportunity_store.list(principal.user_id),
            })
        except LocalPersistenceError as exc:
            raise HTTPException(503, detail={"code": "local_store_unavailable"}) from exc
    if research_repository is None:
        raise HTTPException(503, detail={"code": "persistent_store_not_configured"})
    try:
        items = research_repository.list_saved_opportunities(principal.user_id)
    except (ConnectionError, RuntimeError) as exc:
        raise HTTPException(503, detail={"code": "research_store_unavailable"}) from exc
    return json({"mode": "live", "items": items})


@app.put("/api/v1/saved-opportunities/{opportunity_id}", tags=["scanner"])
async def save_opportunity(
    opportunity_id: str,
    request: SavedOpportunityRequest,
    principal: Principal = Depends(get_principal),
) -> JSONResponse:
    if principal.is_demo:
        if opportunity_id != "demo-opportunity-001":
            raise HTTPException(404, detail={"code": "opportunity_not_found"})
        try:
            return json(saved_opportunity_store.save(
                principal.user_id, opportunity_id, favorited=request.favorited,
            ))
        except LocalPersistenceError as exc:
            raise HTTPException(503, detail={"code": "local_store_unavailable"}) from exc
    if research_repository is None:
        raise HTTPException(503, detail={"code": "persistent_store_not_configured"})
    try:
        return json(research_repository.save_opportunity(
            principal.user_id, opportunity_id, favorited=request.favorited,
        ))
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "opportunity_not_found"}) from exc
    except (ConnectionError, RuntimeError) as exc:
        raise HTTPException(503, detail={"code": "research_store_unavailable"}) from exc


@app.delete("/api/v1/saved-opportunities/{opportunity_id}", tags=["scanner"], status_code=204)
async def unsave_opportunity(
    opportunity_id: str,
    principal: Principal = Depends(get_principal),
) -> Response:
    if principal.is_demo:
        try:
            was_removed = saved_opportunity_store.delete(principal.user_id, opportunity_id)
        except LocalPersistenceError as exc:
            raise HTTPException(503, detail={"code": "local_store_unavailable"}) from exc
    else:
        if research_repository is None:
            raise HTTPException(503, detail={"code": "persistent_store_not_configured"})
        try:
            was_removed = research_repository.delete_saved_opportunity(
                principal.user_id, opportunity_id
            )
        except (ConnectionError, RuntimeError) as exc:
            raise HTTPException(503, detail={"code": "research_store_unavailable"}) from exc
    if not was_removed:
        raise HTTPException(404, detail={"code": "saved_opportunity_not_found"})
    return Response(status_code=204)


@app.get("/api/v1/portfolio", tags=["portfolio"])
async def portfolio(principal: Principal = Depends(get_principal)) -> JSONResponse:
    if not principal.is_demo:
        if paper_repository is None:
            raise HTTPException(503, detail={"code": "persistent_store_not_configured"})
        try:
            return json(paper_repository.portfolio(principal.user_id))
        except (ConnectionError, RuntimeError) as exc:
            raise HTTPException(503, detail={"code": "paper_store_unavailable"}) from exc
    _require_persistent_private_store(principal)
    summary = ledger.summary(principal.user_id)
    return json({
        "mode": "demo" if principal.is_demo else "live",
        "currency": "USD",
        "summary": {
            "portfolio_value": summary["portfolio_value"],
            "cash_balance": summary["cash_balance"],
            "reserved_capital": summary["reserved_capital"],
            "realized_pnl": summary["realized_pnl"],
            "unrealized_pnl": summary["unrealized_pnl"],
            "net_roi": summary["roi"],
            "active_positions": summary["active_positions"],
            "settled_positions": summary["settled_positions"],
        },
        "positions": [
            _paper_position_dto(principal.user_id, position, is_demo=principal.is_demo)
            for position in ledger.positions(principal.user_id)
        ],
        "simulation_only": True,
        "is_demo": principal.is_demo,
    })


@app.post("/api/v1/portfolio/positions", tags=["portfolio"], status_code=201)
async def create_position(
    request: PaperPositionCreate,
    principal: Principal = Depends(get_principal),
    idempotency_header: str | None = Header(default=None, alias="X-Idempotency-Key"),
) -> JSONResponse:
    key = _idempotency_key(request.idempotency_key, idempotency_header)
    if not principal.is_demo:
        if paper_repository is None:
            raise HTTPException(503, detail={"code": "persistent_store_not_configured"})
        try:
            return json(
                paper_repository.create_position(
                    principal.user_id, request, idempotency_key=key
                ),
                status_code=201,
            )
        except KeyError as exc:
            raise HTTPException(404, detail={"code": "opportunity_not_found"}) from exc
        except ValueError as exc:
            raise HTTPException(409, detail={"code": str(exc)}) from exc
        except (ConnectionError, RuntimeError) as exc:
            raise HTTPException(503, detail={"code": "paper_store_unavailable"}) from exc
    _require_persistent_private_store(principal)
    if request.opportunity_id != "demo-opportunity-001":
        raise HTTPException(404, detail={"code": "opportunity_not_found"})
    opportunity = demo_opportunity(total_capital=request.total_capital)
    simulation = opportunity["simulation"]
    if not isinstance(simulation, ArbitrageResult):
        raise HTTPException(500, detail={"code": "invalid_calculation_contract"})
    internal = PositionCreate(
        opportunity_id=request.opportunity_id,
        strategy="two_way_arbitrage",
        sport=str(opportunity["sport"]),
        notes=request.notes,
        simulation=simulation,
    )
    try:
        position = ledger.create_position(principal.user_id, internal, idempotency_key=key)
    except LocalPersistenceError as exc:
        raise HTTPException(503, detail={"code": "local_store_unavailable"}) from exc
    except ValueError as exc:
        raise HTTPException(409, detail={"code": str(exc)}) from exc
    return json(_paper_position_dto(principal.user_id, position, is_demo=principal.is_demo), status_code=201)


@app.post("/api/v1/portfolio/positions/{position_id}/fills", tags=["portfolio"])
async def create_fill(
    position_id: str,
    request: FillCreate,
    principal: Principal = Depends(get_principal),
    idempotency_header: str | None = Header(default=None, alias="X-Idempotency-Key"),
) -> JSONResponse:
    key = _idempotency_key(request.idempotency_key, idempotency_header)
    if not principal.is_demo:
        if paper_repository is None:
            raise HTTPException(503, detail={"code": "persistent_store_not_configured"})
        try:
            return json(
                paper_repository.add_fill(
                    principal.user_id, position_id, request, idempotency_key=key
                )
            )
        except KeyError as exc:
            raise HTTPException(404, detail={"code": "position_not_found"}) from exc
        except ValueError as exc:
            raise HTTPException(409, detail={"code": str(exc)}) from exc
        except (ConnectionError, RuntimeError) as exc:
            raise HTTPException(503, detail={"code": "paper_store_unavailable"}) from exc
    _require_persistent_private_store(principal)
    try:
        position = ledger.add_fill(principal.user_id, position_id, request, idempotency_key=key)
        return json(_paper_position_dto(principal.user_id, position, is_demo=principal.is_demo))
    except LocalPersistenceError as exc:
        raise HTTPException(503, detail={"code": "local_store_unavailable"}) from exc
    except KeyError as exc:
        raise HTTPException(404, "Position not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/v1/portfolio/positions/{position_id}/settle", tags=["portfolio"])
async def settle_position(
    position_id: str,
    request: SettlementCreate,
    principal: Principal = Depends(get_principal),
    idempotency_header: str | None = Header(default=None, alias="X-Idempotency-Key"),
) -> JSONResponse:
    key = _idempotency_key(request.idempotency_key, idempotency_header)
    if not principal.is_demo:
        if paper_repository is None:
            raise HTTPException(503, detail={"code": "persistent_store_not_configured"})
        try:
            return json(
                paper_repository.settle(
                    principal.user_id, position_id, request, idempotency_key=key
                )
            )
        except KeyError as exc:
            raise HTTPException(404, detail={"code": "position_not_found"}) from exc
        except ValueError as exc:
            raise HTTPException(409, detail={"code": str(exc)}) from exc
        except (ConnectionError, RuntimeError) as exc:
            raise HTTPException(503, detail={"code": "paper_store_unavailable"}) from exc
    _require_persistent_private_store(principal)
    try:
        position = ledger.settle(principal.user_id, position_id, request, idempotency_key=key)
        return json(_paper_position_dto(principal.user_id, position, is_demo=principal.is_demo))
    except LocalPersistenceError as exc:
        raise HTTPException(503, detail={"code": "local_store_unavailable"}) from exc
    except KeyError as exc:
        raise HTTPException(404, "Position not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/v1/portfolio/positions/{position_id}/cancel", tags=["portfolio"])
async def cancel_position(
    position_id: str,
    request: CancellationCreate,
    principal: Principal = Depends(get_principal),
    idempotency_header: str | None = Header(default=None, alias="X-Idempotency-Key"),
) -> JSONResponse:
    key = _idempotency_key(request.idempotency_key, idempotency_header)
    if not principal.is_demo:
        if paper_repository is None:
            raise HTTPException(503, detail={"code": "persistent_store_not_configured"})
        try:
            return json(
                paper_repository.cancel(
                    principal.user_id, position_id, request, idempotency_key=key
                )
            )
        except KeyError as exc:
            raise HTTPException(404, detail={"code": "position_not_found"}) from exc
        except ValueError as exc:
            raise HTTPException(409, detail={"code": str(exc)}) from exc
        except (ConnectionError, RuntimeError) as exc:
            raise HTTPException(503, detail={"code": "paper_store_unavailable"}) from exc
    _require_persistent_private_store(principal)
    try:
        position = ledger.cancel(
            principal.user_id, position_id, reason=request.reason, idempotency_key=key,
        )
        return json(_paper_position_dto(principal.user_id, position, is_demo=principal.is_demo))
    except LocalPersistenceError as exc:
        raise HTTPException(503, detail={"code": "local_store_unavailable"}) from exc
    except KeyError as exc:
        raise HTTPException(404, "Position not found") from exc
    except ValueError as exc:
        raise HTTPException(409, detail={"code": str(exc)}) from exc


@app.get("/api/v1/portfolio/export.csv", tags=["portfolio"])
async def export_portfolio(principal: Principal = Depends(get_principal)) -> Response:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["entry_id", "position_id", "entry_type", "amount", "currency", "occurred_at", "data_origin"])
    if not principal.is_demo:
        if paper_repository is None:
            raise HTTPException(503, detail={"code": "persistent_store_not_configured"})
        try:
            for live_entry in paper_repository.ledger_entries(principal.user_id):
                writer.writerow([
                    live_entry["entry_id"], live_entry["position_id"], live_entry["entry_type"],
                    str(live_entry["amount"]), live_entry["currency"],
                    str(live_entry["occurred_at"]), "live",
                ])
        except (ConnectionError, RuntimeError) as exc:
            raise HTTPException(503, detail={"code": "paper_store_unavailable"}) from exc
    else:
        _require_persistent_private_store(principal)
        for demo_entry in ledger.entries(principal.user_id):
            writer.writerow([demo_entry.entry_id, demo_entry.position_id, demo_entry.entry_type, str(demo_entry.amount), "USD", demo_entry.occurred_at.isoformat(), "demo"])
    return Response(stream.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=edgeos-paper-ledger.csv"})


@app.get("/api/v1/analytics", tags=["analytics"])
async def analytics(
    from_: datetime = Query(alias="from"),
    to: datetime = Query(),
    principal: Principal = Depends(get_principal),
) -> JSONResponse:
    if from_.tzinfo is None or to.tzinfo is None:
        raise HTTPException(422, "Analytics bounds must include a timezone")
    from_ = from_.astimezone(UTC)
    to = to.astimezone(UTC)
    if from_ > to:
        raise HTTPException(422, "Analytics 'from' must not be after 'to'")
    if not principal.is_demo:
        if paper_repository is None:
            raise HTTPException(503, detail={"code": "persistent_store_not_configured"})
        try:
            return json(paper_repository.analytics(principal.user_id, from_, to))
        except (ConnectionError, RuntimeError) as exc:
            raise HTTPException(503, detail={"code": "paper_store_unavailable"}) from exc
    _require_persistent_private_store(principal)
    summary = ledger.summary(principal.user_id)
    all_entries = sorted(ledger.entries(principal.user_id), key=lambda item: (item.occurred_at, item.entry_id))
    entries = [entry for entry in all_entries if from_ <= entry.occurred_at <= to]
    prior = sum((entry.amount for entry in all_entries if entry.occurred_at < from_), Decimal("0"))
    running = prior
    series = [{"timestamp": from_, "portfolio_value": ledger.starting_balance + prior, "cumulative_pnl": prior}]
    peak = ledger.starting_balance + prior
    maximum_drawdown = Decimal("0")
    for entry in entries:
        running += entry.amount
        value = ledger.starting_balance + running
        peak = max(peak, value)
        maximum_drawdown = max(maximum_drawdown, peak - value)
        series.append({"timestamp": entry.occurred_at, "portfolio_value": value, "cumulative_pnl": running})

    positions = [
        position for position in ledger.positions(principal.user_id)
        if from_ <= position.created_at <= to
    ]
    settled = [
        position for position in ledger.positions(principal.user_id)
        if position.settled_at is not None and from_ <= position.settled_at <= to
    ]
    realized = sum(
        (
            sum(
                (entry.amount for entry in all_entries if entry.position_id == position.position_id),
                Decimal("0"),
            )
            for position in settled
        ),
        Decimal("0"),
    )
    fees = sum((fill.fee for position in positions for fill in position.fills), Decimal("0"))
    turnover = -sum((entry.amount for entry in entries if entry.entry_type == "fill"), Decimal("0"))

    def breakdown(attribute: str) -> list[dict[str, object]]:
        buckets: dict[str, tuple[Decimal, int]] = {}
        for position in settled:
            key = str(getattr(position.request, attribute) or "unknown")
            pnl = sum(
                (entry.amount for entry in all_entries if entry.position_id == position.position_id),
                Decimal("0"),
            )
            previous = buckets.get(key, (Decimal("0"), 0))
            buckets[key] = (previous[0] + pnl, previous[1] + 1)
        return [
            {"key": key, "label": key.replace("_", " ").title(), "net_pnl": value, "count": count}
            for key, (value, count) in sorted(buckets.items())
        ]

    provider_buckets: dict[str, tuple[Decimal, int]] = {}
    for position in settled:
        pnl = sum(
            (entry.amount for entry in all_entries if entry.position_id == position.position_id),
            Decimal("0"),
        )
        providers = sorted({leg.provider for leg in position.request.simulation.legs})
        for provider in providers:
            prior_value = provider_buckets.get(provider, (Decimal("0"), 0))
            provider_buckets[provider] = (prior_value[0] + pnl / len(providers), prior_value[1] + 1)
    provider_rows = [
        {"key": key, "label": key, "net_pnl": value, "count": count}
        for key, (value, count) in sorted(provider_buckets.items())
    ]

    quote_buckets = {"< 15s": 0, "15–30s": 0, "30–60s": 0, "1–5m": 0, "> 5m": 0, "unknown": 0}
    for position in positions:
        ages = [leg.quote_age_seconds for leg in position.request.simulation.legs if leg.quote_age_seconds is not None]
        if not ages:
            quote_buckets["unknown"] += 1
            continue
        age = max(ages)
        bucket = "< 15s" if age < 15 else "15–30s" if age <= 30 else "30–60s" if age <= 60 else "1–5m" if age <= 300 else "> 5m"
        quote_buckets[bucket] += 1
    return json({
        "mode": "demo" if principal.is_demo else "live",
        "currency": "USD",
        "simulation_only": True,
        "insufficient_data": not positions and not entries,
        "from": from_,
        "to": to,
        "summary": {
            "realized_pnl": realized,
            "unrealized_pnl": summary["unrealized_pnl"],
            "net_roi": realized / ledger.starting_balance if ledger.starting_balance else Decimal("0"),
            "fees": fees,
            "turnover": turnover,
            "reserved_capital": summary["reserved_capital"],
            "maximum_drawdown": maximum_drawdown if entries else None,
            "captured_opportunities": sum(bool(position.fills) for position in positions),
            "attempted_opportunities": len(positions),
        },
        "equity": series if entries else [],
        "strategy": breakdown("strategy"),
        "provider": provider_rows,
        "sport": breakdown("sport"),
        "market": [],
        "quote_age": [{"bucket": bucket, "count": count} for bucket, count in quote_buckets.items()],
        "methodology": {
            "open_position_equity": "recorded cash flow at cost; unrealized mark-to-market is zero until a licensed price source is recorded",
            "capture_rate_denominator": "paper positions created in the selected UTC interval",
            "provider_pnl": "settled bundle P&L divided equally among participating providers",
        },
    })


@app.get("/api/v1/watchlists", tags=["watchlists"])
async def get_watchlists(principal: Principal = Depends(get_principal)) -> JSONResponse:
    store = _private_store(principal)
    try:
        items = store.watchlists(principal.user_id)
    except (ConnectionError, RuntimeError) as exc:
        raise HTTPException(503, detail={"code": "private_store_unavailable"}) from exc
    return json({
        "mode": "demo" if principal.is_demo else "live",
        "items": [_safe_user_record(item) for item in items],
    })


@app.post("/api/v1/watchlists", tags=["watchlists"], status_code=201)
async def add_watchlist(
    request: WatchlistCreate,
    principal: Principal = Depends(get_principal),
    idempotency_header: str | None = Header(default=None, alias="X-Idempotency-Key"),
) -> JSONResponse:
    store = _private_store(principal)
    key = _idempotency_key(request.idempotency_key, idempotency_header)
    request = request.model_copy(update={"idempotency_key": key})
    try:
        item = store.add_watchlist(principal.user_id, request)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "watchlist_target_not_found"}) from exc
    except ValueError as exc:
        code = str(exc)
        status_code = 409 if code == "idempotency_key_conflict" else 422
        raise HTTPException(status_code, detail={"code": code}) from exc
    except (ConnectionError, RuntimeError) as exc:
        raise HTTPException(503, detail={"code": "private_store_unavailable"}) from exc
    return json(_safe_user_record(item), status_code=201)


@app.delete("/api/v1/watchlists/{item_id}", tags=["watchlists"], status_code=204)
async def delete_watchlist(item_id: str, principal: Principal = Depends(get_principal)) -> Response:
    store = _private_store(principal)
    try:
        store.delete_watchlist(principal.user_id, item_id)
    except KeyError as exc:
        raise HTTPException(404, "Watchlist item not found") from exc
    except (ConnectionError, RuntimeError) as exc:
        raise HTTPException(503, detail={"code": "private_store_unavailable"}) from exc
    return Response(status_code=204)


@app.get("/api/v1/alerts", tags=["alerts"])
async def get_alerts(principal: Principal = Depends(get_principal)) -> JSONResponse:
    store = _private_store(principal)
    try:
        items = store.alerts(principal.user_id)
        events = store.alert_events(principal.user_id)
    except (ConnectionError, RuntimeError) as exc:
        raise HTTPException(503, detail={"code": "private_store_unavailable"}) from exc
    return json({
        "mode": "demo" if principal.is_demo else "live",
        "items": [_safe_user_record(item) for item in items],
        "events": [_safe_user_record(item) for item in events],
    })


@app.post("/api/v1/alerts", tags=["alerts"], status_code=201)
async def add_alert(
    request: AlertCreate,
    principal: Principal = Depends(get_principal),
    idempotency_header: str | None = Header(default=None, alias="X-Idempotency-Key"),
) -> JSONResponse:
    store = _private_store(principal)
    key = _idempotency_key(request.idempotency_key, idempotency_header)
    request = request.model_copy(update={"idempotency_key": key})
    try:
        item = store.add_alert(principal.user_id, request)
    except ValueError as exc:
        code = str(exc)
        status_code = 409 if code in {"idempotency_key_conflict", "alert_name_conflict"} else 422
        raise HTTPException(status_code, detail={"code": code}) from exc
    except (ConnectionError, RuntimeError) as exc:
        raise HTTPException(503, detail={"code": "private_store_unavailable"}) from exc
    return json(_safe_user_record(item), status_code=201)


@app.patch("/api/v1/alerts/{alert_id}", tags=["alerts"])
async def update_alert(
    alert_id: str,
    request: AlertUpdate,
    principal: Principal = Depends(get_principal),
) -> JSONResponse:
    store = _private_store(principal)
    try:
        return json(_safe_user_record(store.update_alert(principal.user_id, alert_id, request)))
    except KeyError as exc:
        raise HTTPException(404, "Alert not found") from exc
    except ValueError as exc:
        code = str(exc)
        status_code = 409 if code == "alert_name_conflict" else 422
        raise HTTPException(status_code, detail={"code": code}) from exc
    except (ConnectionError, RuntimeError) as exc:
        raise HTTPException(503, detail={"code": "private_store_unavailable"}) from exc


@app.delete("/api/v1/alerts/{alert_id}", tags=["alerts"], status_code=204)
async def delete_alert(alert_id: str, principal: Principal = Depends(get_principal)) -> Response:
    store = _private_store(principal)
    try:
        store.delete_alert(principal.user_id, alert_id)
    except KeyError as exc:
        raise HTTPException(404, "Alert not found") from exc
    except (ConnectionError, RuntimeError) as exc:
        raise HTTPException(503, detail={"code": "private_store_unavailable"}) from exc
    return Response(status_code=204)


@app.get("/api/v1/settings", tags=["settings"])
async def get_settings(principal: Principal = Depends(get_principal)) -> JSONResponse:
    store = _private_store(principal)
    try:
        return json(store.settings(principal.user_id))
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "settings_not_found"}) from exc
    except (ConnectionError, RuntimeError) as exc:
        raise HTTPException(503, detail={"code": "private_store_unavailable"}) from exc


@app.put("/api/v1/settings", tags=["settings"])
async def put_settings(values: dict[str, Any] = Body(...), principal: Principal = Depends(get_principal)) -> JSONResponse:
    store = _private_store(principal)
    try:
        return json(store.update_settings(principal.user_id, values))
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "settings_not_found"}) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (ConnectionError, RuntimeError) as exc:
        raise HTTPException(503, detail={"code": "private_store_unavailable"}) from exc


LOCAL_BACKUP_MEDIA_TYPE = "application/vnd.edgeos.workspace-backup+json"
LOCAL_BACKUP_FILENAME = "edgeos-local-workspace-backup.edgeos-backup"


def _require_desktop_local_workspace(principal: Principal) -> LocalStateDatabase:
    if not principal.is_demo or local_state_database is None:
        raise HTTPException(
            409,
            detail={
                "code": "desktop_local_workspace_unavailable",
                "message": "Portable workspace backup is available only in installed desktop Local Mode.",
            },
        )
    return local_state_database


@app.get("/api/v1/local-workspace/backup", tags=["settings"])
async def export_local_workspace(principal: Principal = Depends(get_principal)) -> Response:
    database = _require_desktop_local_workspace(principal)
    try:
        payload = database.export_portable()
    except LocalPersistenceError as exc:
        raise HTTPException(
            503, detail={"code": "local_backup_failed", "message": str(exc)}
        ) from exc
    return Response(
        content=payload,
        media_type=LOCAL_BACKUP_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{LOCAL_BACKUP_FILENAME}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/api/v1/local-workspace/restore", tags=["settings"])
async def restore_local_workspace(
    request: Request,
    x_edgeos_confirm_restore: str | None = Header(default=None),
    principal: Principal = Depends(get_principal),
) -> JSONResponse:
    database = _require_desktop_local_workspace(principal)
    if x_edgeos_confirm_restore != "REPLACE":
        raise HTTPException(
            409,
            detail={"code": "restore_confirmation_required", "message": "Type RESTORE to confirm replacement."},
        )
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type != LOCAL_BACKUP_MEDIA_TYPE:
        raise HTTPException(415, detail={"code": "invalid_backup_media_type"})
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) < 1 or int(declared) > MAX_BACKUP_BYTES:
                raise HTTPException(413, detail={"code": "backup_too_large"})
        except ValueError as exc:
            raise HTTPException(400, detail={"code": "invalid_content_length"}) from exc
    payload = await request.body()
    if len(payload) > MAX_BACKUP_BYTES:
        raise HTTPException(413, detail={"code": "backup_too_large"})
    try:
        restored = database.restore_portable(payload)
        # Restore is logical and transactional. Rebuild the in-memory facades
        # only after the durable replacement has committed successfully.
        global ledger, user_store, saved_opportunity_store
        ledger = LocalPaperLedger(database)
        user_store = LocalUserStore(database)
        saved_opportunity_store = LocalSavedOpportunityStore(database)
    except LocalPersistenceError as exc:
        raise HTTPException(
            422, detail={"code": "invalid_local_backup", "message": str(exc)}
        ) from exc
    return json({
        "status": "restored",
        "restored_namespaces": restored,
        "restart_required": False,
    })


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshots: list[HistoricalSnapshot]
    config: BacktestConfig = BacktestConfig()


@app.post("/api/v1/backtests", tags=["backtests"])
async def backtest(request: BacktestRequest, _: Principal = Depends(get_principal)) -> JSONResponse:
    return json(run_backtest(request.snapshots, request.config))


@app.delete("/api/v1/account", tags=["account"], status_code=204)
async def delete_account(principal: Principal = Depends(get_principal)) -> Response:
    if principal.is_demo:
        raise HTTPException(409, detail={"code": "demo_account_not_deletable"})
    _require_recent_auth(principal)
    if not settings.supabase_url or not settings.supabase_secret_key or not principal.access_token:
        raise HTTPException(
            503,
            detail={"code": "account_deletion_not_configured", "message": "Supabase admin credentials are required."},
        )
    try:
        await delete_supabase_account(
            supabase_url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
            user_id=principal.user_id,
            access_token=principal.access_token,
        )
    except (httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(502, detail={"code": "account_deletion_failed", "message": str(exc)}) from exc
    return Response(status_code=204)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn
    uvicorn.run("edgeos.main:app", host="0.0.0.0", port=8000, reload=False)

