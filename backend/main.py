"""
main.py
=======
FastAPI server for the K8s AI Analyzer.

Endpoints
---------
GET  /health             — liveness probe
GET  /api/pods           — live enriched pod list (all namespaces)
GET  /api/pvcs           — live PVC list
GET  /api/events         — live warning events
GET  /api/metrics        — live raw metrics map (ns/pod -> cpu/mem)
GET  /api/nodes          — node capacity info
GET  /api/history        — metric history for a specific pod
POST /api/analyze        — trigger an on-demand AI analysis pass
GET  /api/last-analysis  — return the most recent AI analysis result
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from ai_agents import get_orchestrator
from k8s_client import get_k8s_client

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

AI_ANALYSIS_INTERVAL = int(os.getenv("AI_ANALYSIS_INTERVAL_SECONDS", "60"))

# ---------------------------------------------------------------------------
# Background auto-analysis loop
# ---------------------------------------------------------------------------
_analysis_lock   = threading.Lock()
_last_analysis:  Optional[Dict[str, Any]] = None
_analysis_error: Optional[str]            = None


def _auto_analysis_loop() -> None:
    """Runs the AI analysis pipeline every AI_ANALYSIS_INTERVAL seconds."""
    global _last_analysis, _analysis_error
    k8s   = get_k8s_client()
    crew  = get_orchestrator()

    while True:
        try:
            logger.info("Auto-analysis: running AI agents …")
            result = crew.run(
                pods   = k8s.get_enriched_pods(),
                pvcs   = k8s.get_pvcs(),
                events = k8s.get_events(),
            )
            with _analysis_lock:
                _last_analysis  = result.to_dict()
                _analysis_error = None
            logger.info("Auto-analysis: complete.")
        except Exception as exc:  # noqa: BLE001
            logger.error("Auto-analysis failed: %s", exc, exc_info=True)
            with _analysis_lock:
                _analysis_error = str(exc)

        time.sleep(AI_ANALYSIS_INTERVAL)


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Eagerly initialise the K8s client so it starts polling immediately
    get_k8s_client()
    logger.info("K8s client initialised.")

    # Start background AI loop in a daemon thread
    t = threading.Thread(target=_auto_analysis_loop, daemon=True)
    t.start()
    logger.info("Background AI analysis loop started (interval=%ds).", AI_ANALYSIS_INTERVAL)

    yield   # application runs here

    logger.info("Shutting down …")
    get_k8s_client().stop()


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="K8s AI Analyzer API",
    description="Real-time pod resource discovery and dependency mapping with AI",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health", tags=["System"])
def health() -> Dict:
    return {"status": "ok", "service": "k8s-ai-analyzer"}


@app.get("/api/pods", tags=["Cluster"])
def list_pods() -> Dict:
    """Return all pods enriched with live CPU/Memory usage."""
    k8s  = get_k8s_client()
    pods = k8s.get_enriched_pods()
    return {
        "count": len(pods),
        "pods":  pods,
    }


@app.get("/api/pvcs", tags=["Cluster"])
def list_pvcs() -> Dict:
    k8s = get_k8s_client()
    pvcs = k8s.get_pvcs()
    return {"count": len(pvcs), "pvcs": pvcs}


@app.get("/api/events", tags=["Cluster"])
def list_events() -> Dict:
    k8s    = get_k8s_client()
    events = k8s.get_events()
    return {"count": len(events), "events": events}


@app.get("/api/metrics", tags=["Cluster"])
def list_metrics() -> Dict:
    k8s     = get_k8s_client()
    metrics = k8s.get_metrics()
    return {"count": len(metrics), "metrics": metrics}


@app.get("/api/nodes", tags=["Cluster"])
def list_nodes() -> Dict:
    k8s   = get_k8s_client()
    nodes = k8s.get_node_info()
    return {"count": len(nodes), "nodes": nodes}


@app.get("/api/history", tags=["Cluster"])
def pod_metric_history(
    namespace: str = Query(..., description="Namespace of the pod"),
    pod:       str = Query(..., description="Pod name"),
) -> Dict:
    """Return rolling metric history for a specific pod."""
    k8s     = get_k8s_client()
    history = k8s.get_metric_history(namespace, pod)
    return {
        "namespace": namespace,
        "pod":       pod,
        "count":     len(history),
        "history":   history,
    }


@app.post("/api/analyze", tags=["AI"])
def trigger_analysis() -> Dict:
    """
    Trigger an on-demand AI analysis pass (synchronous — may take a few seconds).
    The dashboard uses this for manual refresh.
    """
    global _last_analysis, _analysis_error
    k8s  = get_k8s_client()
    crew = get_orchestrator()
    try:
        result = crew.run(
            pods   = k8s.get_enriched_pods(),
            pvcs   = k8s.get_pvcs(),
            events = k8s.get_events(),
        )
        data = result.to_dict()
        with _analysis_lock:
            _last_analysis  = data
            _analysis_error = None
        return {"status": "success", "result": data}
    except Exception as exc:  # noqa: BLE001
        logger.error("On-demand analysis failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/last-analysis", tags=["AI"])
def get_last_analysis() -> Dict:
    """Return the most recent completed AI analysis result."""
    with _analysis_lock:
        if _analysis_error:
            raise HTTPException(status_code=503, detail=_analysis_error)
        if _last_analysis is None:
            raise HTTPException(
                status_code=202,
                detail="Analysis not yet available — please wait for the first run.",
            )
        return _last_analysis


# ---------------------------------------------------------------------------
# Entry point (for direct execution: python main.py)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("BACKEND_HOST", "0.0.0.0"),
        port=int(os.getenv("BACKEND_PORT", "8000")),
        reload=False,
        log_level="info",
    )
