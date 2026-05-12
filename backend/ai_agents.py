"""
ai_agents.py
============
Lightweight multi-agent AI analysis engine using the Groq API directly.
No heavy frameworks — just three focused agents with clear roles.

Agents
------
1. ResourceProfilerAgent   — Detects CPU/memory anomalies, bursty workloads,
                             OOM events, and high restart counts.
2. DependencyMapperAgent   — Infers inter-pod relationships via shared PVCs,
                             label selectors, and namespace co-location patterns.
3. InsightsSynthesizerAgent— Combines the two reports above to produce an
                             executive summary with prioritised recommendations.

Each agent makes a single Groq chat-completion call with a carefully crafted
system prompt that is K8s domain-specific.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama3-70b-8192")

# Max tokens for each agent's response
MAX_TOKENS_PROFILER    = 1500
MAX_TOKENS_MAPPER      = 1500
MAX_TOKENS_SYNTHESIZER = 2000

TEMPERATURE = 0.3   # Low temperature → consistent, factual analysis


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class AgentReport:
    agent_name:   str
    analysis:     str
    anomalies:    List[str] = field(default_factory=list)
    run_at:       str       = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    latency_ms:   float     = 0.0


@dataclass
class FullAnalysisResult:
    resource_report:    AgentReport
    dependency_report:  AgentReport
    synthesis_report:   AgentReport
    run_at:             str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_at": self.run_at,
            "resource_profile": {
                "agent":       self.resource_report.agent_name,
                "analysis":    self.resource_report.analysis,
                "anomalies":   self.resource_report.anomalies,
                "latency_ms":  self.resource_report.latency_ms,
                "run_at":      self.resource_report.run_at,
            },
            "dependency_map": {
                "agent":      self.dependency_report.agent_name,
                "analysis":   self.dependency_report.analysis,
                "anomalies":  self.dependency_report.anomalies,
                "latency_ms": self.dependency_report.latency_ms,
                "run_at":     self.dependency_report.run_at,
            },
            "synthesis": {
                "agent":      self.synthesis_report.agent_name,
                "analysis":   self.synthesis_report.analysis,
                "anomalies":  self.synthesis_report.anomalies,
                "latency_ms": self.synthesis_report.latency_ms,
                "run_at":     self.synthesis_report.run_at,
            },
        }


# ---------------------------------------------------------------------------
# Base Groq caller
# ---------------------------------------------------------------------------
class GroqAgent:
    """Minimal Groq API wrapper for one-shot completion calls."""

    def __init__(self, name: str, system_prompt: str) -> None:
        self.name          = name
        self.system_prompt = system_prompt
        self._client       = Groq(api_key=GROQ_API_KEY)

    def run(self, user_message: str, max_tokens: int = 1200) -> AgentReport:
        t0 = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user",   "content": user_message},
                ],
                temperature=TEMPERATURE,
                max_tokens=max_tokens,
            )
            text = response.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001
            logger.error("Groq API error in %s: %s", self.name, exc)
            text = f"[Agent Error] {exc}"

        latency = (time.perf_counter() - t0) * 1000
        logger.info("%s completed in %.0f ms", self.name, latency)

        # Try to extract bullet-point anomalies from the text
        anomalies = self._extract_anomalies(text)
        return AgentReport(
            agent_name=self.name,
            analysis=text.strip(),
            anomalies=anomalies,
            latency_ms=round(latency, 1),
        )

    @staticmethod
    def _extract_anomalies(text: str) -> List[str]:
        """Pull lines that look like anomaly bullets from the agent response."""
        anomalies = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("- ⚠", "- ❌", "- 🔴", "- ALERT", "- WARNING", "- ANOMALY")):
                anomalies.append(stripped.lstrip("- ").strip())
        return anomalies


# ---------------------------------------------------------------------------
# Agent 1: Resource Profiler
# ---------------------------------------------------------------------------
RESOURCE_PROFILER_SYSTEM = """\
You are a Kubernetes Resource Profiler AI operating inside a real-time cluster analysis platform.

Your job: Analyse the raw pod resource snapshot provided and detect:
1. Pods with CPU usage > 80% of their limit (or > 500m if no limit set)
2. Pods with memory usage > 80% of their limit (or > 512Mi if no limit set)
3. Pods with 0 resource requests/limits (unguaranteed QoS — risky!)
4. Pods in CrashLoopBackOff or with restart_count > 5
5. Bursty workloads — pods with no recent metrics but high request values
6. Namespaces that are resource-heavy compared to others

Output format:
- Start with a 2-sentence executive summary.
- Then list anomalies using this exact prefix so they can be parsed:
  "- ⚠ <pod> in <namespace>: <reason>"  for warnings
  "- ❌ <pod> in <namespace>: <reason>"  for critical issues
- End with a short "Recommendations" section (numbered list, max 5 items).

Be factual. Use only the data provided. Do not hallucinate pod names.
"""


# ---------------------------------------------------------------------------
# Agent 2: Dependency Mapper
# ---------------------------------------------------------------------------
DEPENDENCY_MAPPER_SYSTEM = """\
You are a Kubernetes Dependency Mapper AI. You infer relationships between pods
based on structural evidence in the cluster snapshot.

Mapping rules to apply:
1. Shared PVCs — two pods mounting the same PVC have a storage dependency.
2. Label selector patterns — pods with labels like "app=frontend" / "app=backend"
   are likely in a client-server relationship.
3. Namespace co-location — pods in the same namespace likely form a service group.
4. Service naming conventions — e.g., pods named *-db, *-cache, *-api suggest tiers.
5. High restart count correlation — if multiple pods in the same namespace are
   restarting, suspect a shared downstream failure (e.g., DB crash).

Output format:
- List discovered dependency pairs as:
  "POD_A (NS) → POD_B (NS) : <reason>"
- Group them under headings: Storage Dependencies, Network Dependencies, Suspected Dependencies.
- End with a "Dependency Risk Summary" — which pods are most critical (relied on by others).
- Use "- ⚠" prefix for any dependency that looks unhealthy.

Be factual. Only infer from the data given.
"""


# ---------------------------------------------------------------------------
# Agent 3: Insights Synthesizer
# ---------------------------------------------------------------------------
INSIGHTS_SYNTHESIZER_SYSTEM = """\
You are the Chief Insights AI for a Kubernetes cluster monitoring platform.

You receive the analysis from two specialist agents:
  (A) Resource Profiler — CPU/memory anomalies and pod health
  (B) Dependency Mapper — pod relationships and dependency risks

Your job: Synthesise both analyses into a unified, prioritised operational report.

Output structure:
1. **Cluster Health Score** — a score from 0–100 based on the evidence (with brief justification).
2. **Top 5 Priority Actions** — the most impactful things an operator should do RIGHT NOW.
3. **Root Cause Hypotheses** — For any critical anomaly, propose a likely root cause.
4. **Forecast** — If current trends continue, what will happen in the next 30 minutes?
5. **NLP Summary** — 3–4 sentences a non-technical stakeholder can understand.

Use "- ❌" prefix to highlight critical items, "- ⚠" for warnings.
Be concise, professional, and actionable. Avoid repeating information verbatim from the sub-reports.
"""


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
class MultiAgentOrchestrator:
    """Runs all three agents sequentially and returns a unified result."""

    def __init__(self) -> None:
        self.profiler    = GroqAgent("ResourceProfilerAgent",    RESOURCE_PROFILER_SYSTEM)
        self.mapper      = GroqAgent("DependencyMapperAgent",    DEPENDENCY_MAPPER_SYSTEM)
        self.synthesizer = GroqAgent("InsightsSynthesizerAgent", INSIGHTS_SYNTHESIZER_SYSTEM)

        self._last_result: Optional[FullAnalysisResult] = None

    def run(
        self,
        pods: List[Dict],
        pvcs: List[Dict],
        events: List[Dict],
    ) -> FullAnalysisResult:
        """
        Execute all three agents and return a FullAnalysisResult.

        Parameters
        ----------
        pods   : enriched pod list from K8sClient.get_enriched_pods()
        pvcs   : PVC list from K8sClient.get_pvcs()
        events : event list from K8sClient.get_events()
        """
        # --- Build the data payload string (keeps prompts under token limits) ---
        snapshot_str = self._build_snapshot_str(pods, pvcs, events)
        logger.info("Snapshot size: %d chars", len(snapshot_str))

        # --- Agent 1: Resource Profiler ---
        profiler_prompt = (
            f"Here is the current Kubernetes cluster snapshot:\n\n{snapshot_str}\n\n"
            "Please perform your resource profiling analysis now."
        )
        resource_report = self.profiler.run(profiler_prompt, MAX_TOKENS_PROFILER)

        # --- Agent 2: Dependency Mapper ---
        mapper_prompt = (
            f"Here is the current Kubernetes cluster snapshot:\n\n{snapshot_str}\n\n"
            "Please perform your dependency mapping analysis now."
        )
        dependency_report = self.mapper.run(mapper_prompt, MAX_TOKENS_MAPPER)

        # --- Agent 3: Synthesizer (gets both reports as context) ---
        synthesizer_prompt = (
            "=== RESOURCE PROFILER REPORT ===\n"
            f"{resource_report.analysis}\n\n"
            "=== DEPENDENCY MAPPER REPORT ===\n"
            f"{dependency_report.analysis}\n\n"
            "Please synthesise these reports into the unified operational report now."
        )
        synthesis_report = self.synthesizer.run(synthesizer_prompt, MAX_TOKENS_SYNTHESIZER)

        result = FullAnalysisResult(
            resource_report=resource_report,
            dependency_report=dependency_report,
            synthesis_report=synthesis_report,
        )
        self._last_result = result
        return result

    def get_last_result(self) -> Optional[FullAnalysisResult]:
        return self._last_result

    # ------------------------------------------------------------------ #
    # Data formatting helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_snapshot_str(
        pods: List[Dict],
        pvcs: List[Dict],
        events: List[Dict],
    ) -> str:
        """Convert cluster data to a compact, LLM-readable string."""
        lines: List[str] = []

        # --- Pods ---
        lines.append("### POD SNAPSHOT")
        by_ns: Dict[str, List[Dict]] = {}
        for p in pods:
            by_ns.setdefault(p["namespace"], []).append(p)

        for ns, ns_pods in sorted(by_ns.items()):
            lines.append(f"\nNamespace: {ns}  ({len(ns_pods)} pods)")
            for pod in ns_pods:
                cpu_use = (
                    f"{pod['cpu_usage_millicores']}m"
                    if pod.get("cpu_usage_millicores") is not None
                    else "n/a"
                )
                mem_use = (
                    f"{pod['mem_usage_mib']:.1f}Mi"
                    if pod.get("mem_usage_mib") is not None
                    else "n/a"
                )
                cpu_req = sum(c["cpu_request"] for c in pod["containers"])
                mem_req = sum(c["mem_request"] for c in pod["containers"])
                cpu_lim = sum(c["cpu_limit"]   for c in pod["containers"])
                mem_lim = sum(c["mem_limit"]   for c in pod["containers"])

                lines.append(
                    f"  [{pod['phase']}] {pod['name']}  "
                    f"CPU: use={cpu_use} req={cpu_req:.0f}m lim={cpu_lim:.0f}m  "
                    f"MEM: use={mem_use} req={mem_req:.0f}Mi lim={mem_lim:.0f}Mi  "
                    f"restarts={pod['restart_count']}  "
                    f"pvcs={pod['pvcs']}  labels={pod['labels']}"
                )

        # --- PVCs ---
        lines.append("\n### PVC SNAPSHOT")
        for pvc in pvcs:
            lines.append(
                f"  [{pvc['phase']}] {pvc['namespace']}/{pvc['name']}  "
                f"capacity={pvc['capacity']}  class={pvc['storage_class']}"
            )
        if not pvcs:
            lines.append("  (no PVCs found)")

        # --- Events ---
        lines.append("\n### RECENT WARNING EVENTS (last 20)")
        for ev in events[:20]:
            lines.append(
                f"  [{ev['reason']}] {ev['namespace']}/{ev['object']}  "
                f"count={ev['count']}  msg={ev['message'][:120]}"
            )
        if not events:
            lines.append("  (no warning events found)")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_orchestrator_instance: Optional[MultiAgentOrchestrator] = None


def get_orchestrator() -> MultiAgentOrchestrator:
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = MultiAgentOrchestrator()
    return _orchestrator_instance
