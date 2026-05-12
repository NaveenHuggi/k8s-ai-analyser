# K8s AI Analyzer — VM Setup & Run Guide

> **Target environment:** Ubuntu 22.04 LTS VM (minimum 4 vCPUs, 8 GB RAM, 40 GB disk)
> **Assumes:** Fresh install, no existing Kubernetes or Python setup.

---

## Table of Contents
1. [VM Prerequisites](#1-vm-prerequisites)
2. [Install MicroK8s](#2-install-microk8s)
3. [Enable Required Add-ons](#3-enable-required-add-ons)
4. [Clone / Copy the Project](#4-clone--copy-the-project)
5. [Configure Environment Variables](#5-configure-environment-variables)
6. [Deploy Sample Workloads](#6-deploy-sample-workloads)
7. [Install Python Dependencies](#7-install-python-dependencies)
8. [Run the Backend (FastAPI)](#8-run-the-backend-fastapi)
9. [Run the Frontend (Streamlit)](#9-run-the-frontend-streamlit)
10. [Access the Dashboard](#10-access-the-dashboard)
11. [Verifying AI Analysis](#11-verifying-ai-analysis)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. VM Prerequisites

```bash
# Update the system
sudo apt-get update && sudo apt-get upgrade -y

# Install essential tools
sudo apt-get install -y \
    curl \
    wget \
    git \
    python3 \
    python3-pip \
    python3-venv \
    snapd \
    net-tools

# Verify Python
python3 --version   # Should be 3.10 or 3.11
```

---

## 2. Install MicroK8s

We use **MicroK8s** — a lightweight single-node Kubernetes that works perfectly
on a VM with minimal overhead.

```bash
# Install MicroK8s
sudo snap install microk8s --classic --channel=1.30/stable

# Add your user to the microk8s group (avoids using sudo every time)
sudo usermod -aG microk8s $USER
sudo chown -f -R $USER ~/.kube

# Reload your session for group changes to take effect
newgrp microk8s

# Wait until MicroK8s is ready
microk8s status --wait-ready

# Create a kubectl alias for convenience
echo "alias kubectl='microk8s kubectl'" >> ~/.bashrc
source ~/.bashrc

# Verify the node is up
kubectl get nodes
# Expected output:
# NAME     STATUS   ROLES    AGE   VERSION
# <vm>     Ready    <none>   1m    v1.30.x
```

---

## 3. Enable Required Add-ons

```bash
# Enable DNS (required for inter-pod communication)
microk8s enable dns

# Enable the Metrics Server (REQUIRED for CPU/Memory usage data)
microk8s enable metrics-server

# Enable storage (provides a default StorageClass for PVCs)
microk8s enable hostpath-storage

# Verify all add-ons are active
microk8s status

# Verify the metrics server is working (may take ~60 seconds to start)
kubectl top nodes
kubectl top pods -A
```

> **Note:** If `kubectl top pods` shows "Error from server (ServiceUnavailable)",
> wait 60–90 seconds and try again. The metrics-server needs time to collect data.

---

## 4. Clone / Copy the Project

```bash
# Option A: If you have the project on a USB / shared folder
cp -r /path/to/k8s-ai-analyzer ~/k8s-ai-analyzer

# Option B: If you pushed to Git
git clone https://github.com/<your-username>/k8s-ai-analyzer.git ~/k8s-ai-analyzer

# Navigate to the project root
cd ~/k8s-ai-analyzer
ls
# Should show: backend/ frontend/ k8s/ docs/ .env.example README.md
```

---

## 5. Configure Environment Variables

```bash
# Copy the example file
cp .env.example .env

# Edit the .env file
nano .env
```

Set your real Groq API key. The file should look like:

```env
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
GROQ_MODEL=llama3-70b-8192
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
POLL_INTERVAL_SECONDS=15
AI_ANALYSIS_INTERVAL_SECONDS=60
KUBECONFIG=/root/.kube/config
WATCHED_NAMESPACES=production,monitoring,data-processing,storage,dev
BACKEND_URL=http://localhost:8000
```

**Exporting MicroK8s kubeconfig:**

```bash
# Export MicroK8s config to the standard location
mkdir -p ~/.kube
microk8s config > ~/.kube/config
chmod 600 ~/.kube/config

# Test it
kubectl cluster-info
```

---

## 6. Deploy Sample Workloads

```bash
cd ~/k8s-ai-analyzer

# Step 1: Create the namespaces
kubectl apply -f k8s/namespaces.yaml

# Step 2: Apply RBAC (read-only cluster access for the backend)
kubectl apply -f k8s/rbac.yaml

# Step 3: Deploy all sample pods
kubectl apply -f k8s/sample-workloads.yaml

# Step 4: Wait for pods to start (may take 2–3 minutes for image pulls)
kubectl get pods -A --watch
# Press Ctrl+C when most pods show "Running"

# Step 5: Verify pods per namespace
kubectl get pods -n production
kubectl get pods -n monitoring
kubectl get pods -n data-processing
kubectl get pods -n storage
kubectl get pods -n dev

# Step 6: Check PVCs are bound
kubectl get pvc -A
# All PVCs should show "Bound" status
```

Expected pod counts:
| Namespace        | Pods                                                    |
|------------------|---------------------------------------------------------|
| production       | web-frontend (×2), api-server (×2), redis-cache, auth-service |
| monitoring       | prometheus, grafana, alertmanager                       |
| data-processing  | spark-worker (×2), kafka-consumer, etl-job              |
| storage          | postgres-db, minio                                      |
| dev              | test-runner, debug-crashloop (will keep restarting — intentional!), load-simulator |

---

## 7. Install Python Dependencies

We use separate virtual environments for the backend and frontend
to keep dependencies clean.

```bash
# --- Backend ---
cd ~/k8s-ai-analyzer/backend
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

# --- Frontend ---
cd ~/k8s-ai-analyzer/frontend
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
```

---

## 8. Run the Backend (FastAPI)

```bash
cd ~/k8s-ai-analyzer/backend
source venv/bin/activate

# Copy the shared .env (or symlink it)
cp ../.env .env

# Start the server
python main.py
```

You should see log output like:
```
INFO: K8s client initialised — watching namespaces: ['production', 'monitoring', ...]
INFO: Background AI analysis loop started (interval=60s).
INFO: Uvicorn running on http://0.0.0.0:8000
```

**Test the backend is working (open a second terminal):**

```bash
curl http://localhost:8000/health
# {"status":"ok","service":"k8s-ai-analyzer"}

curl http://localhost:8000/api/pods | python3 -m json.tool | head -30
# Should show a list of pod objects from all namespaces
```

---

## 9. Run the Frontend (Streamlit)

Open a **new terminal tab/window** (keep the backend running in the first one).

```bash
cd ~/k8s-ai-analyzer/frontend
source venv/bin/activate
cp ../.env .env

# Start Streamlit
streamlit run app.py --server.port 8501 --server.address 0.0.0.0
```

---

## 10. Access the Dashboard

Open your browser and navigate to:

```
http://localhost:8501
```

If you are accessing from a **host machine** (e.g., your Windows machine connecting
to the Ubuntu VM via VirtualBox/VMware):

```
http://<VM_IP>:8501
```

Find your VM's IP with:
```bash
hostname -I
# e.g., 192.168.56.101
```

---

## 11. Verifying AI Analysis

1. Once the dashboard loads, click **"🤖 Run AI Analysis Now"** in the left sidebar.
2. Wait 10–25 seconds (Groq API response time).
3. Navigate to the **"🤖 AI Agent Insights"** section at the bottom of the dashboard.
4. You should see three tabs:
   - **Synthesis** — Executive summary + health score
   - **Resource Profile** — CPU/memory anomalies (should flag `auth-service` for no limits, `debug-crashloop` for restarts)
   - **Dependency Map** — Shared PVC relationships (spark-worker ↔ kafka-consumer via `spark-output-pvc`)

---

## 12. Troubleshooting

### Metrics server data not showing (`n/a`)
```bash
# Check the metrics-server pod is running
kubectl get pods -n kube-system | grep metrics-server

# If not running, re-enable it
microk8s disable metrics-server
microk8s enable metrics-server

# Wait 2 minutes, then test
kubectl top pods -n production
```

### PVCs stuck in Pending
```bash
# Ensure hostpath-storage add-on is enabled
microk8s enable hostpath-storage

# Check storage class exists
kubectl get storageclass
# Should show: microk8s-hostpath (default)
```

### Groq API errors
```bash
# Verify the key is correctly set
cat ~/k8s-ai-analyzer/.env | grep GROQ_API_KEY

# Test the Groq API manually
curl -X POST https://api.groq.com/openai/v1/chat/completions \
  -H "Authorization: Bearer $GROQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"llama3-8b-8192","messages":[{"role":"user","content":"hi"}],"max_tokens":10}' \
  | python3 -m json.tool
```

### Backend can't connect to Kubernetes
```bash
# Verify kubeconfig is exported
cat ~/.kube/config | head -5

# Verify connectivity
kubectl get nodes
```

### Streamlit can't reach backend
```bash
# Check backend is running
curl http://localhost:8000/health

# Check if the port is open
ss -tlnp | grep 8000
```

---

## Quick Reference: Running Both Services

After initial setup, you can use these one-liners to restart everything:

```bash
# Terminal 1 — Backend
cd ~/k8s-ai-analyzer/backend && source venv/bin/activate && python main.py

# Terminal 2 — Frontend
cd ~/k8s-ai-analyzer/frontend && source venv/bin/activate && streamlit run app.py --server.port 8501
```
