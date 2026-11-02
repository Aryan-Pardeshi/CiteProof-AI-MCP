# Worker agent (reused for each sub-question)
import sys
from pathlib import Path

# Add workspace root to sys.path so 'backend' is importable
root_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import concurrent.futures
import logging

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from backend.app.graph.state import ResearchState
from backend.app.llm import llm
from backend.app.tools.web_search import search_web

logger = logging.getLogger(__name__)

LLM_TIMEOUT = 90

def _invoke_with_timeout(agent_or_llm, messages, timeout=LLM_TIMEOUT):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(agent_or_llm.invoke, messages)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.error(f"LLM invoke timed out after {timeout}s")
            raise TimeoutError(f"LLM call timed out after {timeout}s")

SYSTEM_PROMPT = """You are an elite, highly-analytical research analyst with access to real-time web search.
Your objective is to thoroughly investigate the assigned research task by formulating precise search queries and critically evaluating the results.

### Your Search Workflow
1. **Analyze the Task**: Determine the core concepts, temporal constraints (e.g., specific years or recent dates), and information gaps.
2. **Formulate Queries**:
   - Construct queries using dense, information-rich keywords (avoid natural language sentences where keywords are more effective).
   - If the task mentions a specific timeframe, include relevant years or dates in your queries.
3. **Execute and Refine**:
   - Call the `search_web` tool.
   - Use the `time_range` parameter ONLY if the task specifies a recent timeframe (e.g., "today", "this week", "recently"):
     * `"day"` for news within the last 24 hours.
     * `"week"` for updates from the last 7 days.
     * `"month"` for events in the last 30 days.
     * `"year"` for events in the last 12 months.
     * Do not specify `time_range` (leave as None) for historical or general queries.
   - Limit yourself to a maximum of 3 search iterations.
4. **Evaluate Critically**:
   - Assess search results for credibility, relevance, and completeness.
   - If results are insufficient or ambiguous, refine your search terms and query again.
5. **Conclude**: Once you have gathered sufficient high-quality facts to fully address the task, stop calling tools.

### Constraints & Rigor
- **Factual Grounding**: Every fact, number, and claim you output must be strictly backed by the retrieved search results. Do not speculate or make assumptions.
- **Conciseness**: Keep summaries structured, dense, and under 500 - 700 tokens.
- **Citations**: Track all source URLs from search results so they can be cited in your final response.
"""

SYNTHESIS_PROMPT = """Using all search results gathered above, write your final research report.

You MUST respond in valid JSON with this exact schema:
{{
  "result": "<comprehensive research summary based only on search results>",
  "citations": ["<url1>", "<url2>", ...]
}}

Only include URLs that appeared in the search results."""


class ResearchResult(BaseModel):
    result: str = Field(description="Comprehensive research summary based on search results")
    citations: list[str] = Field(description="List of source URLs from search results")


def researcher_node(state: ResearchState) -> dict:
    import random
    import time
    query = state["query"]
    logger.info(f"Researcher starting for query: {query}")

    # Stagger execution of concurrent researcher nodes to reduce API rate limit spikes
    stagger_time = random.uniform(0.5, 3.0)
    logger.info(f"Staggering researcher start by {stagger_time:.2f}s for: {query}")
    time.sleep(stagger_time)

    try:
        # Single agent: same LLM instance used for both tool calling and synthesis
        agent = llm.bind_tools([search_web])

        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=f"Research Task: {query}")
        ]

        max_steps = 10
        search_count = 0

        for step in range(max_steps):
            response = _invoke_with_timeout(agent, messages)
            messages.append(response)

            # No tool calls → LLM is done searching, move to synthesis
            if not response.tool_calls:
                logger.info(f"Researcher completed search in {step + 1} steps with {search_count} searches")
                break

            # Execute each requested tool call
            for tool_call in response.tool_calls:
                if tool_call["name"] == "search_web":
                    args = tool_call["args"]
                    
                    # Inject search_topic from the active graph state if not already set by the LLM
                    if "search_topic" not in args and "search_topic" in state:
                        args["search_topic"] = state["search_topic"]
                    
                    logger.info(f"Executing search: query='{args.get('query')}', search_topic={args.get('search_topic')}, time_range={args.get('time_range')}")

                    try:
                        tool_output = search_web.invoke(args)
                        search_count += 1
                    except Exception as e:  # noqa: BLE001
                        tool_output = f"Search failed: {e!s}"
                        logger.warning(f"Search failed: {e}")

                    messages.append(
                        ToolMessage(
                            content=str(tool_output),
                            tool_call_id=tool_call["id"],
                            name=tool_call["name"]
                        )
                    )
                else:
                    logger.warning(f"Unknown tool call: {tool_call['name']}")
                    messages.append(ToolMessage(content=f"Unknown tool {tool_call['name']}", tool_call_id=tool_call["id"]))

        # Synthesis step: use plain llm (no tools) — just synthesize the gathered results
        messages.append(HumanMessage(content=SYNTHESIS_PROMPT))
        final_response = _invoke_with_timeout(llm, messages)

        try:
            import json
            raw = final_response.content.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                raw = raw.removeprefix("json")
            data = json.loads(raw)
            final_result = ResearchResult(**data)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Structured output parsing failed: {e}")
            return {"results": [final_response.content], "citations": []}

        logger.info(f"Researcher completed. Citations: {len(final_result.citations)}")
        return {
            "results": [final_result.result],
            "citations": final_result.citations
        }
    except Exception as e:
        logger.exception("Error in researcher node for task '%s'", query)
        return {
            "results": [f"Research failed for this task due to an error: {e!s}"],
            "citations": []
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_state = {
        "query": "What are the latest breakthroughs in fusion energy in 2026?",
        "plan": [],
        "plan_approved": False,
        "user_feedback": None,
        "status": "researching",
        "results": [],
        "final_answer": None,
        "citations": []
    }
    print("Testing researcher_node...")
    output = researcher_node(test_state)
    print("\n--- Output ---")
    print("Result:", output["results"][0][:500])
    print("Citations:", output["citations"])
