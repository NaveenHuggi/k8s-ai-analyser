# K8s AI Analyzer — Architecture Document

## System Overview

The K8s AI Analyzer is a three-layer system that continuously monitors a
single-node Kubernetes cluster and applies a multi-agent AI pipeline to
produce real-time insights, anomaly alerts, and dependency maps.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Ubuntu VM (MicroK8s Cluster)                         │
│                                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  ┌──────────────┐  │
│  │ NAMESPACE    │  │ NAMESPACE    │  │ NAMESPACE     │  │ NAMESPACE    │  │
│  │ production   │  │ monitoring   │  │data-processing│  │   storage    │  │
│  │              │  │              │  │               │  │              │  │
│  │ web-frontend │  │ prometheus   │  │ spark-worker  │  │ postgres-db  │  │
│  │ api-server   │  │ grafana      │  │ kafka-consumer│  │ minio        │  │
│  │ redis-cache  │  │ alertmanager │  │ etl-job       │  │              │  │
│  │ auth-service │  │              │  │               │  │  [PVCs]      │  │
│  │ [api-logs-   │  │ [prometheus- │  │ [spark-output-│  │  postgres-   │  │
│  │  pvc]        │  │  data-pvc]   │  │  pvc]         │  │  data-pvc    │  │
│  └──────┬───────┘  └──────┬───────┘  └───────┬───────┘  └──────┬───────┘  │
│         │                 │                  │                  │          │
│         └─────────────────┴──────────────────┴──────────────────┘          │
│                                      │                                      │
│                         ┌────────────▼────────────┐                        │
│                         │  Kubernetes API Server   │                        │
│                         │  + Metrics Server        │                        │
│                         └────────────┬────────────┘                        │
│                                      │                                      │
└──────────────────────────────────────┼──────────────────────────────────────┘
                                       │ (K8s Python client)
                    ┌──────────────────▼─────────────────────┐
                    │          LAYER 1: Data Collection        │
                    │          backend/k8s_client.py           │
                    │                                          │
                    │  • Polls every 15 seconds                │
                    │  • Fetches: pods, PVCs, events, metrics  │
                    │  • Rolling in-memory history (60 points) │
                    │  • Thread-safe cache for API access      │
                    └──────────────────┬─────────────────────┘
                                       │
                    ┌──────────────────▼─────────────────────┐
                    │       LAYER 2: FastAPI REST Server       │
                    │           backend/main.py                │
                    │                                          │
                    │  GET  /api/pods          (live data)     │
                    │  GET  /api/pvcs          (PVC states)    │
                    │  GET  /api/events        (warnings)      │
                    │  GET  /api/metrics       (usage map)     │
                    │  GET  /api/history       (per-pod ts)    │
                    │  GET  /api/nodes         (capacity)      │
                    │  POST /api/analyze       (trigger AI)    │
                    │  GET  /api/last-analysis (AI result)     │
                    └──────────────────┬─────────────────────┘
                                       │
                    ┌──────────────────▼─────────────────────┐
                    │       LAYER 3: Multi-Agent AI Engine     │
                    │          backend/ai_agents.py            │
                    │                                          │
                    │  ┌──────────────────────────────────┐   │
                    │  │  Agent 1: Resource Profiler       │   │
                    │  │  • Detects CPU/RAM anomalies      │   │
                    │  │  • Flags pods without limits      │   │
                    │  │  • Identifies crashloops & OOM    │   │
                    │  │  • Groq API → llama3-70b          │   │
                    │  └─────────────────┬────────────────┘   │
                    │                    │ report A            │
                    │  ┌──────────────────────────────────┐   │
                    │  │  Agent 2: Dependency Mapper       │   │
                    │  │  • Maps shared PVC relationships  │   │
                    │  │  • Infers label-based topology    │   │
                    │  │  • Correlates namespace restarts  │   │
                    │  │  • Groq API → llama3-70b          │   │
                    │  └─────────────────┬────────────────┘   │
                    │                    │ report B            │
                    │  ┌──────────────────────────────────┐   │
                    │  │  Agent 3: Insights Synthesizer    │   │
                    │  │  • Health score 0–100             │   │
                    │  │  • Top 5 priority actions         │   │
                    │  │  • Root cause hypotheses          │   │
                    │  │  • 30-min forecast                │   │
                    │  │  • NLP summary for stakeholders   │   │
                    │  │  • Groq API → llama3-70b          │   │
                    │  └─────────────────┬────────────────┘   │
                    └──────────────────┬─────────────────────┘
                                       │
                    ┌──────────────────▼─────────────────────┐
                    │     LAYER 4: Streamlit Dashboard         │
                    │         frontend/app.py                  │
                    │                                          │
                    │  ┌────────────────────────────────────┐ │
                    │  │ Sidebar: Namespace filter + AI btn  │ │
                    │  ├────────────────────────────────────┤ │
                    │  │ Overview: Node gauges + NS bar chart│ │
                    │  ├────────────────────────────────────┤ │
                    │  │ Pod Table: Color-coded (phase,      │ │
                    │  │ restarts, CPU, MEM)                 │ │
                    │  ├────────────────────────────────────┤ │
                    │  │ Time-series: CPU/MEM per pod        │ │
                    │  ├────────────────────────────────────┤ │
                    │  │ Dependency Graph: Interactive nodes │ │
                    │  ├────────────────────────────────────┤ │
                    │  │ Events: Warning timeline            │ │
                    │  ├────────────────────────────────────┤ │
                    │  │ AI Insights: Tabbed agent reports   │ │
                    │  └────────────────────────────────────┘ │
                    └────────────────────────────────────────┘
```

---

## Data Flow Diagram

```
K8s API Server ─── every 15s ──▶ k8s_client.py ──▶ In-memory cache
                                      │                    │
                                      │                    ├──▶ /api/pods
                                      │                    ├──▶ /api/pvcs
                                      │                    ├──▶ /api/events
                                      │                    ├──▶ /api/metrics
                                      │                    └──▶ /api/history
                                      │
                              every 60s (or on-demand)
                                      │
                                      ▼
                              ai_agents.py
                                │       │
                         Agent1 │ Agent2│
                                └───┬───┘
                                    │ (reports)
                                    ▼
                                Agent 3 (synthesizer)
                                    │
                                    ▼
                              /api/last-analysis
                                    │
                                    ▼
                          Streamlit Dashboard (polls every 15s)
```

---

## Multi-Agent AI Architecture

```
                    ┌────────────────────────────────────┐
                    │        K8s Cluster Snapshot          │
                    │  (pods + metrics + PVCs + events)    │
                    └───────────────┬────────────────────┘
                                    │
                    ┌───────────────▼────────────────────┐
                    │        Groq API (llama3-70b)         │
                    └───────────────┬────────────────────┘
                                    │
               ┌────────────────────┼─────────────────────┐
               │                    │                      │
    ┌──────────▼──────────┐ ┌───────▼─────────┐ ┌────────▼────────┐
    │  Resource Profiler  │ │Dependency Mapper│ │ Insights Synth. │
    │                     │ │                 │ │                 │
    │ System Prompt:      │ │ System Prompt:  │ │ System Prompt:  │
    │ "You are a K8s      │ │ "You are a      │ │ "You are the    │
    │  resource expert... │ │  dependency     │ │  chief insights │
    │  detect anomalies"  │ │  mapper...      │ │  AI... combine  │
    │                     │ │  infer PVC &    │ │  both reports"  │
    │ Output:             │ │  label links"   │ │                 │
    │ - Anomaly bullets   │ │                 │ │ Output:         │
    │ - Recommendations   │ │ Output:         │ │ - Health Score  │
    │                     │ │ - Dep. pairs    │ │ - Top 5 Actions │
    │                     │ │ - Risk summary  │ │ - RCA           │
    └─────────────────────┘ └─────────────────┘ │ - Forecast      │
               │                    │            │ - NLP summary   │
               └────────────────────┘            └─────────────────┘
                       reports A + B                      │
                            └─────────────────────────────┘
                                    Unified Report
```

---

## Dependency Mapping Logic

The Dependency Mapper agent infers relationships using these signals:

| Signal Type       | Evidence                                    | Relationship Inferred     |
|-------------------|---------------------------------------------|---------------------------|
| Shared PVC        | Two pods mount the same `claimName`         | Storage dependency        |
| Label selectors   | `tier=frontend` → `tier=backend` naming     | Client-server dependency  |
| Namespace co-loc. | Pods in the same namespace                  | Service group             |
| Restart cascade   | Multiple pods in same NS restarting         | Shared downstream failure |
| Naming convention | `-db`, `-cache`, `-api`, `-worker` suffixes | Service tier inference    |

---

## Technology Stack

| Component         | Technology                       | Why                                          |
|-------------------|----------------------------------|----------------------------------------------|
| Orchestration     | MicroK8s (Kubernetes 1.30)       | Lightweight single-node, all K8s features    |
| Backend API       | FastAPI + Uvicorn                | Async, fast, auto-generates OpenAPI docs     |
| K8s Client        | `kubernetes` Python SDK          | Official, well-maintained                    |
| AI Engine         | Groq API (llama3-70b-8192)       | Ultra-fast inference, no local GPU needed    |
| Frontend          | Streamlit + Plotly               | Rapid data dashboard development in Python   |
| Dependency Graph  | NetworkX + Plotly                | Graph construction + interactive rendering   |
| PVC Storage       | MicroK8s hostpath-storage        | Simple provisioner for single-node demo      |

---

## Namespace → Workload Map

```
production/
├── web-frontend     (nginx, 2 replicas)   → stable baseline load
├── api-server       (python, 2 replicas)  → mounts api-logs-pvc
├── redis-cache      (redis)               → downstream of api-server
└── auth-service     (python, NO limits)   → ⚠ intentional anomaly

monitoring/
├── prometheus       (prom/prometheus)     → mounts prometheus-data-pvc
├── grafana          (grafana/grafana)     → depends on prometheus
└── alertmanager     (prom/alertmanager)   → depends on prometheus

data-processing/
├── spark-worker     (python, 2 replicas)  → bursty CPU; mounts spark-output-pvc
├── kafka-consumer   (python)              → mounts spark-output-pvc (shared!)
└── etl-job          (python)              → growing memory pattern

storage/
├── postgres-db      (postgres)            → mounts postgres-data-pvc (20Gi)
└── minio            (minio/minio)         → mounts minio-data-pvc (30Gi)

dev/
├── test-runner      (python)              → intermittent load
├── debug-crashloop  (busybox)             → ❌ intentional crashloop (restarts++)
└── load-simulator   (python)              → random bursty CPU for demo
```

---

## Security Notes

- The backend uses a **read-only** ClusterRole — it cannot modify or delete cluster resources.
- The Groq API key is stored in a `.env` file — never commit this to Git.
- All API endpoints are currently open (no auth) — acceptable for a hackathon demo;
  add JWT/API key auth before any production deployment.

---

## Scalability Notes

This system is designed for **single-node clusters** (Minikube, MicroK8s, K3s).
For multi-node clusters:
- The `k8s_client.py` already handles multi-namespace data — no changes needed for the data layer.
- The AI analysis payload may grow large; consider chunking by namespace for large clusters.
- The Streamlit dashboard auto-refreshes every 15 seconds — suitable for demo; for production, switch to WebSocket push.
