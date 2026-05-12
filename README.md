# K8s AI Analyzer

> **Hackathon Project — Theme 2: AI Agents for Real-time Pod Resource Discovery and Dependency Mapping**

Real-time, AI-driven Kubernetes pod resource discovery, dependency mapping, and anomaly detection for single-node clusters. Powered by Groq API (llama3-70b) with a multi-agent analysis pipeline and a live Streamlit dashboard.

---

## 🎯 Program Overview

This project is a container-based automation solution designed to rethink how containerized systems are understood, not just monitored. As applications scale, even single-node deployments often host hundreds of pods across multiple namespaces, each with diverse resource patterns including CPU, memory, disk, network usage, and PVC operations.

### Problem Statement

While platforms like Kubernetes provide raw metrics, correlating resource behavior is extremely challenging, especially when dealing with:
- Bursty workloads
- Large file I/O
- PVC-based storage stress
- Multi-service dependency behavior
- Sudden anomalies or leaks

Engineers struggle to answer foundational operational questions such as:
- *Which pod is causing unexpected CPU spikes?*
- *How are PVC I/O patterns linked to pod restarts?*
- *Are different services influencing each other’s resource consumption?*
- *Which workloads need optimization?*

Currently, there is no unified tool providing real-time, AI-driven correlation across all these resource types in single-node clusters commonly used in edge and industrial environments.

### Stakeholders

- **Engineers** managing containerized environments
- **Platform/system operators** working with Kubernetes and similar tools
- **Organizations** relying on these systems for performance and reliability
- **Communities** adopting this system to improve efficiency

### Desired Solution & Benefits

Our system collects, analyzes, and correlates real-time resource consumption of pods across all namespaces. Key capabilities include:
- **Real-time resource discovery** (CPU, RAM, disk usage, PVC metrics, network data)
- **Multi-agent AI analysis** across CPU, Memory, Storage/PVC, and Log/IO
- **Interdependency mapping** to identify relationships between pods
- **Intelligent recommendations** for optimization, alerts, and forecasting
- **Rich real-time dashboard** with graphs, correlations, anomaly timelines, and NLP insights

**Impact & Benefits:**
- Provides real-time visibility into pod-level resource behavior, preventing performance degradation and downtime.
- Improves reliability through AI-driven anomaly detection, bottleneck identification, and dependency understanding.

---

## Features

- **Real-time resource discovery** — CPU, memory, PVC status, events, pod phases across all namespaces
- **3-agent AI pipeline** — Resource Profiler → Dependency Mapper → Insights Synthesizer (all via Groq API)
- **Dependency mapping** — infers pod relationships via shared PVCs, label selectors, namespace co-location
- **Anomaly detection** — flags bursty workloads, no-limit pods, OOMKilled events, crashloops
- **Interactive dashboard** — dark glassmorphism UI with pod table, time-series charts, dependency graph, event feed, and AI insights tabs
- **No heavy AI frameworks** — direct Groq API calls; fast and lightweight

---

## Quick Start (Ubuntu VM)

```bash
# 1. Install MicroK8s
sudo snap install microk8s --classic --channel=1.30/stable
microk8s enable dns metrics-server hostpath-storage

# 2. Deploy workloads
kubectl apply -f k8s/namespaces.yaml
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/sample-workloads.yaml

# 3. Configure
cp .env.example .env
nano .env   # add your GROQ_API_KEY

# 4. Run backend
cd backend && python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt && python main.py

# 5. Run frontend (new terminal)
cd frontend && python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt && streamlit run app.py
```

Open `http://localhost:8501` in your browser.

---

## Project Structure

```
k8s-ai-analyzer/
├── backend/
│   ├── main.py           # FastAPI REST server
│   ├── k8s_client.py     # Kubernetes API wrapper + polling
│   ├── ai_agents.py      # Multi-agent Groq AI engine
│   └── requirements.txt
├── frontend/
│   ├── app.py            # Streamlit dashboard
│   └── requirements.txt
├── k8s/
│   ├── namespaces.yaml   # 5 namespace definitions
│   ├── rbac.yaml         # Read-only ClusterRole + SA
│   └── sample-workloads.yaml  # 15+ demo pods across namespaces
├── docs/
│   ├── setup_guide.md    # Full VM setup walkthrough
│   └── architecture.md   # Architecture diagrams + tech stack
└── .env.example          # Environment variable template
```

---

## Namespaces & Workloads

| Namespace        | Pods                                                        | Notes                         |
|------------------|-------------------------------------------------------------|-------------------------------|
| `production`     | web-frontend, api-server, redis-cache, auth-service         | auth-service has no limits ⚠  |
| `monitoring`     | prometheus, grafana, alertmanager                           | prometheus mounts a PVC       |
| `data-processing`| spark-worker (×2), kafka-consumer, etl-job                  | spark+kafka share a PVC       |
| `storage`        | postgres-db, minio                                          | heavy PVC usage               |
| `dev`            | test-runner, debug-crashloop, load-simulator                | crashloop pod is intentional  |

---

## Documentation

- 📖 [Setup Guide](docs/setup_guide.md) — Step-by-step VM setup and run instructions
- 🏗️ [Architecture](docs/architecture.md) — Full architecture diagrams and component explanations

---

## Tech Stack

| Layer          | Technology                     |
|----------------|--------------------------------|
| Orchestration  | MicroK8s (Kubernetes 1.30)     |
| Backend        | FastAPI + Uvicorn              |
| K8s Client     | `kubernetes` Python SDK        |
| AI Engine      | Groq API — llama3-70b-8192     |
| Frontend       | Streamlit + Plotly + NetworkX  |
| Storage        | MicroK8s hostpath-storage      |
