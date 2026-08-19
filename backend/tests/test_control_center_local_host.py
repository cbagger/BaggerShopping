from fastapi.testclient import TestClient

from app import control_center


def test_request_host_accepts_only_lan_names_or_addresses():
    for host in ("127.0.0.1", "::1", "192.168.0.111", "10.20.30.40", "kurv-nas.local", "localhost", "testserver"):
        assert control_center._request_host_is_local(host) is True
    for host in ("shopping.example.com", "kurv.example.dk", "8.8.8.8", None):
        assert control_center._request_host_is_local(host) is False


def test_public_domain_host_is_rejected_even_from_local_test_client():
    client = TestClient(control_center.app)
    response = client.get("/api/health", headers={"Host": "kurv.example.dk"})
    assert response.status_code == 403
    assert response.json()["ok"] is False


def test_local_ip_host_is_accepted():
    client = TestClient(control_center.app)
    response = client.get("/api/health", headers={"Host": "192.168.0.111:8092"})
    assert response.status_code == 200
    assert response.json()["local_only"] is True
