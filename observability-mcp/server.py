"""Read-only MCP tools for querying the Jaeger API."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP


JAEGER_URL = os.getenv("JAEGER_URL", "http://jaeger:16686").rstrip("/")
MAX_LIMIT = 100

mcp = FastMCP("BOS observability", host="0.0.0.0", port=8081)


async def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(base_url=JAEGER_URL, timeout=10.0) as client:
            response = await client.get(path, params=params)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"Jaeger query failed: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Jaeger returned an unexpected response")
    return payload


@mcp.tool()
async def list_services() -> list[str]:
    """List services that have traces available in Jaeger."""
    payload = await _get("/api/services")
    services = payload.get("data")
    if not isinstance(services, list) or not all(isinstance(item, str) for item in services):
        raise RuntimeError("Jaeger returned an invalid services list")
    return sorted(services)


@mcp.tool()
async def search_traces(
    service: str,
    lookback: str = "1h",
    limit: int = 20,
    min_duration: str | None = None,
    error_only: bool = False,
) -> dict[str, Any]:
    """Search recent traces for a service. Read-only; limit is capped at 100."""
    service = service.strip()
    if not service:
        raise ValueError("service must not be empty")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    if not lookback.strip():
        raise ValueError("lookback must not be empty")

    params: dict[str, Any] = {
        "service": service,
        "lookback": lookback.strip(),
        "limit": limit,
    }
    if min_duration:
        params["minDuration"] = min_duration.strip()
    if error_only:
        params["tags"] = '{"error":"true"}'
    return await _get("/api/traces", params)


@mcp.tool()
async def get_trace(trace_id: str) -> dict[str, Any]:
    """Get all spans and metadata for one Jaeger trace ID."""
    trace_id = trace_id.strip()
    if not trace_id:
        raise ValueError("trace_id must not be empty")
    return await _get(f"/api/traces/{quote(trace_id, safe='')}")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
