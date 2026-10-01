"""Fake Kubernetes API for M4 tests and E2E.

Serves payloads built from the official ``kubernetes`` client models, so the
JSON shapes are guaranteed real. Endpoints: /version, /api/v1/nodes,
/api/v1/pods, /apis/apps/v1/{deployments,statefulsets,daemonsets},
/api/v1/events. ``mode="forbidden"`` makes /api/v1/nodes return 403 (RBAC).
"""
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import yaml
from kubernetes import client as k8s


def _node_info(kubelet_version, os_image="Ubuntu 22.04.5 LTS"):
    return k8s.V1NodeSystemInfo(
        machine_id="m1", system_uuid="u1", boot_id="b1",
        kernel_version="5.15.0-91-generic", os_image=os_image,
        container_runtime_version="containerd://1.7.13",
        kubelet_version=kubelet_version,
        kube_proxy_version=kubelet_version,
        operating_system="linux", architecture="amd64")


def _node(name, roles, ready, kubelet_version, unschedulable=False):
    labels = {f"node-role.kubernetes.io/{r}": "" for r in roles}
    labels["kubernetes.io/hostname"] = name
    return k8s.V1Node(
        metadata=k8s.V1ObjectMeta(name=name, labels=labels),
        spec=k8s.V1NodeSpec(unschedulable=unschedulable or None),
        status=k8s.V1NodeStatus(
            conditions=[k8s.V1NodeCondition(type="Ready",
                                            status="True" if ready else "False")],
            node_info=_node_info(kubelet_version),
            capacity={"cpu": "4", "memory": "16384Mi"},
            allocatable={"cpu": "4", "memory": "16384Mi"}))


def _pod(namespace, name, phase, restarts=0, wait_reason=None):
    containers = None
    if wait_reason:
        containers = [k8s.V1ContainerStatus(
            name="main", image="example/bad:1",
            image_id="", container_id="",
            ready=False, restart_count=restarts,
            state=k8s.V1ContainerState(
                waiting=k8s.V1ContainerStateWaiting(reason=wait_reason)))]
    elif restarts:
        containers = [k8s.V1ContainerStatus(
            name="main", image="example/app:1",
            image_id="", container_id="",
            ready=True, restart_count=restarts,
            state=k8s.V1ContainerState(
                running=k8s.V1ContainerStateRunning()))]
    return k8s.V1Pod(
        metadata=k8s.V1ObjectMeta(name=name, namespace=namespace),
        status=k8s.V1PodStatus(phase=phase,
                               container_statuses=containers))


def _pod_template(image):
    return k8s.V1PodTemplateSpec(
        metadata=k8s.V1ObjectMeta(labels={"app": "x"}),
        spec=k8s.V1PodSpec(containers=[
            k8s.V1Container(name="main", image=image)]))


def _deployment(namespace, name, image, replicas, ready, updated):
    now = datetime.now(timezone.utc)
    return k8s.V1Deployment(
        metadata=k8s.V1ObjectMeta(name=name, namespace=namespace,
                                  creation_timestamp=now - timedelta(hours=2)),
        spec=k8s.V1DeploymentSpec(
            replicas=replicas,
            selector=k8s.V1LabelSelector(match_labels={"app": "x"}),
            template=_pod_template(image)),
        status=k8s.V1DeploymentStatus(
            replicas=replicas, ready_replicas=ready,
            updated_replicas=updated, available_replicas=ready))


def _statefulset(namespace, name, image, replicas, ready):
    now = datetime.now(timezone.utc)
    return k8s.V1StatefulSet(
        metadata=k8s.V1ObjectMeta(name=name, namespace=namespace,
                                  creation_timestamp=now - timedelta(days=1)),
        spec=k8s.V1StatefulSetSpec(
            service_name=name, replicas=replicas,
            selector=k8s.V1LabelSelector(match_labels={"app": "x"}),
            template=_pod_template(image)),
        status=k8s.V1StatefulSetStatus(
            replicas=replicas, ready_replicas=ready,
            updated_replicas=ready))


def _daemonset(namespace, name, image, desired, available):
    now = datetime.now(timezone.utc)
    return k8s.V1DaemonSet(
        metadata=k8s.V1ObjectMeta(name=name, namespace=namespace,
                                  creation_timestamp=now - timedelta(days=3)),
        spec=k8s.V1DaemonSetSpec(
            selector=k8s.V1LabelSelector(match_labels={"app": "x"}),
            template=_pod_template(image)),
        status=k8s.V1DaemonSetStatus(
            current_number_scheduled=desired,
            desired_number_scheduled=desired,
            number_available=available,
            number_misscheduled=0,
            number_ready=available,
            updated_number_scheduled=available))


def _event(namespace, name, etype, reason, obj_kind, obj_name, message,
           count, minutes_ago):
    now = datetime.now(timezone.utc)
    ts = now - timedelta(minutes=minutes_ago)
    return k8s.CoreV1Event(
        metadata=k8s.V1ObjectMeta(name=name, namespace=namespace,
                                  creation_timestamp=ts),
        involved_object=k8s.V1ObjectReference(
            kind=obj_kind, name=obj_name, namespace=namespace),
        reason=reason, message=message, type=etype, count=count,
        first_timestamp=ts - timedelta(minutes=5), last_timestamp=ts)


def fixtures():
    api = k8s.ApiClient()
    nodes = k8s.V1NodeList(items=[
        _node("k3s-master-01", ["control-plane", "master"], True, "v1.30.5+k3s1"),
        _node("k3s-worker-01", ["worker"], True, "v1.30.5+k3s1"),
        _node("k3s-worker-02", ["worker"], False, "v1.29.0+k3s1",
              unschedulable=True),
    ])
    pods = k8s.V1PodList(items=[
        _pod("kube-system", "coredns-abc", "Running"),
        _pod("default", "app-1", "Running", restarts=1),
        _pod("default", "app-2", "Running", restarts=7,
             wait_reason="CrashLoopBackOff"),
        _pod("default", "pending-1", "Pending"),
    ])
    ser = api.sanitize_for_serialization
    version = ser(k8s.VersionInfo(
        major="1", minor="30", git_version="v1.30.5+k3s1",
        git_commit="abc123", git_tree_state="clean",
        build_date="2024-01-01T00:00:00Z",
        go_version="go1.22.0", compiler="gc", platform="linux/amd64"))
    deployments = k8s.V1DeploymentList(items=[
        _deployment("default", "web", "example/web:2", 3, 3, 3),      # ready
        _deployment("default", "api", "example/api:7", 3, 3, 2),      # progressing
    ])
    statefulsets = k8s.V1StatefulSetList(items=[
        _statefulset("default", "db", "example/db:1", 3, 1),          # degraded
    ])
    daemonsets = k8s.V1DaemonSetList(items=[
        _daemonset("kube-system", "agent", "example/agent:4", 3, 3),  # ready
    ])
    events = k8s.CoreV1EventList(items=[
        _event("default", "e1", "Warning", "FailedScheduling", "Pod",
               "pending-1", "0/3 nodes are available: insufficient cpu.", 5, 3),
        _event("default", "e2", "Warning", "BackOff", "Pod",
               "app-2", 'Back-off restarting failed container "main".', 12, 10),
        _event("default", "e3", "Normal", "Scheduled", "Pod",
               "app-1", "Successfully assigned default/app-1 to k3s-worker-01.", 1, 60),
    ])
    return {
        "/version": version,
        "/api/v1/nodes": ser(nodes),
        "/api/v1/pods": ser(pods),
        "/apis/apps/v1/deployments": ser(deployments),
        "/apis/apps/v1/statefulsets": ser(statefulsets),
        "/apis/apps/v1/daemonsets": ser(daemonsets),
        "/api/v1/events": ser(events),
    }


def kubeconfig_for(port, scheme="http", insecure=True, token="fake-token",
                   ca_data=None):
    cluster = {"server": f"{scheme}://127.0.0.1:{port}"}
    if insecure:
        cluster["insecure-skip-tls-verify"] = True
    if ca_data:
        cluster["certificate-authority-data"] = ca_data
    user = {"token": token} if token else {}
    return yaml.safe_dump({
        "apiVersion": "v1", "kind": "Config",
        "clusters": [{"name": "fake", "cluster": cluster}],
        "users": [{"name": "fake", "user": user}],
        "contexts": [{"name": "fake",
                      "context": {"cluster": "fake", "user": "fake"}}],
        "current-context": "fake",
    })


class FakeK8s:
    """A fake K8s API server in a background thread."""

    def __init__(self, mode="ok"):
        self.mode = mode
        self.payloads = fixtures()
        handler = self._handler()
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?")[0].rstrip("/") or "/"
                if outer.mode == "forbidden" and path == "/api/v1/nodes":
                    body = {"kind": "Status", "apiVersion": "v1",
                            "status": "Failure",
                            "message": "nodes is forbidden",
                            "reason": "Forbidden", "code": 403}
                    code = 403
                else:
                    body = outer.payloads.get(path)
                    code = 200 if body is not None else 404
                    if body is None:
                        body = {"message": "not found"}
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        return Handler

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
