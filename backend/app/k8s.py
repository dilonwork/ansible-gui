"""Kubernetes cluster access (M4): kubeconfig -> official client, read-only views.

Every function takes the raw kubeconfig YAML, writes it to a 600 temp file,
talks to the API with the official ``kubernetes`` client, and deletes the
file. Connection failures are categorized (4.1 acceptance criterion) instead
of surfacing as generic errors.
"""
import logging
import os
import stat
import tempfile

import yaml
from kubernetes import client as k8s
from kubernetes.client.rest import ApiException
from kubernetes.config import load_kube_config

log = logging.getLogger("drydock.k8s")

# container waiting reasons that mean "abnormal" even when phase == Running
ABNORMAL_WAIT = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
                 "CreateContainerConfigError", "InvalidImageName"}

# never let a hung API hang a web request forever
REQUEST_TIMEOUT = 15


def parse_kubeconfig(kubeconfig_yaml: str) -> dict:
    """Extract cluster name + server from a kubeconfig. Raises ValueError."""
    try:
        data = yaml.safe_load(kubeconfig_yaml)
    except Exception as e:
        raise ValueError(f"not valid YAML: {e}")
    if not isinstance(data, dict):
        raise ValueError("kubeconfig must be a YAML mapping")
    clusters = data.get("clusters") or []
    if not clusters:
        raise ValueError("kubeconfig has no clusters")
    current = data.get("current-context")
    contexts = {c["name"]: c["context"] for c in (data.get("contexts") or [])
                if "name" in c}
    cluster_name = None
    if current and current in contexts:
        cluster_name = contexts[current].get("cluster")
    if not cluster_name:
        cluster_name = clusters[0]["name"]
    server = next((c["cluster"].get("server") for c in clusters
                   if c["name"] == cluster_name), None)
    if not server:
        raise ValueError(f"cluster {cluster_name!r} has no server address")
    return {"cluster_name": cluster_name, "server": server}


def categorize_error(e: Exception) -> str:
    msg = str(e)
    if isinstance(e, ApiException):
        if e.status == 401:
            return "authentication failed (401): check the token/certificate"
        if e.status == 403:
            return "insufficient RBAC permissions (403)"
        return f"Kubernetes API error {e.status}: {(e.reason or '')[:120]}"
    low = msg.lower()
    if "certificate has expired" in low:
        return "certificate expired: the kubeconfig certificate is no longer valid"
    if "certificate verify failed" in low:
        return f"TLS verification failed: {msg[:160]}"
    if any(s in low for s in ("newconnectionerror", "max retries exceeded",
                              "name or service not known", "nodename nor servname",
                              "connection refused", "connecttimeout")):
        return f"network unreachable: {msg[:160]}"
    return f"connection failed: {msg[:200]}"


class K8sSession:
    """One kubeconfig's API session; cleans up its temp file on close."""

    def __init__(self, kubeconfig_yaml: str):
        fd, self._path = tempfile.mkstemp(prefix="drydock-kubeconfig-")
        with os.fdopen(fd, "w") as f:
            f.write(kubeconfig_yaml.strip() + "\n")
        os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
        cfg = k8s.Configuration()
        load_kube_config(client_configuration=cfg, config_file=self._path)
        # be explicit: never let a proxy swallow cluster traffic by accident
        self.api = k8s.ApiClient(cfg)
        self.core = k8s.CoreV1Api(self.api)
        self.apps = k8s.AppsV1Api(self.api)
        self.version_api = k8s.VersionApi(self.api)

    def close(self):
        try:
            self.api.close()
        except Exception:
            pass
        try:
            os.unlink(self._path)
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_connection(kubeconfig_yaml: str) -> dict:
    """Hit the API once (list nodes). Returns ok/server/version or error."""
    info = parse_kubeconfig(kubeconfig_yaml)
    try:
        with K8sSession(kubeconfig_yaml) as s:
            version = s.version_api.get_code(
                _request_timeout=REQUEST_TIMEOUT).git_version
            s.core.list_node(limit=1, _request_timeout=REQUEST_TIMEOUT)
        return {"ok": True, "server": info["server"],
                "k8s_version": version,
                "detail": f"reached {info['server']} ({version})"}
    except Exception as e:
        log.warning("k8s connection test failed: %s", e)
        return {"ok": False, "server": info["server"],
                "error": categorize_error(e)}


def parse_node(n) -> dict:
    labels = n.metadata.labels or {}
    roles = sorted(k.split("/", 1)[1] for k in labels
                   if k.startswith("node-role.kubernetes.io/"))
    ready = any(c.type == "Ready" and c.status == "True"
                for c in (n.status.conditions or []))
    info = n.status.node_info
    cap = n.status.capacity or {}
    return {
        "name": n.metadata.name,
        "roles": roles or ["worker"],
        "ready": ready,
        "unschedulable": bool(n.spec and n.spec.unschedulable),
        "kubelet_version": getattr(info, "kubelet_version", ""),
        "cri": getattr(info, "container_runtime_version", ""),
        "os_image": getattr(info, "os_image", ""),
        "arch": getattr(info, "architecture", ""),
        "cpu": cap.get("cpu", ""),
        "memory": cap.get("memory", ""),
    }


def list_nodes(kubeconfig_yaml: str) -> list[dict]:
    with K8sSession(kubeconfig_yaml) as s:
        return [parse_node(n) for n in
                s.core.list_node(_request_timeout=REQUEST_TIMEOUT).items]


def _images(pod_spec) -> list[str]:
    containers = (pod_spec.containers or []) if pod_spec else []
    return [c.image for c in containers if c.image]


def _workload_status(kind: str, desired: int, ready: int, updated: int) -> str:
    if ready < desired:
        return "degraded"
    if updated < desired:
        return "progressing"
    return "ready"


def parse_workload(kind: str, w) -> dict:
    """One row for the workload table (4.5)."""
    spec = w.spec
    status = w.status
    if kind == "DaemonSet":
        desired = status.desired_number_scheduled or 0
        ready = status.number_available or 0
        updated = status.updated_number_scheduled or 0
    else:
        desired = spec.replicas or 0
        ready = status.ready_replicas or 0
        updated = status.updated_replicas or 0
    created = w.metadata.creation_timestamp
    return {
        "kind": kind,
        "namespace": w.metadata.namespace,
        "name": w.metadata.name,
        "desired": desired,
        "ready": ready,
        "updated": updated,
        "status": _workload_status(kind, desired, ready, updated),
        "images": _images(spec.template.spec if spec.template else None),
        "created_at": created.timestamp() if created else None,
    }


def list_workloads(kubeconfig_yaml: str) -> list[dict]:
    """Deployments, StatefulSets, DaemonSets across all namespaces (4.5)."""
    with K8sSession(kubeconfig_yaml) as s:
        out = []
        for d in s.apps.list_deployment_for_all_namespaces(
                _request_timeout=REQUEST_TIMEOUT).items:
            out.append(parse_workload("Deployment", d))
        for st in s.apps.list_stateful_set_for_all_namespaces(
                _request_timeout=REQUEST_TIMEOUT).items:
            out.append(parse_workload("StatefulSet", st))
        for ds in s.apps.list_daemon_set_for_all_namespaces(
                _request_timeout=REQUEST_TIMEOUT).items:
            out.append(parse_workload("DaemonSet", ds))
    out.sort(key=lambda w: (w["namespace"], w["kind"], w["name"]))
    return out


def _event_time(e):
    return e.last_timestamp or e.event_time or e.first_timestamp


def parse_event(e) -> dict:
    t = _event_time(e)
    return {
        "type": e.type or "Normal",
        "reason": e.reason or "",
        "kind": e.involved_object.kind if e.involved_object else "",
        "name": e.involved_object.name if e.involved_object else "",
        "namespace": e.involved_object.namespace if e.involved_object else "",
        "message": (e.message or "")[:300],
        "count": e.count or 0,
        "last_seen": t.timestamp() if t else None,
    }


def list_events(kubeconfig_yaml: str, limit: int = 50) -> list[dict]:
    """Most recent cluster events, warnings first (4.6)."""
    with K8sSession(kubeconfig_yaml) as s:
        items = s.core.list_event_for_all_namespaces(
            _request_timeout=REQUEST_TIMEOUT).items
    parsed = [parse_event(e) for e in items]
    parsed.sort(key=lambda e: (
        0 if e["type"] == "Warning" else 1,
        -(e["last_seen"] or 0),
    ))
    return parsed[:limit]


def get_overview(kubeconfig_yaml: str) -> dict:
    """One screen answering 'is this cluster healthy right now' (4.2)."""
    with K8sSession(kubeconfig_yaml) as s:
        version = s.version_api.get_code(
            _request_timeout=REQUEST_TIMEOUT).git_version
        nodes = [parse_node(n) for n in
                 s.core.list_node(_request_timeout=REQUEST_TIMEOUT).items]
        pods = s.core.list_pod_for_all_namespaces(
            watch=False, _request_timeout=REQUEST_TIMEOUT).items
    by_phase: dict[str, int] = {}
    abnormal = []
    for p in pods:
        phase = p.status.phase or "Unknown"
        by_phase[phase] = by_phase.get(phase, 0) + 1
        wait_reasons = {c.state.waiting.reason for c in
                        (p.status.container_statuses or [])
                        if c.state and c.state.waiting and c.state.waiting.reason}
        bad = wait_reasons & ABNORMAL_WAIT
        if phase not in ("Running", "Succeeded") or bad:
            abnormal.append({
                "namespace": p.metadata.namespace,
                "name": p.metadata.name,
                "phase": phase,
                "reason": ",".join(sorted(bad)),
                "restarts": sum(c.restart_count for c in
                                (p.status.container_statuses or [])),
            })
    abnormal.sort(key=lambda x: -x["restarts"])
    ready = sum(1 for n in nodes if n["ready"])
    return {
        "version": version,
        "nodes_total": len(nodes),
        "nodes_ready": ready,
        "pods_total": len(pods),
        "pods_by_phase": by_phase,
        "abnormal_pods": abnormal[:5],
    }
