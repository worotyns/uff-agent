from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.agent import AgentProfile
from src.llm.openrouter import InsufficientCreditsError, LLMResponse, OpenRouterClient
from src.tools.mcp import McpManager
from src.tools.skill import SkillRegistry
from src.tokenizer import count_messages_tokens

logger = logging.getLogger(__name__)

CONTINUE_PROMPT = "Continue with the result above. Call more tools if needed, or write the final response."

_THREAD_AWARE_TOOLS = {
    "add_reminder", "create_script", "generate_image", "text_to_speech",
    "browser_screenshot", "browser_markdown", "browser_crawl", "send_progress",
    "create_background_task", "create_attachment", "search_documents", "send_document",
}


@dataclass
class RunResult:
    content: str
    sources: list[dict] = field(default_factory=list)
    model: str = ""
    usage: dict | None = None
    took_s: float = 0.0
    failed: bool = False
    error: str = ""


def merge_mcp_tools(tools_list: list[dict] | None, mcp: McpManager | None) -> list[dict] | None:
    if not mcp:
        return tools_list
    mcp_tools = mcp.get_tools()
    if mcp_tools:
        merged = (tools_list or []) + mcp_tools
        logger.info("MCP tools available: %d", len(mcp_tools))
        return merged
    return tools_list


def inject_thread_context(tool_calls: list[dict], thread_id: str, thread_subject: str) -> None:
    for tc in tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name")
        if name not in _THREAD_AWARE_TOOLS:
            continue
        try:
            args = json.loads(fn.get("arguments", "{}"))
            args["_thread_id"] = thread_id
            args["_thread_subject"] = thread_subject
            fn["arguments"] = json.dumps(args)
        except json.JSONDecodeError:
            pass


def handle_tool_calls(response: LLMResponse, skills: SkillRegistry, mcp: McpManager | None = None) -> list[dict]:
    sources: list[dict] = []
    combined = response.content or ""
    for tc in response.tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name", "?")
        args_str = fn.get("arguments", "{}")
        try:
            args = json.loads(args_str)
        except json.JSONDecodeError:
            args = {}

        logger.info("TOOL >>> %s(%s)", name, args_str[:300])
        t0 = time.time()
        try:
            if name.startswith("mcp__") and mcp:
                result = mcp.call_tool(name, args)
            else:
                result = skills.execute(name, **args)
        except Exception as e:
            result = f"Error: {e}"
            logger.error("TOOL <<< %s FAILED: %s", name, e)

        dt = time.time() - t0
        result_str = str(result)
        logger.info("TOOL <<< %s returned %d chars in %.2fs", name, len(result_str), dt)
        logger.info("TOOL result preview: %.200s", result_str)

        combined += f"\n\n[Tool {name} returned: {result_str}]"

        sources.append({
            "tool": name,
            "args": args,
            "result_snippet": result_str[:500],
            "took_s": round(dt, 2),
        })

    response.content = combined
    return sources


def polish_response(llm: OpenRouterClient, response: LLMResponse, model: str) -> LLMResponse:
    if "[Tool" not in response.content:
        return response
    try:
        polished = llm.chat(
            messages=[
                {"role": "system", "content": "Write a clean, natural response based on the information below. Ignore raw tool outputs."},
                {"role": "user", "content": f"Raw data:\n\n{response.content[:8000]}\n\nWrite a clean response."},
            ],
            model=model,
            max_tokens=2048,
            temperature=0.5,
        )
        if polished.content:
            response.content = polished.content
    except Exception:
        pass
    return response


class AgentRunner:
    def __init__(self, llm: OpenRouterClient, skills: SkillRegistry | None, mcp: McpManager | None) -> None:
        self.llm = llm
        self.skills = skills
        self.mcp = mcp
        self.last_error: str = ""

    def tools_for(self, agent: AgentProfile) -> list[dict] | None:
        tools = self.skills.get_tools_for_agent(agent.skills) if self.skills else None
        return merge_mcp_tools(tools, self.mcp)

    def call(self, model: str, messages: list[dict], tools: list[dict] | None) -> LLMResponse | None:
        logger.info("LLM >>> model=%s tokens=%d tools=%d | sending...",
                     model, count_messages_tokens(messages), len(tools or []))
        t0 = time.time()
        try:
            response = self.llm.chat(messages, tools=tools, model=model)
        except InsufficientCreditsError:
            raise
        except Exception as e:
            self.last_error = str(e)
            logger.error("LLM <<< FAILED after %.1fs: %s", time.time() - t0, e)
            return None
        dt = time.time() - t0
        usage = response.usage or {}
        logger.info("LLM <<< model=%s tok_in=%s tok_out=%s tool_calls=%d finish=%s %.1fs",
                     response.model, usage.get("prompt_tokens", "?"), usage.get("completion_tokens", "?"),
                     len(response.tool_calls), response.finish_reason, dt)
        logger.info("LLM says: %.200s", response.content or "(empty)")
        return response

    def run(
        self,
        agent: AgentProfile,
        messages: list[dict],
        tools: list[dict] | None,
        *,
        max_rounds: int,
        thread_id: str | None = None,
        thread_subject: str = "",
        recovery_prompt: str | None = None,
        should_stop: Callable[[], bool] | None = None,
        polish: bool = True,
    ) -> RunResult:
        model = agent.model
        t0 = time.time()

        response = self.call(model, messages, tools)
        if response is None:
            return RunResult(content="", failed=True, model=model, error=self.last_error)

        all_sources: list[dict] = []
        for _round in range(1, max_rounds):
            if should_stop is not None and should_stop():
                break
            if response.tool_calls and tools:
                if thread_id:
                    inject_thread_context(response.tool_calls, thread_id, thread_subject)
                round_sources = handle_tool_calls(response, self.skills, mcp=self.mcp)
                all_sources.extend(round_sources)
                messages = messages + [
                    {"role": "assistant", "content": response.content or ""},
                    {"role": "user", "content": CONTINUE_PROMPT},
                ]
                response = self.call(model, messages, tools)
                if response is None:
                    return RunResult(content="", sources=all_sources, model=model, failed=True, error=self.last_error)
            else:
                break

        if not response.content and recovery_prompt:
            logger.info("LLM empty after tool rounds — forcing final response without tools")
            recovery = self.llm.chat(
                messages=messages + [{"role": "user", "content": recovery_prompt}],
                model=model,
                max_tokens=4096,
            )
            if recovery and recovery.content:
                response = recovery

        if polish and response.content and "[Tool" in response.content:
            response = polish_response(self.llm, response, model)

        return RunResult(
            content=response.content or "",
            sources=all_sources,
            model=response.model,
            usage=response.usage,
            took_s=round(time.time() - t0, 1),
        )
