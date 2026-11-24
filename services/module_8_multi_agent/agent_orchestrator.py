"""
Module 8: Multi-Agent Orchestration with LangGraph DAG, specialized agents,
self-healing reflection, and Qdrant (or in-memory) RAG memory.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, List, Optional, TypedDict

from pydantic import BaseModel, Field

from services.ai_gateway.gateway import UniversalAIGateway
from services.security_governance.pii_redactor import PIIRedactor
from services.shared.humanization import HumanizationEngine, humanization


class CampaignStateGraph(BaseModel):
    graph_id: str
    tenant_id: str
    campaign_goal: str
    current_node: str = "SUPERVISOR"
    extracted_leads: List[Dict[str, Any]] = Field(default_factory=list)
    generated_copy: List[Dict[str, Any]] = Field(default_factory=list)
    security_clearance: bool = False
    execution_errors: List[Dict[str, Any]] = Field(default_factory=list)
    healing_actions: List[Dict[str, Any]] = Field(default_factory=list)
    rag_context: List[str] = Field(default_factory=list)
    status: str = "IN_PROGRESS"
    node_trace: List[str] = Field(default_factory=list)
    human_delays: List[float] = Field(default_factory=list)
    humanization: Dict[str, Any] = Field(default_factory=dict)


class AgentGraphState(TypedDict, total=False):
    graph_id: str
    tenant_id: str
    account_id: str
    campaign_goal: str
    current_node: str
    extracted_leads: List[Dict[str, Any]]
    generated_copy: List[Dict[str, Any]]
    security_clearance: bool
    execution_errors: List[Dict[str, Any]]
    healing_actions: List[Dict[str, Any]]
    rag_context: List[str]
    status: str
    node_trace: List[str]
    human_delays: List[float]
    humanization: Dict[str, Any]


class VectorMemoryStore:
    """Qdrant-backed semantic memory with in-memory fallback for local/dev."""

    COLLECTION = "wolf_brand_voice"

    def __init__(self):
        self._memory: List[Dict[str, Any]] = []
        self._qdrant = None
        self.backend = "memory"
        self._init_qdrant()
        self._seed_defaults()

    def _init_qdrant(self) -> None:
        if os.getenv("QDRANT_FORCE_MEMORY", "false").lower() in {"1", "true", "yes"}:
            return
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.http import models as qm

            url = os.getenv("QDRANT_URL", "http://localhost:6333")
            client = QdrantClient(url=url, timeout=2.0)
            # Probe
            client.get_collections()
            names = [c.name for c in client.get_collections().collections]
            if self.COLLECTION not in names:
                client.create_collection(
                    collection_name=self.COLLECTION,
                    vectors_config=qm.VectorParams(size=8, distance=qm.Distance.COSINE),
                )
            self._qdrant = client
            self.backend = "qdrant"
        except Exception:
            self._qdrant = None
            self.backend = "memory"

    def _seed_defaults(self) -> None:
        seeds = [
            "Brand voice: professional, concise, enterprise-friendly, never spammy.",
            "CTA preference: soft ask with concrete value (case study, demo, ROI).",
            "Compliance: no medical/financial guarantees; redact PII before send.",
            "ICP: growth/marketing leaders at mid-market SaaS companies.",
        ]
        for text in seeds:
            self.upsert(text, {"type": "brand_voice"})

    @staticmethod
    def _embed(text: str) -> List[float]:
        # Lightweight deterministic embedding (no external model required)
        vals = [0.0] * 8
        for i, ch in enumerate(text.lower()):
            vals[i % 8] += (ord(ch) % 31) / 31.0
        norm = sum(v * v for v in vals) ** 0.5 or 1.0
        return [v / norm for v in vals]

    def upsert(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        metadata = metadata or {}
        vector = self._embed(text)
        point_id = str(uuid.uuid4())
        record = {"id": point_id, "text": text, "metadata": metadata, "vector": vector}
        self._memory.append(record)
        if self._qdrant is not None:
            try:
                from qdrant_client.http import models as qm

                self._qdrant.upsert(
                    collection_name=self.COLLECTION,
                    points=[
                        qm.PointStruct(
                            id=point_id,
                            vector=vector,
                            payload={"text": text, **metadata},
                        )
                    ],
                )
            except Exception:
                pass

    def search(self, query: str, limit: int = 3) -> List[str]:
        qvec = self._embed(query)
        if self._qdrant is not None:
            try:
                hits = self._qdrant.search(
                    collection_name=self.COLLECTION,
                    query_vector=qvec,
                    limit=limit,
                )
                return [h.payload.get("text", "") for h in hits if h.payload]
            except Exception:
                pass

        def score(rec: Dict[str, Any]) -> float:
            v = rec["vector"]
            return sum(a * b for a, b in zip(v, qvec))

        ranked = sorted(self._memory, key=score, reverse=True)[:limit]
        return [r["text"] for r in ranked]


class Module8MultiAgentOrchestrator:
    """
    LangGraph-directed multi-agent campaign graph with self-healing reflection.
    Falls back to sequential node execution when langgraph is unavailable.
    Human-like: think delays between agents, warm-up caps, Spintax DM variants.
    """

    def __init__(self, engine: Optional[HumanizationEngine] = None):
        self.ai_gateway = UniversalAIGateway()
        self.pii_redactor = PIIRedactor()
        self.memory = VectorMemoryStore()
        self.human = engine or humanization

    async def _think(self) -> float:
        return await self.human.async_apply_delay(self.human.agent_think_delay(), fast_cap=0.2)

    async def run_campaign_graph(
        self,
        tenant_id: str,
        campaign_goal: str,
        inject_error: Optional[Dict[str, Any]] = None,
        account_id: str = "agent_main",
    ) -> CampaignStateGraph:
        cap = self.human.can_act(account_id)
        if not cap["allowed"]:
            return CampaignStateGraph(
                graph_id=f"graph_{uuid.uuid4().hex[:10]}",
                tenant_id=tenant_id,
                campaign_goal=campaign_goal,
                current_node="THROTTLED",
                status="THROTTLED_HUMAN_CAP",
                rag_context=[f"Humanization blocked: {cap}"],
            )

        initial: AgentGraphState = {
            "graph_id": f"graph_{uuid.uuid4().hex[:10]}",
            "tenant_id": tenant_id,
            "campaign_goal": campaign_goal,
            "account_id": account_id,
            "current_node": "SUPERVISOR",
            "extracted_leads": [],
            "generated_copy": [],
            "security_clearance": False,
            "execution_errors": [inject_error] if inject_error else [],
            "healing_actions": [],
            "rag_context": self.memory.search(campaign_goal, limit=3),
            "status": "IN_PROGRESS",
            "node_trace": [],
            "human_delays": [],
        }

        final_state = await self._run_langgraph(initial)
        if final_state is None:
            final_state = await self._run_sequential(initial)

        self.human.record_action(account_id)
        final_state["humanization"] = {
            "profile": self.human.profile.profile_name,
            "cap": self.human.can_act(account_id),
            "delays": final_state.get("human_delays", []),
        }
        allowed = set(CampaignStateGraph.model_fields.keys())
        return CampaignStateGraph(**{k: v for k, v in final_state.items() if k in allowed})

    async def _run_langgraph(self, initial: AgentGraphState) -> Optional[AgentGraphState]:
        try:
            from langgraph.graph import END, StateGraph
        except Exception:
            return None

        graph = StateGraph(AgentGraphState)
        graph.add_node("supervisor", self._node_supervisor)
        graph.add_node("security", self._node_security)
        graph.add_node("prospecting", self._node_prospecting)
        graph.add_node("copywriter", self._node_copywriter)
        graph.add_node("self_healing", self._node_self_healing)
        graph.add_node("blocked", self._node_blocked)
        graph.add_node("complete", self._node_complete)

        graph.set_entry_point("supervisor")
        graph.add_edge("supervisor", "security")
        graph.add_conditional_edges(
            "security",
            self._route_after_security,
            {"prospecting": "prospecting", "blocked": "blocked"},
        )
        graph.add_edge("prospecting", "copywriter")
        graph.add_conditional_edges(
            "copywriter",
            self._route_after_copywriter,
            {"self_healing": "self_healing", "complete": "complete"},
        )
        graph.add_edge("self_healing", "complete")
        graph.add_edge("blocked", END)
        graph.add_edge("complete", END)

        compiled = graph.compile()
        return await compiled.ainvoke(initial)

    async def _run_sequential(self, state: AgentGraphState) -> AgentGraphState:
        state = await self._node_supervisor(state)
        state = await self._node_security(state)
        if not state.get("security_clearance"):
            return await self._node_blocked(state)
        state = await self._node_prospecting(state)
        state = await self._node_copywriter(state)
        if state.get("execution_errors"):
            state = await self._node_self_healing(state)
        return await self._node_complete(state)

    def _route_after_security(self, state: AgentGraphState) -> str:
        return "prospecting" if state.get("security_clearance") else "blocked"

    def _route_after_copywriter(self, state: AgentGraphState) -> str:
        return "self_healing" if state.get("execution_errors") else "complete"

    async def _node_supervisor(self, state: AgentGraphState) -> AgentGraphState:
        delay = await self._think()
        state.setdefault("human_delays", []).append(round(delay, 3))
        state["current_node"] = "SUPERVISOR"
        state.setdefault("node_trace", []).append("SUPERVISOR")
        # Optional LLM plan decomposition
        try:
            plan = await self.ai_gateway.chat_completion(
                "module_8",
                [
                    {
                        "role": "user",
                        "content": (
                            "Decompose this campaign goal into 3 short execution steps. "
                            f"Goal: {state.get('campaign_goal')}. Reply as a short bullet list."
                        ),
                    }
                ],
                temperature=0.3,
                max_tokens=200,
            )
            content = plan.get("choices", [{}])[0].get("message", {}).get("content", "")
            if content:
                state.setdefault("rag_context", []).append(f"Supervisor plan:\n{content}")
        except Exception:
            pass
        return state

    async def _node_security(self, state: AgentGraphState) -> AgentGraphState:
        delay = await self._think()
        state.setdefault("human_delays", []).append(round(delay, 3))
        state["current_node"] = "SECURITY_RISK"
        state.setdefault("node_trace", []).append("SECURITY_RISK")
        # Block obviously unsafe goals
        goal = (state.get("campaign_goal") or "").lower()
        unsafe = any(k in goal for k in ("scrape passwords", "bypass captcha", "buy followers"))
        state["security_clearance"] = not unsafe
        if unsafe:
            state["status"] = "BLOCKED_BY_SECURITY"
            state.setdefault("execution_errors", []).append(
                {"type": "SECURITY_POLICY", "message": "Goal violates safety policy"}
            )
        return state

    async def _node_prospecting(self, state: AgentGraphState) -> AgentGraphState:
        delay = await self._think()
        state.setdefault("human_delays", []).append(round(delay, 3))
        state["current_node"] = "PROSPECTING"
        state.setdefault("node_trace", []).append("PROSPECTING")
        # Enrich with RAG ICP hints
        icp_hint = " ".join(state.get("rag_context") or [])
        leads = [
            {
                "lead_id": f"lead_{uuid.uuid4().hex[:6]}",
                "name": "Alex Rivera",
                "score": 0.92,
                "icp_match": "High",
                "notes": icp_hint[:160],
            },
            {
                "lead_id": f"lead_{uuid.uuid4().hex[:6]}",
                "name": "Jordan Lee",
                "score": 0.88,
                "icp_match": "High",
                "notes": "Matches mid-market SaaS growth profile",
            },
        ]
        state["extracted_leads"] = leads
        return state

    async def _node_copywriter(self, state: AgentGraphState) -> AgentGraphState:
        delay = await self._think()
        state.setdefault("human_delays", []).append(round(delay, 3))
        state["current_node"] = "COPYWRITER"
        state.setdefault("node_trace", []).append("COPYWRITER")
        brand = " | ".join(state.get("rag_context") or [])
        template = self.human.spintax_dm("there")
        try:
            raw = await self.ai_gateway.chat_completion(
                "module_8",
                [
                    {
                        "role": "user",
                        "content": (
                            f"Write one short DM using this brand context: {brand}\n"
                            f"Campaign goal: {state.get('campaign_goal')}\n"
                            "Use {name} placeholder. No hashtags. Sound natural and human."
                        ),
                    }
                ],
                temperature=0.6,
                max_tokens=120,
            )
            content = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
            if content and "{name}" in content:
                template = content.strip()
            elif content:
                template = content.strip() + " — {name}"
        except Exception:
            pass

        sanitized = self.pii_redactor.sanitize_prompt(template)
        copies = []
        for lead in state.get("extracted_leads") or []:
            # Prefer Spintax variant per lead so messages aren't identical
            if "{name}" in sanitized and "|" not in sanitized:
                msg = sanitized.replace("{name}", lead.get("name", "there"))
            else:
                msg = self.human.spintax_dm(lead.get("name", "there"))
            # Pace as if an operator is reviewing each DM
            await self.human.async_apply_delay(
                min(self.human.estimated_typing_duration(msg) * 0.15, 2.0),
                fast_cap=0.15,
            )
            copies.append({"lead_id": lead.get("lead_id"), "message": msg})
        state["generated_copy"] = copies

        # Store successful copy pattern in vector memory
        self.memory.upsert(sanitized, {"type": "copy_template", "tenant_id": state.get("tenant_id")})
        return state

    async def _node_self_healing(self, state: AgentGraphState) -> AgentGraphState:
        """
        Classify errors: TRANSIENT / TECHNICAL (DOM) / SECURITY → pivot strategy.
        """
        state["current_node"] = "SELF_HEALING"
        state.setdefault("node_trace", []).append("SELF_HEALING")
        actions: List[Dict[str, Any]] = []
        for err in state.get("execution_errors") or []:
            err_type = str(err.get("type", "TRANSIENT")).upper()
            if err_type in {"DOM_SELECTOR_DRIFT", "TECHNICAL"}:
                action = {
                    "error_type": err_type,
                    "pivot": "FALLBACK_META_GRAPH_API",
                    "detail": "Detected DOM shift. Pivoting to Meta Graph API dispatch.",
                }
            elif err_type in {"SECURITY_CAPTCHA", "SECURITY"}:
                action = {
                    "error_type": err_type,
                    "pivot": "ESCALATE_HITL",
                    "detail": "Detected CAPTCHA/challenge. Escalating to Module 6 HITL queue.",
                }
                try:
                    from services.module_6_control_center.governance import control_center

                    control_center.enqueue_hitl_if_needed(
                        tenant_id=state.get("tenant_id", "unknown"),
                        account_id=err.get("account_id", "acct_unknown"),
                        confidence=0.4,
                        payload={"error": err, "graph_id": state.get("graph_id")},
                        manual=True,
                    )
                except Exception:
                    pass
            elif err_type in {"PROXY_TIMEOUT", "TRANSIENT"}:
                action = {
                    "error_type": err_type,
                    "pivot": "ROTATE_PROXY_SUBNET",
                    "detail": "Transient/network failure. Rotating proxy subnet and retrying.",
                }
            else:
                action = {
                    "error_type": err_type,
                    "pivot": "RETRY_WITH_BACKOFF",
                    "detail": "Unclassified error. Scheduling jittered backoff retry.",
                }
            actions.append(action)
        state["healing_actions"] = actions
        return state

    async def _node_blocked(self, state: AgentGraphState) -> AgentGraphState:
        state["current_node"] = "BLOCKED"
        state.setdefault("node_trace", []).append("BLOCKED")
        state["status"] = "BLOCKED_BY_SECURITY"
        return state

    async def _node_complete(self, state: AgentGraphState) -> AgentGraphState:
        state["current_node"] = "COMPLETED"
        state.setdefault("node_trace", []).append("COMPLETED")
        if state.get("status") != "BLOCKED_BY_SECURITY":
            state["status"] = "COMPLETED"
        return state

    # Back-compat private aliases used by earlier sequential API
    async def _agent_supervisor_orchestrate(self, graph: CampaignStateGraph) -> CampaignStateGraph:
        state = await self._node_supervisor(graph.model_dump())
        return CampaignStateGraph(**state)

    async def _agent_security_audit(self, graph: CampaignStateGraph) -> CampaignStateGraph:
        state = await self._node_security(graph.model_dump())
        return CampaignStateGraph(**state)

    async def _agent_prospecting(self, graph: CampaignStateGraph) -> CampaignStateGraph:
        state = await self._node_prospecting(graph.model_dump())
        return CampaignStateGraph(**state)

    async def _agent_copywriter(self, graph: CampaignStateGraph) -> CampaignStateGraph:
        state = await self._node_copywriter(graph.model_dump())
        return CampaignStateGraph(**state)

    async def _agent_self_healing_pivot(self, graph: CampaignStateGraph) -> CampaignStateGraph:
        state = await self._node_self_healing(graph.model_dump())
        return CampaignStateGraph(**state)
