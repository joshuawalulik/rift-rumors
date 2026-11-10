"""CargoClient tests against a fake HTTP server: no network, no real waiting."""

import httpx
import pytest

from rift_rumors.data.leaguepedia import PAGE_SIZE, CargoClient, LeaguepediaError


def make_client(tmp_path, handler):
    sleeps: list[float] = []
    client = CargoClient(
        tmp_path,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
        log=lambda msg: None,
        min_interval_s=0,
    )
    return client, sleeps


def cargo(rows):
    return httpx.Response(200, json={"cargoquery": [{"title": r} for r in rows]})


QUERY = dict(tables="Players=P", fields="P.ID=id")


def test_follows_pagination(tmp_path):
    def handler(request):
        offset = int(request.url.params["offset"])
        n = PAGE_SIZE if offset == 0 else 3  # one full page, then a short one
        return cargo([{"id": str(offset + i)} for i in range(n)])

    client, _ = make_client(tmp_path, handler)
    rows = client.query(**QUERY)
    assert len(rows) == PAGE_SIZE + 3
    assert client.requests_made == 2


def test_second_run_is_served_from_disk_cache(tmp_path):
    client, _ = make_client(tmp_path, lambda r: cargo([{"id": "Faker"}]))
    client.query(**QUERY)

    offline, _ = make_client(tmp_path, lambda r: pytest.fail("hit the network"))
    assert offline.query(**QUERY) == [{"id": "Faker"}]
    assert offline.requests_made == 0


def test_max_age_forces_refetch(tmp_path):
    client, _ = make_client(tmp_path, lambda r: cargo([{"id": "old"}]))
    client.query(**QUERY)

    fresh, _ = make_client(tmp_path, lambda r: cargo([{"id": "new"}]))
    assert fresh.query(**QUERY, max_age_s=0) == [{"id": "new"}]


def test_rate_limit_backs_off_then_succeeds(tmp_path):
    responses = iter(
        [
            httpx.Response(200, json={"error": {"code": "ratelimited", "info": "slow down"}}),
            httpx.Response(429),
            cargo([{"id": "Chovy"}]),
        ]
    )
    client, sleeps = make_client(tmp_path, lambda r: next(responses))
    assert client.query(**QUERY) == [{"id": "Chovy"}]
    assert sleeps == [30, 60]  # exponential backoff


def test_gives_up_after_max_retries(tmp_path):
    limited = httpx.Response(200, json={"error": {"code": "ratelimited", "info": "no"}})
    client, _ = make_client(tmp_path, lambda r: limited)
    with pytest.raises(LeaguepediaError, match="Gave up"):
        client.query(**QUERY)


def test_other_api_errors_raise_immediately(tmp_path):
    bad = httpx.Response(200, json={"error": {"code": "badfield", "info": "no such field"}})
    client, _ = make_client(tmp_path, lambda r: bad)
    with pytest.raises(LeaguepediaError, match="badfield"):
        client.query(**QUERY)
    assert client.requests_made == 1


def login_server(result="Success", seen=None):
    """Fake MediaWiki: hands out a login token, accepts/rejects the login, serves cargo."""

    def handler(request):
        params = request.url.params
        if request.method == "GET" and params.get("meta") == "tokens":
            return httpx.Response(200, json={"query": {"tokens": {"logintoken": "tok+\\"}}})
        if request.method == "POST":
            body = request.content.decode()
            if seen is not None:
                seen.append(body)
            return httpx.Response(200, json={"login": {"result": result, "reason": "bad pw"}})
        return cargo([{"id": "Faker"}])

    return handler


def test_logs_in_before_first_network_query(tmp_path):
    seen = []
    client, _ = make_client(tmp_path, login_server(seen=seen))
    client.username, client.bot_password = "Me@riftrumors", "secret"
    assert client.query(**QUERY) == [{"id": "Faker"}]
    assert "lgname=Me%40riftrumors" in seen[0] and "lgtoken=" in seen[0]


def test_failed_login_explains_username_format(tmp_path):
    client, _ = make_client(tmp_path, login_server(result="Failed"))
    client.username, client.bot_password = "Me", "wrong"
    with pytest.raises(LeaguepediaError, match="YourAccount@BotName"):
        client.query(**QUERY)


def test_cache_hits_never_log_in(tmp_path):
    client, _ = make_client(tmp_path, lambda r: cargo([{"id": "Faker"}]))
    client.query(**QUERY)

    def no_network(request):
        pytest.fail("logged in or queried despite a cache hit")

    offline, _ = make_client(tmp_path, no_network)
    offline.username, offline.bot_password = "Me@riftrumors", "secret"
    assert offline.query(**QUERY) == [{"id": "Faker"}]


def test_anonymous_give_up_points_to_bot_password(tmp_path):
    limited = httpx.Response(200, json={"error": {"code": "ratelimited", "info": "no"}})
    client, _ = make_client(tmp_path, lambda r: limited)
    with pytest.raises(LeaguepediaError, match="LEAGUEPEDIA_BOT_PASSWORD"):
        client.query(**QUERY)


def test_where_values_are_quoted():
    from rift_rumors.data.ground_truth import _in, _quote

    assert _quote("O'Brien") == "'O''Brien'"
    assert _quote("back\\") == "'back\\\\'"  # a trailing backslash can't eat the quote
    assert _in("P.Player", ["a", "b'c"]) == "P.Player IN ('a', 'b''c')"
