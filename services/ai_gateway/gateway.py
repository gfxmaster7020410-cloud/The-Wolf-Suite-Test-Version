"""
Universal OpenAI-compatible AI Gateway with Level 1/Level 2 inheritance.
Exposes /v1/chat/completions for modules and external clients.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


class AIGatewayConfig(BaseModel):
    base_url: str
    api_key: str
    default_model: str


class ChatMessage(BaseModel):
    role: str
    content: Any


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: List[ChatMessage]
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    module: str = Field(default="master", description="Module key for Level-2 override resolution")


class UniversalAIGateway:
    """
    Universal Open-Source AI Gateway implementing Level 1 (Global Master Setting)
    and Level 2 (Section/Module Override) configuration inheritance.
    All modules interface through a unified OpenAI-compatible REST schema.
    """

    def __init__(self):
        self.master_config = AIGatewayConfig(
            base_url=os.getenv("MASTER_AI_BASE_URL", "https://api.openai.com/v1"),
            api_key=os.getenv("MASTER_AI_API_KEY", "sk-proj-placeholder"),
            default_model=os.getenv("MASTER_AI_MODEL", "gpt-4o"),
        )
        self.module_overrides: Dict[str, AIGatewayConfig] = {}
        self._load_module_overrides()

    def _load_module_overrides(self):
        if os.getenv("MOD2_AI_BASE_URL"):
            self.module_overrides["module_2"] = AIGatewayConfig(
                base_url=os.getenv("MOD2_AI_BASE_URL"),
                api_key=os.getenv("MOD2_AI_API_KEY", "ollama"),
                default_model=os.getenv("MOD2_AI_MODEL", "qwen2.5-vl"),
            )

        if os.getenv("MOD1_AI_BASE_URL") or os.getenv("GEMINI_API_KEY"):
            self.module_overrides["module_1"] = AIGatewayConfig(
                base_url=os.getenv(
                    "MOD1_AI_BASE_URL",
                    os.getenv(
                        "MASTER_AI_BASE_URL",
                        "https://generativelanguage.googleapis.com/v1beta/openai",
                    ),
                ),
                api_key=os.getenv("MOD1_AI_API_KEY")
                or os.getenv("GEMINI_API_KEY")
                or os.getenv("MASTER_AI_API_KEY", ""),
                default_model=os.getenv(
                    "MOD1_AI_MODEL", os.getenv("GEMINI_API_MODEL", "gemini-2.0-flash")
                ),
            )

        if os.getenv("MOD3_AI_BASE_URL"):
            self.module_overrides["module_3"] = AIGatewayConfig(
                base_url=os.getenv("MOD3_AI_BASE_URL"),
                api_key=os.getenv("MOD3_AI_API_KEY", ""),
                default_model=os.getenv("MOD3_AI_MODEL", "llama-3.3-70b-versatile"),
            )

        if os.getenv("MOD8_AI_BASE_URL"):
            self.module_overrides["module_8"] = AIGatewayConfig(
                base_url=os.getenv("MOD8_AI_BASE_URL"),
                api_key=os.getenv("MOD8_AI_API_KEY", ""),
                default_model=os.getenv("MOD8_AI_MODEL", "deepseek-reasoner"),
            )

    def get_config_for_module(self, module_name: str) -> AIGatewayConfig:
        return self.module_overrides.get(module_name, self.master_config)

    def describe(self) -> Dict[str, Any]:
        return {
            "master": self.master_config.model_dump(exclude={"api_key"}),
            "overrides": {
                k: v.model_dump(exclude={"api_key"}) for k, v in self.module_overrides.items()
            },
        }

    async def chat_completion(
        self,
        module_name: str,
        messages: list,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        model_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        config = self.get_config_for_module(module_name)
        target_model = model_override or config.default_model

        if not config.api_key or str(config.api_key).startswith("sk-proj-placeholder"):
            # Deterministic local stub so pipelines can run without cloud keys
            last_user = ""
            for msg in reversed(messages):
                content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
                if isinstance(content, list):
                    texts = [c.get("text", "") for c in content if isinstance(c, dict)]
                    content = " ".join(texts)
                if content:
                    last_user = str(content)
                    break
            lower = last_user.lower()
            if "json array" in lower or "exactly 4 caption" in lower:
                stub_content = (
                    '[{"caption":"Stop spending hours on manual posts — let AI scale your audience.",'
                    '"hashtags":"#Growth #AI #Automation","engagement_score":0.91,"cta_strength":0.88},'
                    '{"caption":"One asset. Three platforms. Zero busywork.",'
                    '"hashtags":"#ContentOps #AI","engagement_score":0.84,"cta_strength":0.8},'
                    '{"caption":"The future of multi-platform engagement is here.",'
                    '"hashtags":"#WolfSuite #Scale","engagement_score":0.86,"cta_strength":0.79},'
                    '{"caption":"Transform your workflow with autonomous social intelligence.",'
                    '"hashtags":"#Enterprise #AI","engagement_score":0.8,"cta_strength":0.7}]'
                )
            elif "select the single best" in lower or '"description"' in lower:
                stub_content = (
                    '{"description":"Stop spending hours on manual posts — let AI scale your audience.",'
                    '"hashtags":"#Growth #AI #Automation","reason":"local_stub_selected"}'
                )
            elif "discussion reply hooks" in lower or '"hooks"' in lower:
                stub_content = (
                    '{"hooks":["What feature would help your team most?",'
                    '"How many hours do you spend on manual posts?",'
                    '"Drop your thoughts below!",'
                    '"Tried multi-platform AI scheduling yet?",'
                    '"Tag a founder who needs this automation!"]}'
                )
            elif "comment" in lower or "facebook" in lower:
                stub_content = (
                    "Thanks for sharing this — really solid work. "
                    "Curious how long the setup took?"
                )
            else:
                stub_content = last_user[:400] or "Acknowledged."
            return {
                "id": "chatcmpl-wolf-local",
                "object": "chat.completion",
                "model": target_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": stub_content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "wolf_local_stub": True,
            }

        endpoint = f"{config.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()


gateway_singleton = UniversalAIGateway()
app = FastAPI(title="Wolf Suite Universal AI Gateway")


@app.get("/v1/models")
def list_models():
    desc = gateway_singleton.describe()
    models = [{"id": desc["master"]["default_model"], "object": "model", "owned_by": "master"}]
    for name, cfg in desc["overrides"].items():
        models.append({"id": cfg["default_model"], "object": "model", "owned_by": name})
    return {"object": "list", "data": models, "wolf": desc}


@app.post("/v1/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    x_wolf_module: Optional[str] = Header(default=None, alias="X-Wolf-Module"),
):
    module = x_wolf_module or body.module or "master"
    # Normalize module_2 style keys
    if module.startswith("m") and module[1:].isdigit():
        module = f"module_{module[1:]}"
    try:
        return await gateway_singleton.chat_completion(
            module_name=module if module != "master" else "module_2",
            messages=[m.model_dump() for m in body.messages],
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            model_override=body.model,
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/health")
def health():
    return {"status": "ok", "service": "ai_gateway", "config": gateway_singleton.describe()}
