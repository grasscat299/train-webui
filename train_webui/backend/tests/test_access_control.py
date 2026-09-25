import pytest
from fastapi.testclient import TestClient

from train_webui.backend.app import create_app
from train_webui.backend.settings import DEFAULT_ALLOWED_NETWORKS

from .conftest import FakeRemote, make_settings


@pytest.fixture()
def restricted_app(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    settings = make_settings(tmp_path, root, allowed_networks=DEFAULT_ALLOWED_NETWORKS)
    app = create_app(settings, remote=FakeRemote(), start_background=False)
    yield app
    app.state.job_manager.shutdown()


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "100.101.102.103", "fd7a:115c:a1e0::1", "::ffff:100.100.0.19"])
def test_vpn_and_loopback_are_allowed(restricted_app, host):
    resp = TestClient(restricted_app, client=(host, 50000)).get("/api/state")
    assert resp.status_code == 200


@pytest.mark.parametrize("host", ["192.168.1.10", "172.19.1.1", "8.8.8.8", "100.128.0.1", "testclient"])
def test_outside_vpn_is_rejected(restricted_app, host):
    client = TestClient(restricted_app, client=(host, 50000))
    assert client.get("/api/state").status_code == 403
    assert client.post("/api/results", json={"run_id": "x", "source": "s", "items": []}).status_code == 403
