"""M4 cluster tests (v1: onboarding, overview, node list).

The fake K8s API serves payloads built from the official kubernetes client
models, so response shapes are guaranteed real.
"""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="drydock-k8s-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["CELERY_EAGER"] = "1"

import pytest
from fastapi.testclient import TestClient
from kubernetes.client.rest import ApiException

from app import main as main_mod
from app import k8s as k8s_mod
from app.db import init_db, session_scope
from app.main import app
from app.models import Cluster, Host, Job, MaintenanceRun, NotificationChannel, \
    Playbook, Schedule, Template
from tests.fake_k8s import FakeK8s, kubeconfig_for


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main_mod, "keyscan", lambda address, port: "fake-host-key-line")
    init_db()
    _clear_db()
    with TestClient(app) as c:
        yield c
    _clear_db()


def _clear_db():
    with session_scope() as s:
        for m in (Cluster, NotificationChannel, Schedule, MaintenanceRun,
                  Job, Template, Playbook, Host):
            s.query(m).delete()


@pytest.fixture
def fake():
    f = FakeK8s()
    yield f
    f.stop()


def _onboard(client, fake, name="homelab"):
    r = client.post("/api/clusters", json={
        "name": name, "kubeconfig": kubeconfig_for(fake.port)})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_onboard_and_list(client, fake):
    cid = _onboard(client, fake)
    r = client.post("/api/clusters", json={
        "name": "dup", "kubeconfig": kubeconfig_for(fake.port)})
    assert r.status_code == 200
    cs = client.get("/api/clusters").json()
    assert len(cs) == 2
    c = cs[0]
    assert c["server"] == f"http://127.0.0.1:{fake.port}"
    assert c["k8s_version"] == "v1.30.5+k3s1"
    assert c["status"] == "ok"
    assert "kubeconfig" not in c  # never returned


def test_overview(client, fake):
    cid = _onboard(client, fake)
    ov = client.get(f"/api/clusters/{cid}/overview").json()
    assert ov["stale"] is False
    assert ov["version"] == "v1.30.5+k3s1"
    assert (ov["nodes_ready"], ov["nodes_total"]) == (2, 3)
    assert ov["pods_total"] == 4
    assert ov["pods_by_phase"]["Running"] == 3
    assert ov["pods_by_phase"]["Pending"] == 1
    names = {(p["namespace"], p["name"]) for p in ov["abnormal_pods"]}
    assert ("default", "app-2") in names      # CrashLoopBackOff
    assert ("default", "pending-1") in names  # Pending
    app2 = next(p for p in ov["abnormal_pods"] if p["name"] == "app-2")
    assert app2["reason"] == "CrashLoopBackOff"
    assert app2["restarts"] == 7


def test_nodes_parsed(client, fake):
    cid = _onboard(client, fake)
    nodes = client.get(f"/api/clusters/{cid}/nodes").json()["nodes"]
    assert len(nodes) == 3
    by_name = {n["name"]: n for n in nodes}
    assert by_name["k3s-master-01"]["roles"] == ["control-plane", "master"]
    assert by_name["k3s-master-01"]["ready"] is True
    w2 = by_name["k3s-worker-02"]
    assert w2["ready"] is False
    assert w2["unschedulable"] is True  # cordoned
    assert w2["kubelet_version"] == "v1.29.0+k3s1"  # drifted
    assert by_name["k3s-worker-01"]["cri"].startswith("containerd://")
    assert "Ubuntu" in by_name["k3s-worker-01"]["os_image"]


def test_onboard_rejects_bad_kubeconfig(client, fake):
    assert client.post("/api/clusters", json={
        "name": "x", "kubeconfig": "not: [valid"}).status_code == 400
    assert client.post("/api/clusters", json={
        "name": "x", "kubeconfig": "kind: Config\nclusters: []\n"}).status_code == 400
    assert client.post("/api/clusters", json={
        "name": "x", "kubeconfig": ""}).status_code == 400


def test_onboard_unreachable_categorized(client):
    r = client.post("/api/clusters", json={
        "name": "x", "kubeconfig": kubeconfig_for(1)})  # nothing on port 1
    assert r.status_code == 400
    assert "network unreachable" in r.json()["detail"]


def test_rbac_403_categorized(client):
    fake = FakeK8s(mode="forbidden")
    try:
        r = client.post("/api/clusters", json={
            "name": "x", "kubeconfig": kubeconfig_for(fake.port)})
        assert r.status_code == 400
        assert "RBAC" in r.json()["detail"]
    finally:
        fake.stop()


def test_stale_when_api_dies(client, fake):
    cid = _onboard(client, fake)
    fake.stop()  # cluster goes away
    ov = client.get(f"/api/clusters/{cid}/overview").json()
    assert ov["stale"] is True
    assert "network unreachable" in ov["error"]
    c = client.get(f"/api/clusters/{cid}").json()
    assert c["status"] == "error"
    # and it recovers when the API is back (retest still fails: port dead)
    r = client.post(f"/api/clusters/{cid}/test")
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_delete_cluster(client, fake):
    cid = _onboard(client, fake)
    assert client.delete(f"/api/clusters/{cid}").status_code == 200
    assert client.get("/api/clusters").json() == []
    assert client.get(f"/api/clusters/{cid}").status_code == 404


def test_workloads(client, fake):
    cid = _onboard(client, fake)
    wl = client.get(f"/api/clusters/{cid}/workloads").json()
    assert wl["stale"] is False
    ws = {(w["kind"], w["name"]): w for w in wl["workloads"]}
    assert len(ws) == 4
    assert ws[("Deployment", "web")]["status"] == "ready"
    assert ws[("Deployment", "web")]["ready"] == 3
    assert ws[("Deployment", "web")]["images"] == ["example/web:2"]
    assert ws[("Deployment", "api")]["status"] == "progressing"  # 2/3 updated
    assert ws[("StatefulSet", "db")]["status"] == "degraded"     # 1/3 ready
    assert ws[("DaemonSet", "agent")]["status"] == "ready"


def test_events_warnings_first(client, fake):
    cid = _onboard(client, fake)
    ev = client.get(f"/api/clusters/{cid}/events").json()
    assert ev["stale"] is False
    es = ev["events"]
    assert len(es) == 3
    # warnings first, newest first
    assert [e["type"] for e in es] == ["Warning", "Warning", "Normal"]
    assert es[0]["reason"] == "FailedScheduling"  # 3 min ago
    assert es[1]["reason"] == "BackOff"           # 10 min ago
    assert es[0]["count"] == 5
    assert es[0]["name"] == "pending-1"
    assert "insufficient cpu" in es[0]["message"]


def test_categorize_error_unit():
    assert "RBAC" in k8s_mod.categorize_error(ApiException(status=403))
    assert "401" in k8s_mod.categorize_error(ApiException(status=401))
    assert "certificate expired" in k8s_mod.categorize_error(
        Exception("SSL: certificate has expired"))
    assert "network unreachable" in k8s_mod.categorize_error(
        Exception("HTTPSConnectionPool: NewConnectionError"))


def test_parse_kubeconfig_unit():
    info = k8s_mod.parse_kubeconfig(kubeconfig_for(6443))
    assert info["server"] == "http://127.0.0.1:6443"
    assert info["cluster_name"] == "fake"
    with pytest.raises(ValueError):
        k8s_mod.parse_kubeconfig("clusters: []\n")
