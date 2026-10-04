"""Tests for the Gamma market-discovery client (task 0.5.14).

Parsing is tested offline against the exact wire shapes Gamma returns
(JSON-encoded string arrays). Transport is a fake fetcher; no network.
"""

from __future__ import annotations

import json

import pytest

from polymm.data.gamma import GammaClient, GammaError, parse_market

pytestmark = pytest.mark.unit


def raw_market(**overrides: object) -> dict:
    base: dict = {
        "id": "123",
        "question": "Will X happen?",
        "conditionId": "0xcond",
        "clobTokenIds": json.dumps(["111", "222"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(["0.6", "0.4"]),
        "negRisk": False,
        "active": True,
        "closed": False,
    }
    base.update(overrides)
    return base


# ── parsing ───────────────────────────────────────────────────


def test_parse_market_basic() -> None:
    m = parse_market(raw_market())
    assert m.condition_id == "0xcond"
    assert m.yes_token_id == "111"
    assert m.no_token_id == "222"
    assert m.question == "Will X happen?"
    assert m.neg_risk is False


def test_parse_market_handles_real_lists_too() -> None:
    m = parse_market(raw_market(clobTokenIds=["111", "222"]))
    assert m.yes_token_id == "111"
    assert m.no_token_id == "222"


def test_parse_market_rejects_multi_outcome() -> None:
    """Only true binaries can be complete-set arbitraged.

    Buying the first two tokens of a 3-outcome market is not a complete set
    and would report a fake edge, so it must be refused.
    """
    with pytest.raises(GammaError, match="only binary markets"):
        parse_market(
            raw_market(
                clobTokenIds=json.dumps(["111", "222", "333"]),
                outcomes=json.dumps(["Yes", "No", "Maybe"]),
            )
        )


def test_parse_market_reads_end_date_and_accepting_orders() -> None:
    m = parse_market(raw_market(endDate="2026-12-31T00:00:00Z", acceptingOrders=False))
    assert m.end_date == "2026-12-31T00:00:00Z"
    assert m.accepting_orders is False
    assert m.outcome_count == 2


def test_parse_market_maps_labels_not_positions() -> None:
    # A market that reports No first must not be mislabelled.
    m = parse_market(
        raw_market(
            clobTokenIds=json.dumps(["222", "111"]),
            outcomes=json.dumps(["No", "Yes"]),
        )
    )
    assert m.yes_token_id == "111"
    assert m.no_token_id == "222"


def test_parse_market_yes_no_labels_keep_order() -> None:
    m = parse_market(
        raw_market(
            clobTokenIds=json.dumps(["111", "222"]),
            outcomes=json.dumps(["Yes", "No"]),
        )
    )
    assert m.yes_token_id == "111"
    assert m.no_token_id == "222"


def test_parse_market_unknown_labels_fall_back_to_order() -> None:
    m = parse_market(raw_market(outcomes=json.dumps(["Up", "Down"])))
    assert m.yes_token_id == "111"
    assert m.no_token_id == "222"


def test_parse_market_neg_risk_flag() -> None:
    assert parse_market(raw_market(negRisk=True)).neg_risk is True
    assert parse_market(raw_market(negRiskAugmented=True)).neg_risk is True


def test_parse_market_falls_back_to_title() -> None:
    m = parse_market(raw_market(question=None, title="T"))
    assert m.question == "T"


def test_parse_market_rejects_missing_tokens() -> None:
    with pytest.raises(GammaError, match="clobTokenIds"):
        parse_market(raw_market(clobTokenIds="[]"))


def test_parse_market_rejects_missing_condition() -> None:
    with pytest.raises(GammaError, match="conditionId"):
        parse_market(raw_market(conditionId=""))


def test_parse_market_rejects_malformed_token_json() -> None:
    with pytest.raises(GammaError, match="clobTokenIds"):
        parse_market(raw_market(clobTokenIds="{not json"))


# ── client with a fake fetcher ────────────────────────────────


class FakeFetcher:
    def __init__(self, payload, *, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail
        self.calls: list[tuple[str, dict | None]] = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        if self.fail:
            raise RuntimeError("network down")
        return self.payload


def test_fetch_markets_sends_expected_params() -> None:
    fetcher = FakeFetcher([raw_market()])
    client = GammaClient(fetcher=fetcher)
    data = client.fetch_markets(limit=5, offset=10)
    assert len(data) == 1
    url, params = fetcher.calls[0]
    assert url.endswith("/markets")
    assert params["limit"] == 5
    assert params["offset"] == 10
    assert params["active"] == "true"
    assert params["closed"] == "false"


def test_fetch_tradable_markets_parses() -> None:
    client = GammaClient(fetcher=FakeFetcher([raw_market()]))
    markets = client.fetch_tradable_markets(limit=1)
    assert len(markets) == 1
    assert markets[0].yes_token_id == "111"


def test_fetch_tradable_skips_unparseable_and_reports() -> None:
    payload = [raw_market(), raw_market(id="bad", clobTokenIds="[]")]
    skipped: list[tuple] = []
    client = GammaClient(fetcher=FakeFetcher(payload))
    markets = client.fetch_tradable_markets(on_skip=lambda raw, exc: skipped.append((raw, exc)))
    assert len(markets) == 1
    assert len(skipped) == 1


def test_fetch_markets_rejects_non_list() -> None:
    client = GammaClient(fetcher=FakeFetcher({"not": "a list"}))
    with pytest.raises(GammaError, match="expected a list"):
        client.fetch_markets()


def test_transport_error_is_wrapped() -> None:
    client = GammaClient(fetcher=FakeFetcher(None, fail=True))
    with pytest.raises(GammaError, match="Gamma request failed"):
        client.fetch_markets()


def test_no_fetcher_configured_raises() -> None:
    with pytest.raises(GammaError, match="no fetcher"):
        GammaClient().fetch_markets()
