"""
k8s_client.py
=============
Kubernetes API wrapper for the K8s AI Analyzer.

Responsibilities:
  - Connect to the cluster (in-cluster or via kubeconfig)
  - Fetch live pod info, resource requests/limits, container statuses
  - Fetch real-time CPU/Memory usage from the Metrics API
  - Fetch PersistentVolumeClaim (PVC) states per namespace
  - Fetch cluster events (OOMKilled, CrashLoopBackOff, etc.)
  - Fetch Service/Deployment relationships for dependency inference
  - Maintain a rolling in-memory history of metrics (last N snapshots)
"""

from __future__ import annotations

import os
import time
import logging
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from kubernetes import client, config
from kubernetes.client.rest import ApiException

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WATCHED_NAMESPACES_ENV = os.getenv("WATCHED_NAMESPACES", "*")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "15"))
HISTORY_MAX_LEN = 60          # keep last 60 snapshots (~15 min at 15s interval)


# ---------------------------------------------------------------------------
# Helper: parse Kubernetes resource quantity strings  e.g. "250m", "512Mi"
# ---------------------------------------------------------------------------
def parse_cpu_to_millicores(value: str) -> float:
    """Convert CPU quantity string to millicores (int)."""
    if not value:
        return 0.0
    value = str(value).strip()
    if value.endswith("n"):       # nanocores
        return float(value[:-1]) / 1_000_000
    if value.endswith("u"):       # microcores
        return float(value[:-1]) / 1_000
    if value.endswith("m"):       # millicores
        return float(value[:-1])
    return float(value) * 1000    # whole cores → millicores


def parse_memory_to_mib(value: str) -> float:
    """Convert memory quantity string to MiB."""
    if not value:
        return 0.0
    value = str(value).strip()
    suffixes = {
        "Ki": 1 / 1024,
        "Mi": 1.0,
        "Gi": 1024.0,
        "Ti": 1024 ** 2,
        "K":  1 / 1024,
        "M":  1.0,
        "G":  1024.0,
    }
    for suffix, factor in suffixes.items():
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) * factor
    return float(value) / (1024 ** 2)   # raw bytes


# ---------------------------------------------------------------------------
# K8sClient
# ---------------------------------------------------------------------------
class K8sClient:
    """Thread-safe Kubernetes data collector with in-memory rolling history."""

    def __init__(self) -> None:
        self._load_config()
        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.custom = client.CustomObjectsApi()

        # Resolved list of namespaces to watch
        self.namespaces: List[str] = self._resolve_namespaces()

        # Rolling history: namespace -> pod_name -> deque of metric dicts
        self.metric_history: Dict[str, Dict[str, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=HISTORY_MAX_LEN))
        )

        # Latest snapshot cache (refreshed by background thread)
        self._cache_lock = threading.Lock()
        self._latest_pods: List[Dict] = []
        self._latest_pvcs: List[Dict] = []
        self._latest_events: List[Dict] = []
        self._latest_metrics: Dict[str, Dict] = {}   # ns/pod -> metrics

        # Start background polling thread
        self._stop_event = threading.Event()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True
        )
        self._poll_thread.start()
        logger.info("K8sClient initialised — watching namespaces: %s", self.namespaces)

    # ------------------------------------------------------------------ #
    # Configuration loading
    # ------------------------------------------------------------------ #
    def _load_config(self) -> None:
        """Load kubeconfig or use in-cluster service-account."""
        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes config.")
        except config.ConfigException:
            kubeconfig = os.getenv("KUBECONFIG", os.path.expanduser("~/.kube/config"))
            config.load_kube_config(config_file=kubeconfig)
            logger.info("Loaded kubeconfig from: %s", kubeconfig)

    def _resolve_namespaces(self) -> List[str]:
        """Return the list of namespaces to watch."""
        raw = WATCHED_NAMESPACES_ENV.strip()
        if raw == "*":
            try:
                ns_list = self.core_v1.list_namespace()
                return [ns.metadata.name for ns in ns_list.items]
            except ApiException as exc:
                logger.warning("Could not list all namespaces: %s", exc)
                return ["default"]
        return [ns.strip() for ns in raw.split(",") if ns.strip()]

    # ------------------------------------------------------------------ #
    # Background polling
    # ------------------------------------------------------------------ #
    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                pods    = self._fetch_all_pods()
                pvcs    = self._fetch_all_pvcs()
                events  = self._fetch_all_events()
                metrics = self._fetch_metrics_api()

                # Update history
                for pod in pods:
                    ns  = pod["namespace"]
                    nm  = pod["name"]
                    key = f"{ns}/{nm}"
                    if key in metrics:
                        entry = {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            **metrics[key],
                        }
                        self.metric_history[ns][nm].append(entry)

                with self._cache_lock:
                    self._latest_pods    = pods
                    self._latest_pvcs    = pvcs
                    self._latest_events  = events
                    self._latest_metrics = metrics

            except Exception as exc:  # noqa: BLE001
                logger.error("Poll loop error: %s", exc, exc_info=True)

            self._stop_event.wait(POLL_INTERVAL)

    # ------------------------------------------------------------------ #
    # Raw fetchers
    # ------------------------------------------------------------------ #
    def _fetch_all_pods(self) -> List[Dict]:
        """Return a flat list of pod dicts across all watched namespaces."""
        results: List[Dict] = []
        for ns in self.namespaces:
            try:
                pod_list = self.core_v1.list_namespaced_pod(namespace=ns)
                for pod in pod_list.items:
                    containers = []
                    for c in (pod.spec.containers or []):
                        res = c.resources or client.V1ResourceRequirements()
                        containers.append({
                            "name":         c.name,
                            "image":        c.image,
                            "cpu_request":  parse_cpu_to_millicores(
                                (res.requests or {}).get("cpu", "0")),
                            "mem_request":  parse_memory_to_mib(
                                (res.requests or {}).get("memory", "0")),
                            "cpu_limit":    parse_cpu_to_millicores(
                                (res.limits or {}).get("cpu", "0")),
                            "mem_limit":    parse_memory_to_mib(
                                (res.limits or {}).get("memory", "0")),
                        })

                    # Volume claims attached to this pod
                    pvcs_attached = [
                        v.persistent_volume_claim.claim_name
                        for v in (pod.spec.volumes or [])
                        if v.persistent_volume_claim
                    ]

                    results.append({
                        "name":         pod.metadata.name,
                        "namespace":    pod.metadata.namespace,
                        "phase":        pod.status.phase,
                        "node":         pod.spec.node_name,
                        "labels":       pod.metadata.labels or {},
                        "containers":   containers,
                        "pvcs":         pvcs_attached,
                        "restart_count": sum(
                            (cs.restart_count or 0)
                            for cs in (pod.status.container_statuses or [])
                        ),
                        "conditions":   [
                            {"type": c.type, "status": c.status}
                            for c in (pod.status.conditions or [])
                        ],
                    })
            except ApiException as exc:
                logger.warning("Error listing pods in %s: %s", ns, exc)
        return results

    def _fetch_all_pvcs(self) -> List[Dict]:
        """Return PVC info across all watched namespaces."""
        results: List[Dict] = []
        for ns in self.namespaces:
            try:
                pvc_list = self.core_v1.list_namespaced_persistent_volume_claim(namespace=ns)
                for pvc in pvc_list.items:
                    results.append({
                        "name":           pvc.metadata.name,
                        "namespace":      pvc.metadata.namespace,
                        "phase":          pvc.status.phase,
                        "capacity":       (pvc.status.capacity or {}).get("storage", "unknown"),
                        "access_modes":   pvc.status.access_modes or [],
                        "storage_class":  pvc.spec.storage_class_name,
                        "volume_name":    pvc.spec.volume_name,
                    })
            except ApiException as exc:
                logger.warning("Error listing PVCs in %s: %s", ns, exc)
        return results

    def _fetch_all_events(self) -> List[Dict]:
        """Return recent Warning-level events across watched namespaces."""
        results: List[Dict] = []
        for ns in self.namespaces:
            try:
                event_list = self.core_v1.list_namespaced_event(
                    namespace=ns, field_selector="type=Warning"
                )
                for ev in event_list.items:
                    results.append({
                        "namespace":  ev.metadata.namespace,
                        "name":       ev.metadata.name,
                        "reason":     ev.reason,
                        "message":    ev.message,
                        "object":     f"{ev.involved_object.kind}/{ev.involved_object.name}",
                        "count":      ev.count,
                        "first_time": str(ev.first_timestamp),
                        "last_time":  str(ev.last_timestamp),
                    })
            except ApiException as exc:
                logger.warning("Error listing events in %s: %s", ns, exc)
        return results

    def _fetch_metrics_api(self) -> Dict[str, Dict]:
        """
        Fetch live CPU/Memory from the Kubernetes Metrics API.
        Returns dict keyed by 'namespace/pod_name'.
        Falls back gracefully if metrics-server is not installed.
        """
        result: Dict[str, Dict] = {}
        for ns in self.namespaces:
            try:
                data = self.custom.list_namespaced_custom_object(
                    group="metrics.k8s.io",
                    version="v1beta1",
                    namespace=ns,
                    plural="pods",
                )
                for item in data.get("items", []):
                    pod_name = item["metadata"]["name"]
                    total_cpu = 0.0
                    total_mem = 0.0
                    for c in item.get("containers", []):
                        usage = c.get("usage", {})
                        total_cpu += parse_cpu_to_millicores(usage.get("cpu", "0"))
                        total_mem += parse_memory_to_mib(usage.get("memory", "0"))
                    result[f"{ns}/{pod_name}"] = {
                        "cpu_usage_millicores": round(total_cpu, 2),
                        "mem_usage_mib":        round(total_mem, 2),
                    }
            except ApiException as exc:
                if exc.status == 404:
                    logger.warning(
                        "metrics.k8s.io API not available — is metrics-server installed?"
                    )
                else:
                    logger.warning("Metrics API error for %s: %s", ns, exc)
        return result

    # ------------------------------------------------------------------ #
    # Public read API (thread-safe snapshots)
    # ------------------------------------------------------------------ #
    def get_pods(self) -> List[Dict]:
        with self._cache_lock:
            return list(self._latest_pods)

    def get_pvcs(self) -> List[Dict]:
        with self._cache_lock:
            return list(self._latest_pvcs)

    def get_events(self) -> List[Dict]:
        with self._cache_lock:
            return list(self._latest_events)

    def get_metrics(self) -> Dict[str, Dict]:
        with self._cache_lock:
            return dict(self._latest_metrics)

    def get_metric_history(self, namespace: str, pod_name: str) -> List[Dict]:
        return list(self.metric_history[namespace][pod_name])

    def get_enriched_pods(self) -> List[Dict]:
        """
        Return pods enriched with their live metric usage.
        This is the primary data feed for AI agents and the dashboard.
        """
        pods    = self.get_pods()
        metrics = self.get_metrics()
        for pod in pods:
            key     = f"{pod['namespace']}/{pod['name']}"
            m       = metrics.get(key, {})
            pod["cpu_usage_millicores"] = m.get("cpu_usage_millicores", None)
            pod["mem_usage_mib"]        = m.get("mem_usage_mib", None)
        return pods

    def get_node_info(self) -> List[Dict]:
        """Return basic node capacity info."""
        nodes = []
        try:
            node_list = self.core_v1.list_node()
            for n in node_list.items:
                cap = n.status.capacity or {}
                alloc = n.status.allocatable or {}
                nodes.append({
                    "name":             n.metadata.name,
                    "cpu_capacity":     parse_cpu_to_millicores(cap.get("cpu", "0")),
                    "mem_capacity_mib": parse_memory_to_mib(cap.get("memory", "0")),
                    "cpu_allocatable":  parse_cpu_to_millicores(alloc.get("cpu", "0")),
                    "mem_allocatable_mib": parse_memory_to_mib(alloc.get("memory", "0")),
                    "conditions":       [
                        {"type": c.type, "status": c.status}
                        for c in (n.status.conditions or [])
                    ],
                })
        except ApiException as exc:
            logger.warning("Error listing nodes: %s", exc)
        return nodes

    def stop(self) -> None:
        """Stop the background polling thread."""
        self._stop_event.set()
        self._poll_thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Module-level singleton (created once on first import)
# ---------------------------------------------------------------------------
_client_instance: Optional[K8sClient] = None


def get_k8s_client() -> K8sClient:
    global _client_instance
    if _client_instance is None:
        _client_instance = K8sClient()
    return _client_instance
