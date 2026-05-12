"""
app.py
======
Streamlit real-time dashboard for the K8s AI Analyzer.

Sections
--------
1. Sidebar   — Cluster summary, namespace filter, manual AI refresh
2. Overview  — Node capacity gauges + namespace pod count bar chart
3. Pod Table — Sortable, colour-coded table of all pods with live metrics
4. Charts    — CPU/Memory time-series per selected pod
5. Dependency Graph — Interactive network graph built with pyvis
6. Events    — Warning event timeline
7. AI Insights Panel — AI agent reports in expandable cards
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from streamlit.delta_generator import DeltaGenerator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
import os
from dotenv import load_dotenv
load_dotenv()

BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")
REFRESH_INTERVAL = 15   # seconds

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="K8s AI Analyzer",
    page_icon="🔮",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS — dark glassmorphism theme
# ---------------------------------------------------------------------------
st.markdown("""
<style>
  /* Dark base */
  [data-testid="stAppViewContainer"] {
      background: linear-gradient(135deg, #0d0f1a 0%, #111827 60%, #0a0e1a 100%);
      color: #e2e8f0;
  }
  [data-testid="stSidebar"] {
      background: rgba(15, 23, 42, 0.9);
      border-right: 1px solid rgba(99, 102, 241, 0.3);
  }
  /* Cards */
  .metric-card {
      background: rgba(30, 41, 59, 0.7);
      border: 1px solid rgba(99, 102, 241, 0.25);
      border-radius: 14px;
      padding: 18px 22px;
      backdrop-filter: blur(12px);
      margin-bottom: 12px;
  }
  .agent-card {
      background: rgba(15, 23, 42, 0.85);
      border: 1px solid rgba(139, 92, 246, 0.4);
      border-radius: 16px;
      padding: 22px;
      margin-bottom: 16px;
  }
  .agent-card h4 { color: #a78bfa; margin: 0 0 10px 0; }
  /* Status badges */
  .badge-running  { background:#16a34a22; color:#4ade80; padding:2px 10px; border-radius:9px; font-size:12px; }
  .badge-pending  { background:#ca8a0422; color:#fbbf24; padding:2px 10px; border-radius:9px; font-size:12px; }
  .badge-failed   { background:#dc262622; color:#f87171; padding:2px 10px; border-radius:9px; font-size:12px; }
  /* Section headers */
  .section-title {
      font-size: 1.2rem;
      font-weight: 700;
      color: #818cf8;
      letter-spacing: 0.04em;
      border-bottom: 1px solid rgba(99,102,241,0.3);
      padding-bottom: 6px;
      margin-bottom: 14px;
  }
  /* Streamlit metric overrides */
  [data-testid="metric-container"] {
      background: rgba(30, 41, 59, 0.6);
      border: 1px solid rgba(99,102,241,0.2);
      border-radius: 12px;
      padding: 12px;
  }
  div[data-testid="stMetric"] label { color: #94a3b8 !important; }
  div[data-testid="stMetric"] div   { color: #e2e8f0 !important; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
@st.cache_data(ttl=REFRESH_INTERVAL, show_spinner=False)
def fetch(endpoint: str) -> Optional[Dict]:
    try:
        r = requests.get(f"{BACKEND}{endpoint}", timeout=8)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        return None


def post(endpoint: str) -> Optional[Dict]:
    try:
        r = requests.post(f"{BACKEND}{endpoint}", timeout=60)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        st.error(f"API error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------
def pods_to_df(pods: List[Dict]) -> pd.DataFrame:
    rows = []
    for p in pods:
        rows.append({
            "Namespace":  p.get("namespace", ""),
            "Pod":        p.get("name", ""),
            "Phase":      p.get("phase", ""),
            "Node":       p.get("node", "") or "—",
            "CPU (m)":    p.get("cpu_usage_millicores"),
            "MEM (Mi)":   round(p.get("mem_usage_mib") or 0, 1),
            "Restarts":   p.get("restart_count", 0),
            "PVCs":       ", ".join(p.get("pvcs", [])) or "—",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def render_sidebar(pods: List[Dict]) -> str:
    st.sidebar.markdown("## 🔮 K8s AI Analyzer")
    st.sidebar.markdown("---")

    # Namespace filter
    namespaces = sorted({p["namespace"] for p in pods})
    selected_ns = st.sidebar.multiselect(
        "Filter Namespaces",
        options=["All"] + namespaces,
        default=["All"],
    )
    ns_filter = None if "All" in selected_ns else selected_ns

    st.sidebar.markdown("---")

    # Cluster health snapshot
    total     = len(pods)
    running   = sum(1 for p in pods if p.get("phase") == "Running")
    pending   = sum(1 for p in pods if p.get("phase") == "Pending")
    failed    = sum(1 for p in pods if p.get("phase") in ("Failed", "Error"))
    restarted = sum(1 for p in pods if (p.get("restart_count") or 0) > 3)

    st.sidebar.metric("Total Pods",         total)
    st.sidebar.metric("Running",            running,   delta=None)
    st.sidebar.metric("Pending / Failed",   f"{pending} / {failed}")
    st.sidebar.metric("High-Restart Pods",  restarted)

    st.sidebar.markdown("---")

    # Manual AI trigger
    if st.sidebar.button("🤖 Run AI Analysis Now", use_container_width=True):
        with st.spinner("Running AI agents … (~10–20s)"):
            result = post("/api/analyze")
        if result:
            st.sidebar.success("✅ Analysis complete!")
            st.cache_data.clear()

    st.sidebar.markdown(
        "<small style='color:#64748b'>Auto-refreshes every 15s</small>",
        unsafe_allow_html=True,
    )

    return ns_filter


# ---------------------------------------------------------------------------
# Section: Overview gauges and namespace chart
# ---------------------------------------------------------------------------
def render_overview(nodes: List[Dict], pods: List[Dict]) -> None:
    st.markdown('<div class="section-title">📊 Cluster Overview</div>', unsafe_allow_html=True)

    col1, col2, col3, col4 = st.columns(4)
    total   = len(pods)
    running = sum(1 for p in pods if p.get("phase") == "Running")
    namespaces = len({p["namespace"] for p in pods})
    pvcs_used  = sum(1 for p in pods if p.get("pvcs"))

    col1.metric("Total Pods",    total)
    col2.metric("Running",       running)
    col3.metric("Namespaces",    namespaces)
    col4.metric("Pods w/ PVCs",  pvcs_used)

    st.markdown("---")

    # Namespace pod count bar chart
    ns_counts: Dict[str, int] = {}
    for p in pods:
        ns_counts[p["namespace"]] = ns_counts.get(p["namespace"], 0) + 1

    fig = go.Figure(go.Bar(
        x=list(ns_counts.keys()),
        y=list(ns_counts.values()),
        marker_color="#818cf8",
        marker_line_color="#6366f1",
        marker_line_width=1.5,
        text=list(ns_counts.values()),
        textposition="outside",
    ))
    fig.update_layout(
        title="Pods per Namespace",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#e2e8f0",
        height=280,
        margin=dict(l=20, r=20, t=40, b=20),
        xaxis=dict(gridcolor="rgba(99,102,241,0.1)"),
        yaxis=dict(gridcolor="rgba(99,102,241,0.1)"),
    )
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Section: Pod table
# ---------------------------------------------------------------------------
def render_pod_table(pods: List[Dict], ns_filter) -> List[Dict]:
    st.markdown('<div class="section-title">🗂️ Pod Resource Table</div>', unsafe_allow_html=True)

    filtered = pods if not ns_filter else [p for p in pods if p["namespace"] in ns_filter]
    df = pods_to_df(filtered)

    if df.empty:
        st.info("No pods found for the selected namespaces.")
        return filtered

    # Colour-code by phase
    def phase_color(val):
        if val == "Running":   return "background-color: rgba(22,163,74,0.15); color: #4ade80"
        if val == "Pending":   return "background-color: rgba(202,138,4,0.15); color: #fbbf24"
        return "background-color: rgba(220,38,38,0.15); color: #f87171"

    def restart_color(val):
        if val is None: return ""
        if val > 10:  return "color: #f87171; font-weight:700"
        if val > 3:   return "color: #fbbf24"
        return "color: #4ade80"

    styled = (
        df.style
        .applymap(phase_color,   subset=["Phase"])
        .applymap(restart_color, subset=["Restarts"])
        .format({"CPU (m)": lambda x: f"{x:.1f}" if x is not None else "n/a",
                 "MEM (Mi)": "{:.1f}"})
        .set_properties(**{"background-color": "rgba(15,23,42,0.6)", "color": "#e2e8f0"})
    )
    st.dataframe(styled, use_container_width=True, height=320)
    return filtered


# ---------------------------------------------------------------------------
# Section: CPU/Memory time-series for a selected pod
# ---------------------------------------------------------------------------
def render_timeseries(filtered_pods: List[Dict]) -> None:
    st.markdown('<div class="section-title">📈 Pod Metric History</div>', unsafe_allow_html=True)

    pod_names = [f"{p['namespace']}/{p['name']}" for p in filtered_pods]
    if not pod_names:
        st.info("No pods to display.")
        return

    selected = st.selectbox("Select pod for time-series view", options=pod_names)
    if not selected:
        return

    ns, pod = selected.split("/", 1)
    hist_data = fetch(f"/api/history?namespace={ns}&pod={pod}")
    history   = (hist_data or {}).get("history", [])

    if not history:
        st.info("No history yet — metrics are collected every 15s. Check back shortly.")
        return

    times = [h["timestamp"] for h in history]
    cpus  = [h.get("cpu_usage_millicores", 0) for h in history]
    mems  = [h.get("mem_usage_mib", 0) for h in history]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=times, y=cpus, name="CPU (m)",
        line=dict(color="#818cf8", width=2),
        fill="tozeroy", fillcolor="rgba(129,140,248,0.1)",
    ))
    fig.add_trace(go.Scatter(
        x=times, y=mems, name="MEM (Mi)",
        yaxis="y2",
        line=dict(color="#34d399", width=2),
        fill="tozeroy", fillcolor="rgba(52,211,153,0.08)",
    ))
    fig.update_layout(
        title=f"Resource Usage — {selected}",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#e2e8f0",
        height=320,
        margin=dict(l=20, r=20, t=40, b=20),
        yaxis  = dict(title="CPU (millicores)", gridcolor="rgba(99,102,241,0.1)", color="#818cf8"),
        yaxis2 = dict(title="Memory (MiB)", overlaying="y", side="right",
                      gridcolor="rgba(52,211,153,0.1)", color="#34d399"),
        xaxis  = dict(gridcolor="rgba(99,102,241,0.08)"),
        legend = dict(bgcolor="rgba(0,0,0,0)"),
    )
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Section: Dependency graph
# ---------------------------------------------------------------------------
def render_dependency_graph(pods: List[Dict], pvcs: List[Dict]) -> None:
    st.markdown('<div class="section-title">🔗 Pod Dependency Graph</div>', unsafe_allow_html=True)

    try:
        import networkx as nx

        G = nx.DiGraph()

        # Add pod nodes
        for p in pods:
            label = f"{p['namespace']}\n{p['name']}"
            color = "#818cf8" if p.get("phase") == "Running" else "#f87171"
            G.add_node(label, color=color, title=f"Phase: {p.get('phase')}\nRestarts: {p.get('restart_count')}")

        # PVC-based edges
        pvc_owners: Dict[str, List[str]] = {}
        for p in pods:
            for pvc in p.get("pvcs", []):
                pvc_owners.setdefault(pvc, []).append(f"{p['namespace']}\n{p['name']}")

        edge_labels = []
        for pvc, owners in pvc_owners.items():
            for i in range(len(owners)):
                for j in range(i + 1, len(owners)):
                    G.add_edge(owners[i], owners[j], label=f"PVC:{pvc}")
                    edge_labels.append((owners[i], owners[j], f"PVC:{pvc}"))

        # Namespace co-location edges (same namespace = potential dependency)
        ns_pods: Dict[str, List[str]] = {}
        for p in pods:
            ns_pods.setdefault(p["namespace"], []).append(f"{p['namespace']}\n{p['name']}")
        for ns, members in ns_pods.items():
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    if not G.has_edge(members[i], members[j]):
                        G.add_edge(members[i], members[j], label="co-located")

        # Layout
        if len(G.nodes) == 0:
            st.info("No pods to graph.")
            return

        pos = nx.spring_layout(G, seed=42, k=2.5)

        edge_x, edge_y = [], []
        for u, v in G.edges():
            x0, y0 = pos[u]
            x1, y1 = pos[v]
            edge_x += [x0, x1, None]
            edge_y += [y0, y1, None]

        node_x = [pos[n][0] for n in G.nodes()]
        node_y = [pos[n][1] for n in G.nodes()]
        colors = [G.nodes[n].get("color", "#818cf8") for n in G.nodes()]
        labels = [n.replace("\n", "/") for n in G.nodes()]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=edge_x, y=edge_y, mode="lines",
            line=dict(width=1, color="rgba(99,102,241,0.4)"),
            hoverinfo="none",
        ))
        fig.add_trace(go.Scatter(
            x=node_x, y=node_y, mode="markers+text",
            marker=dict(size=18, color=colors,
                        line=dict(width=2, color="rgba(255,255,255,0.2)")),
            text=labels,
            textposition="top center",
            textfont=dict(color="#e2e8f0", size=9),
            hoverinfo="text",
        ))
        fig.update_layout(
            showlegend=False,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#e2e8f0",
            height=420,
            margin=dict(l=0, r=0, t=10, b=0),
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        )
        st.plotly_chart(fig, use_container_width=True)

    except Exception as exc:
        st.warning(f"Dependency graph error: {exc}")


# ---------------------------------------------------------------------------
# Section: Events
# ---------------------------------------------------------------------------
def render_events(events: List[Dict]) -> None:
    st.markdown('<div class="section-title">⚠️ Warning Events</div>', unsafe_allow_html=True)
    if not events:
        st.success("✅ No warning events found.")
        return

    df = pd.DataFrame(events)[["namespace", "object", "reason", "count", "message", "last_time"]]
    df.columns = ["Namespace", "Object", "Reason", "Count", "Message", "Last Seen"]
    df = df.sort_values("Count", ascending=False).head(30)

    st.dataframe(
        df.style.set_properties(**{"background-color": "rgba(15,23,42,0.6)", "color": "#e2e8f0"}),
        use_container_width=True,
        height=250,
    )


# ---------------------------------------------------------------------------
# Section: AI Insights
# ---------------------------------------------------------------------------
def render_ai_insights(analysis: Optional[Dict]) -> None:
    st.markdown('<div class="section-title">🤖 AI Agent Insights</div>', unsafe_allow_html=True)

    if not analysis:
        st.info("No AI analysis available yet. Click **Run AI Analysis Now** in the sidebar.")
        return

    run_at = analysis.get("run_at", "Unknown")
    st.caption(f"Last analysis run: `{run_at}`")

    tabs = st.tabs(["🧠 Synthesis", "📊 Resource Profile", "🔗 Dependency Map"])

    with tabs[0]:
        synth = analysis.get("synthesis", {})
        st.markdown(f"""
        <div class="agent-card">
            <h4>🧠 Insights Synthesizer Agent</h4>
            <pre style="white-space:pre-wrap; color:#e2e8f0; font-family:inherit;">{synth.get('analysis','')}</pre>
        </div>
        """, unsafe_allow_html=True)
        st.caption(f"Latency: {synth.get('latency_ms', '?')} ms")

    with tabs[1]:
        res = analysis.get("resource_profile", {})
        st.markdown(f"""
        <div class="agent-card">
            <h4>📊 Resource Profiler Agent</h4>
            <pre style="white-space:pre-wrap; color:#e2e8f0; font-family:inherit;">{res.get('analysis','')}</pre>
        </div>
        """, unsafe_allow_html=True)
        anomalies = res.get("anomalies", [])
        if anomalies:
            st.error("**Detected Anomalies:**\n" + "\n".join(f"- {a}" for a in anomalies))
        st.caption(f"Latency: {res.get('latency_ms', '?')} ms")

    with tabs[2]:
        dep = analysis.get("dependency_map", {})
        st.markdown(f"""
        <div class="agent-card">
            <h4>🔗 Dependency Mapper Agent</h4>
            <pre style="white-space:pre-wrap; color:#e2e8f0; font-family:inherit;">{dep.get('analysis','')}</pre>
        </div>
        """, unsafe_allow_html=True)
        st.caption(f"Latency: {dep.get('latency_ms', '?')} ms")


# ---------------------------------------------------------------------------
# Main layout
# ---------------------------------------------------------------------------
def main() -> None:
    # Header
    st.markdown("""
    <h1 style='text-align:center; background: linear-gradient(90deg,#818cf8,#a78bfa,#34d399);
    -webkit-background-clip:text; -webkit-text-fill-color:transparent; font-size:2.4rem; margin-bottom:4px;'>
    🔮 K8s AI Analyzer
    </h1>
    <p style='text-align:center; color:#64748b; margin-bottom:24px;'>
    Real-time Pod Resource Discovery · Multi-Agent AI · Dependency Mapping
    </p>
    """, unsafe_allow_html=True)

    # Fetch data
    pods_data    = fetch("/api/pods")
    pvcs_data    = fetch("/api/pvcs")
    events_data  = fetch("/api/events")
    nodes_data   = fetch("/api/nodes")
    analysis     = fetch("/api/last-analysis")

    pods   = (pods_data   or {}).get("pods",   [])
    pvcs   = (pvcs_data   or {}).get("pvcs",   [])
    events = (events_data or {}).get("events", [])
    nodes  = (nodes_data  or {}).get("nodes",  [])

    if not pods:
        st.error("⚠️ Cannot reach backend or no pods found. Is the backend running?")
        st.stop()

    # Sidebar returns namespace filter
    ns_filter = render_sidebar(pods)

    # Sections
    render_overview(nodes, pods)
    st.markdown("---")
    filtered = render_pod_table(pods, ns_filter)
    st.markdown("---")

    col_left, col_right = st.columns([3, 2])
    with col_left:
        render_timeseries(filtered)
    with col_right:
        render_events(events)

    st.markdown("---")
    render_dependency_graph(pods, pvcs)
    st.markdown("---")
    render_ai_insights(analysis)

    # Auto-refresh
    time.sleep(REFRESH_INTERVAL)
    st.rerun()


if __name__ == "__main__":
    main()
