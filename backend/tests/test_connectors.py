import json
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber
import boto3

from app.connectors.base import ConnectorError, with_retries
from app.connectors.aws import AwsConnector, ReadOnlyClient
from app.connectors.azure import AzureConnector
from app.connectors.gcp import GcpConnector
from app.connectors.demo import DemoConnector

NOW = datetime.now(timezone.utc)


def test_retries_only_retryable():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectorError("throttle", "slow")
        return "ok"

    assert with_retries(flaky, sleep=lambda s: None) == "ok" and calls["n"] == 3
    calls["n"] = 0

    def auth():
        calls["n"] += 1
        raise ConnectorError("auth", "no")

    with pytest.raises(ConnectorError):
        with_retries(auth, sleep=lambda s: None)
    assert calls["n"] == 1


def test_retries_give_up():
    with pytest.raises(ConnectorError):
        with_retries(lambda: (_ for _ in ()).throw(ConnectorError("transient", "x")), attempts=3, sleep=lambda s: None)


def test_readonly_wrapper_blocks_writes():
    c = ReadOnlyClient(boto3.client("logs", region_name="us-east-1", aws_access_key_id="a", aws_secret_access_key="b"))
    for bad in ("put_log_events", "delete_log_group", "create_log_group"):
        with pytest.raises(PermissionError):
            getattr(c, bad)


def _aws():
    client = boto3.client("logs", region_name="us-east-1", aws_access_key_id="a", aws_secret_access_key="b")
    return client, Stubber(client)


def test_aws_fetch_paginates_and_maps():
    client, st = _aws()
    ts = int(NOW.timestamp() * 1000)
    st.add_response("filter_log_events", {"events": [{"eventId": "1", "timestamp": ts, "message": "[svc] ERROR boom", "logStreamName": "s1"}], "nextToken": "n"})
    st.add_response("filter_log_events", {"events": [{"eventId": "2", "timestamp": ts + 1, "message": "later", "logStreamName": "s1"}]})
    st.activate()
    c = AwsConnector({"log_groups": ["/app/x"]}, {}, client=client)
    recs = c.fetch(NOW - timedelta(minutes=5), NOW)
    assert [r.uid for r in recs] == ["1", "2"] and recs[0].service == "x"


def test_aws_throttle_retried_then_ok():
    client, st = _aws()
    st.add_client_error("filter_log_events", "ThrottlingException")
    st.add_response("filter_log_events", {"events": []})
    st.activate()
    c = AwsConnector({"log_groups": ["/g"]}, {}, client=client, sleep=lambda s: None)
    assert c.fetch(NOW - timedelta(minutes=1), NOW) == []


def test_aws_auth_error_not_retried():
    client, st = _aws()
    st.add_client_error("filter_log_events", "AccessDeniedException")
    st.activate()
    c = AwsConnector({"log_groups": ["/g"]}, {}, client=client, sleep=lambda s: None)
    with pytest.raises(ConnectorError) as e:
        c.fetch(NOW - timedelta(minutes=1), NOW)
    assert e.value.kind == "auth"


def test_aws_missing_group_config():
    client, _ = _aws()
    with pytest.raises(ConnectorError) as e:
        AwsConnector({}, {}, client=client).fetch(NOW - timedelta(minutes=1), NOW)
    assert e.value.kind == "config"


def _azure_transport(query_status=200, token_status=200, seen=None):
    def h(req: httpx.Request):
        if "login.microsoftonline.com" in str(req.url):
            return httpx.Response(token_status, json={"access_token": "t", "expires_in": 3600} if token_status == 200 else {"error": "bad"})
        if seen is not None:
            seen.append(json.loads(req.content)["query"])
        if query_status != 200:
            return httpx.Response(query_status, text="err")
        return httpx.Response(200, json={"tables": [{"columns": [{"name": n} for n in ("TimeGenerated", "Computer", "Message", "Level", "Proc", "Id")],
                                                    "rows": [[NOW.isoformat().replace("+00:00", "Z"), "vm1", "disk error", "3", "kernel", "1"]]}]})
    return httpx.MockTransport(h)


CFG = {"workspace_id": "w", "tenant_id": "t", "client_id": "c", "computer": "vm1"}


def test_azure_fetch_maps_level_and_filters_computer():
    seen = []
    c = AzureConnector(CFG, {"client_secret": "s"}, transport=_azure_transport(seen=seen), sleep=lambda s: None)
    recs = c.fetch(NOW - timedelta(minutes=5), NOW)
    assert recs[0].level == "ERROR" and recs[0].host == "vm1" and 'Computer == "vm1"' in seen[0]


def test_azure_errors():
    with pytest.raises(ConnectorError) as e:
        AzureConnector(CFG, {"client_secret": "s"}, transport=_azure_transport(token_status=400)).fetch(NOW, NOW)
    assert e.value.kind == "auth"
    with pytest.raises(ConnectorError) as e:
        AzureConnector(CFG, {"client_secret": "s"}, transport=_azure_transport(query_status=403)).fetch(NOW, NOW)
    assert e.value.kind == "auth"
    with pytest.raises(ConnectorError) as e:
        AzureConnector(CFG, {}, transport=_azure_transport()).fetch(NOW, NOW)
    assert e.value.kind == "config"


def test_azure_kql_injection_and_dangerous_ops_rejected():
    with pytest.raises(ConnectorError):
        AzureConnector({**CFG, "computer": 'x" | drop'}, {"client_secret": "s"}, transport=_azure_transport()).fetch(NOW, NOW)
    with pytest.raises(ConnectorError):
        AzureConnector({**CFG, "kql": ".drop table X"}, {"client_secret": "s"}, transport=_azure_transport()).fetch(NOW, NOW)


def test_azure_transient_retry():
    n = {"i": 0}

    def h(req):
        if "login" in str(req.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        n["i"] += 1
        return httpx.Response(503) if n["i"] == 1 else httpx.Response(200, json={"tables": []})

    c = AzureConnector(CFG, {"client_secret": "s"}, transport=httpx.MockTransport(h), sleep=lambda s: None)
    assert c.fetch(NOW - timedelta(minutes=1), NOW) == [] and n["i"] == 2


def _sa():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return {"private_key": pem, "client_email": "sa@p.iam.gserviceaccount.com"}


def test_gcp_fetch_paginates():
    pages = [{"entries": [{"insertId": "a", "timestamp": NOW.isoformat(), "textPayload": "boom", "severity": "ERROR", "resource": {"type": "gce_instance", "labels": {"instance_id": "i1"}}}], "nextPageToken": "p2"},
             {"entries": [{"insertId": "b", "timestamp": NOW.isoformat(), "jsonPayload": {"message": "json msg"}, "severity": "DEFAULT", "resource": {"type": "cloud_run_revision", "labels": {"service_name": "api"}}}]}]

    def h(req):
        if "oauth2" in str(req.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(200, json=pages.pop(0))

    c = GcpConnector({"project_id": "p"}, {"service_account_json": json.dumps(_sa())}, transport=httpx.MockTransport(h), sleep=lambda s: None)
    recs = c.fetch(NOW - timedelta(minutes=5), NOW + timedelta(seconds=1))
    assert [r.uid for r in recs] == ["a", "b"] and recs[0].level == "ERROR" and recs[1].service == "api"


def test_gcp_bad_key_is_config_error():
    with pytest.raises(ConnectorError) as e:
        GcpConnector({"project_id": "p"}, {"service_account_json": {"private_key": "nope", "client_email": "x"}}).fetch(NOW, NOW)
    assert e.value.kind == "config"


def test_demo_is_deterministic_and_scenario_has_errors():
    cfg = {"scenario": "connection_pool", "scenario_started_at": (NOW - timedelta(minutes=10)).isoformat()}
    a = DemoConnector(cfg).fetch(NOW - timedelta(minutes=30), NOW)
    b = DemoConnector(cfg).fetch(NOW - timedelta(minutes=30), NOW)
    assert [r.uid for r in a] == [r.uid for r in b]
    assert any("Connection pool exhausted" in r.message for r in a)
    assert not any(r.level == "ERROR" for r in DemoConnector({"scenario": "quiet"}).fetch(NOW - timedelta(minutes=30), NOW))
