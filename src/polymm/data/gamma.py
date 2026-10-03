"""Gamma API client: market discovery and metadata.

Gamma is the read-only metadata API. It is where the bot learns which
token ids form a binary market (``clobTokenIds``) and whether the market
uses the neg-risk exchange (``negRisk``).

Parsing is separated from transport so it can be tested offline against
recorded JSON. The transport layer is injected, so unit tests pass a fake
fetcher and never touch the network.

The wire format is quirky and this module does not hide that: several
fields (``outcomes``, ``clobTokenIds``, ``outcomePrices``) are JSON-encoded
*strings*, not arrays.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from polymm.market import Market


class GammaError(RuntimeError):
    """Any failure fetching or parsing Gamma data."""


class Fetcher(Protocol):
    """Minimal HTTP shape: get a URL, return parsed JSON."""

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any: ...


def _parse_json_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def parse_market(raw: dict[str, Any]) -> Market:
    """Turn one Gamma market object into our :class:`Market`.

    Gamma lists ``clobTokenIds`` in the same order as ``outcomes``, but we
    never assume which label is which: we locate the YES/NO label explicitly
    so a market that reports ``["No", "Yes"]`` still maps correctly. If
    labels are absent or unrecognised we fall back to the documented
    Yes-then-No order.

    Raises:
        GammaError: when the market lacks the two CLOB token ids needed to
            trade it — we refuse to guess which token is YES.
    """
    tokens = _parse_json_array(raw.get("clobTokenIds"))
    outcomes = _parse_json_array(raw.get("outcomes"))
    if len(tokens) < 2:
        raise GammaError(f"market {raw.get('id')!r} has no clobTokenIds pair")

    condition_id = raw.get("conditionId") or raw.get("condition_id") or ""
    if not condition_id:
        raise GammaError(f"market {raw.get('id')!r} has no conditionId")

    yes_index, no_index = _yes_no_indices(outcomes)
    yes_token, no_token = str(tokens[yes_index]), str(tokens[no_index])
    neg_risk = bool(raw.get("negRisk") or raw.get("negRiskAugmented"))

    return Market(
        condition_id=str(condition_id),
        yes_token_id=yes_token,
        no_token_id=no_token,
        question=str(raw.get("question") or raw.get("title") or ""),
        neg_risk=neg_risk,
    )


def _yes_no_indices(outcomes: list[Any]) -> tuple[int, int]:
    """Return (yes_index, no_index) into the token array.

    Defaults to (0, 1) — Gamma's documented Yes/No ordering — unless the
    labels say otherwise.
    """
    if len(outcomes) >= 2:
        labels = [str(o).strip().lower() for o in outcomes[:2]]
        if labels == ["no", "yes"]:
            return 1, 0
        if labels == ["yes", "no"]:
            return 0, 1
    return 0, 1


@dataclass
class GammaClient:
    """Read-only Gamma API client."""

    base_url: str = "https://gamma-api.polymarket.com"
    fetcher: Fetcher | None = None
    page_size: int = 100

    def _fetch(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        if self.fetcher is None:
            raise GammaError("no fetcher configured (network client not injected)")
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        try:
            return self.fetcher.get_json(url, params)
        except Exception as exc:  # noqa: BLE001 - normalise transport errors
            raise GammaError(f"Gamma request failed: {exc}") from exc

    def fetch_markets(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False,
    ) -> list[dict[str, Any]]:
        """Raw market objects (unparsed), newest first."""
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "order": "createdAt",
            "ascending": "false",
            "active": str(active).lower(),
            "closed": str(closed).lower(),
        }
        data = self._fetch("markets", params)
        if not isinstance(data, list):
            raise GammaError(f"expected a list of markets, got {type(data).__name__}")
        return data

    def fetch_tradable_markets(
        self,
        *,
        limit: int = 100,
        on_skip: Callable[[dict[str, Any], Exception], None] | None = None,
    ) -> list[Market]:
        """Parsed, tradable binary markets; unparseable ones are skipped.

        A market without a token pair cannot be traded, so it is dropped
        (optionally reported via ``on_skip``) rather than failing the scan.
        """
        out: list[Market] = []
        for raw in self.fetch_markets(limit=limit):
            try:
                out.append(parse_market(raw))
            except GammaError as exc:
                if on_skip is not None:
                    on_skip(raw, exc)
        return out


class UrllibFetcher:
    """Real HTTP fetcher using the standard library (no extra dependency).

    Only constructed on the live path. Kept tiny and dependency-free so the
    read-only metadata call does not pull in requests/httpx.
    """

    def __init__(self, *, timeout: float = 15.0) -> None:
        self.timeout = timeout

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        import urllib.parse
        import urllib.request

        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(  # noqa: S310 - fixed https base_url
            url, headers={"User-Agent": "polymm/0.1"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
