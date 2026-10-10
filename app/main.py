"""HTTP interface. Run with: uvicorn app.main:app --reload"""

import csv
from contextlib import asynccontextmanager
from io import StringIO
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Path, Query, Response

from app.history import HistoryError
from app.models import MonthlyCandle, MonthlyHistory
from app.service import get_monthly_history
from app.mcp_server import create_mcp_server


def health() -> dict[str, str]:
    """Application liveness; does not call the data provider."""
    return {"status": "ok"}


def monthly_history(
    symbol: Annotated[str, Path(min_length=1, max_length=43, examples=["TCS"])],
    exchange: Annotated[Literal["NSE", "BSE"] | None, Query(description="Defaults to NSE, or the symbol's .NS/.BO suffix.")] = None,
    adjusted: Annotated[bool, Query(description="Apply Yahoo's adjusted-close factor to daily OHLC before aggregation.")] = False,
    include_incomplete: Annotated[bool, Query(description="Include the ongoing month. Set false for completed calendar months only.")] = True,
    format: Annotated[Literal["json", "csv"], Query(description="JSON metadata and candles, or a downloadable CSV.")] = "json",
) -> MonthlyHistory | Response:
    try:
        result = get_monthly_history(symbol, exchange, adjusted, include_incomplete)
    except HistoryError as exc:
        headers = {"Retry-After": "60"} if exc.status_code == 503 else None
        raise HTTPException(exc.status_code, str(exc), headers=headers) from exc

    if format == "csv":
        output = StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=list(MonthlyCandle.model_fields))
        writer.writeheader()
        for candle in result.data:
            writer.writerow(candle.model_dump(mode="json"))
        return Response(
            output.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{result.symbol}_monthly.csv"',
                "X-Data-Source": "Yahoo Finance",
                "X-Price-Adjusted": str(adjusted).lower(),
                "X-First-Available-Date": result.first_available_date.isoformat(),
                "X-Last-Available-Date": result.last_available_date.isoformat(),
                "X-Missing-Months-Count": str(len(result.missing_months)),
                "X-Coverage-Verified": "false",
            },
        )
    return result


def create_app() -> FastAPI:
    mcp = create_mcp_server()
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp.session_manager.run():
            yield

    api = FastAPI(
        title="Indian Stock Monthly OHLC API",
        version="1.1.0",
        description=(
            "Monthly OHLC from 2000-01-01 through today in Asia/Kolkata, "
            "limited to available Yahoo Finance history. MCP is available at /mcp without authentication."
        ),
        lifespan=lifespan,
    )
    api.add_api_route("/health", health, methods=["GET"])
    api.add_api_route(
        "/api/stocks/{symbol}/monthly",
        monthly_history,
        methods=["GET"],
        response_model=MonthlyHistory,
        responses={
            200: {"content": {"text/csv": {"schema": {"type": "string"}}}},
            404: {"description": "No history available for this symbol"},
            502: {"description": "Provider unavailable or invalid prices"},
            503: {"description": "Provider rate limit; retry later"},
        },
    )
    # Mount last: REST/docs keep their routes; the SDK serves the exact /mcp path.
    api.mount("/", mcp_app)
    return api


app = create_app()
