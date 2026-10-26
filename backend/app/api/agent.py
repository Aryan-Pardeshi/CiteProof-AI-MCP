import asyncio
import json
import logging
import re
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

# Resolved per call, not bound at import: lifespan recompiles the graph with the
# SQLite checkpointer, and an import-time binding would pin the in-memory one.
from backend.app.graph.builder import get_research_graph
from backend.app.report_guard import deepsearch_report_is_valid

logger = logging.getLogger(__name__)

THREAD_ID_PATTERN = re.compile(r"[a-zA-Z0-9\-_]{1,64}")


def _validate_thread_id(thread_id: str) -> str:
    if not thread_id or not THREAD_ID_PATTERN.fullmatch(thread_id):
        raise HTTPException(status_code=400, detail="Invalid thread ID format")
    return thread_id


# Bounds on request fields: these texts go into LLM prompts paid for by the
# server's key, and FastAPI parses the body before any handler runs.
MAX_QUERY_CHARS = 4000
MAX_MESSAGE_CHARS = 4000
MAX_THREAD_ID_CHARS = 100


class ResearchStartRequest(BaseModel):
    query: str = Field(max_length=MAX_QUERY_CHARS)
    search_topic: list[Literal["all", "news", "academic", "finance", "patent"]] = Field(
        default_factory=lambda: ["all"], max_length=5
    )


class ResearchApproveRequest(BaseModel):
    thread_id: str = Field(max_length=MAX_THREAD_ID_CHARS)
    message: str = Field(max_length=MAX_MESSAGE_CHARS)


class CancelRequest(BaseModel):
    thread_id: str = Field(max_length=MAX_THREAD_ID_CHARS)


# Track active tasks per thread_id
active_tasks = {}


router = APIRouter()

@router.post("/research/start")
async def run_research(request: ResearchStartRequest):
    id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": id}}

    try:
        # Run the graph stream. Since astream is an async generator, we consume it directly.
        # The graph will run until it hits the interrupt/pause state.
        async for event in get_research_graph().astream(
            {"query": request.query, "search_topic": request.search_topic}, config=config
        ):
            pass

        # Reads the persisted snapshot from MemorySaver for that thread_id.
        state = await get_research_graph().aget_state(config)

        # Values from current state (not yet persisted)
        plan = state.values.get("plan")
        status = state.values.get("status")
        ps = state.values.get("ps")
        error = state.values.get("error")

        return {"thread_id": id, "ps": ps, "plan": plan, "status": status, "error": error}
    except Exception:
        logger.exception("Error during run_research")
        return {
            "thread_id": id,
            "ps": None,
            "plan": None,
            "status": "error",
            "error": "Failed to start research. Check the server logs for details.",
        }


# SSE
@router.post("/research/approve")
async def approve_plan(request: ResearchApproveRequest):
    thread_id = _validate_thread_id(request.thread_id)
    config = {"configurable": {"thread_id": thread_id}}

    async def event_generator():
        current_task = asyncio.current_task()
        active_tasks[thread_id] = current_task
        try:
            try:
                # 1. Notify the frontend immediately that we are resuming graph execution for this thread
                yield f"data: {json.dumps({'event': 'resume', 'thread_id': thread_id})}\n\n"
                # This is a workaround for SSE over HTTP
                await asyncio.sleep(0.01)
                
                researcher_tasks = {}
                researcher_count = 0
                researcher_run_to_task = {}
                
                # 2. # Stream the graph events using the astream_events engine
                #For aggrigator node streaming
                # We use `astream_events(version="v2")` to capture both LLM token streams and node completions.
                async for event in get_research_graph().astream_events(
                    Command(resume={"message": request.message}),
                    config=config,
                    version="v2",
                ):
                    event_type = event["event"]
                    
                    # Capture when any node starts running
                    if event_type == "on_chain_start":
                        node_name = event.get("metadata", {}).get("langgraph_node")
                        if node_name and event.get("name") == node_name and not node_name.startswith("__"):
                            current_node_name = node_name
                            # If it is a researcher node, assign it a number
                            if node_name == "researcher":
                                query = event["data"].get("input", {}).get("query")
                                if query not in researcher_tasks:
                                    researcher_count += 1
                                    researcher_tasks[query] = f"researcher_{researcher_count}"
                                current_node_name = researcher_tasks[query]
                                # Map the run ID to the research task query
                                researcher_run_to_task[event["run_id"]] = query
                                
                            payload = {
                                "event": "node_start",
                                "node": current_node_name,
                            }
                            if node_name == "researcher":
                                payload["task"] = event["data"].get("input", {}).get("query")
                            yield f"data: {json.dumps(payload)}\n\n"
                            await asyncio.sleep(0.01)
                    
                    # Capture tool execution starts and ends
                    elif event_type == "on_tool_start" and event.get("name") == "search_web":
                        parent_run_id = event.get("parent_run_id") or event.get("metadata", {}).get("parent_run_id")
                        task = researcher_run_to_task.get(parent_run_id)
                        if task:
                            tool_input = event["data"].get("input", {})
                            search_query = tool_input.get("query")
                            payload = {
                                "event": "researcher_search",
                                "task": task,
                                "query": search_query,
                                "status": "start"
                            }
                            yield f"data: {json.dumps(payload)}\n\n"
                            await asyncio.sleep(0.01)

                    elif event_type == "on_tool_end" and event.get("name") == "search_web":
                        parent_run_id = event.get("parent_run_id") or event.get("metadata", {}).get("parent_run_id")
                        task = researcher_run_to_task.get(parent_run_id)
                        if task:
                            payload = {
                                "event": "researcher_search",
                                "task": task,
                                "status": "completed"
                            }
                            yield f"data: {json.dumps(payload)}\n\n"
                            await asyncio.sleep(0.01)

                    # Stream individual LLM tokens 
                    elif event_type == "on_chat_model_stream":
                        metadata = event.get("metadata", {})
                        
                        if metadata.get("langgraph_node") == "aggregator":
                            chunk = event["data"].get("chunk")
                            if chunk and hasattr(chunk, "content") and chunk.content:
                                payload = {
                                    "event": "aggregator_token",
                                    "token": chunk.content,
                                }
                                yield f"data: {json.dumps(payload)}\n\n"
                                # Small sleep to prevent network congestion
                                await asyncio.sleep(0.001)
                                
                    # Capture node completion updates 
                    elif event_type == "on_chain_end":
                        node_name = event.get("metadata", {}).get("langgraph_node")
                        # In v2, the top-level node completion matches the node's name in the event
                        if node_name and event.get("name") == node_name:
                            # Skip LangGraph's internal/helper nodes (which start with double underscores)
                            if node_name.startswith("__"):
                                continue
                            
                            current_node_name = node_name
                            
                            if node_name == "researcher":
                                query = event["data"].get("input", {}).get("query")
                                if query in researcher_tasks:
                                    current_node_name = researcher_tasks[query]
                                else:
                                    researcher_count += 1
                                    researcher_tasks[query] = f"researcher_{researcher_count}"
                                    current_node_name = researcher_tasks[query]
                            
                            node_output = event["data"].get("output")
                            payload = {
                                "event": "node_update",
                                "node": current_node_name,
                                "data": node_output,
                            }
                            # Attach the matching sub-task query to the end event
                            if node_name == "researcher":
                                payload["task"] = event["data"].get("input", {}).get("query")
                                
                            yield f"data: {json.dumps(payload)}\n\n"
                            await asyncio.sleep(0.01)

                # 3. Graph execution is finished. Retrieve the final state snapshot to send the final report/citations.
                state = await get_research_graph().aget_state(config)

                final_answer = state.values.get("final_answer")
                citations = state.values.get("citations") or []
                status = state.values.get("status")
                # A safety verdict or an empty stub is not a report: report an
                # error instead of "completed" so the UI never shows junk.
                report_rejected = status == "completed" and not deepsearch_report_is_valid(
                    final_answer,
                    citations=citations,
                )

                if report_rejected:
                    logger.warning(
                        "DeepSearch finished without a usable report; failing the run"
                    )
                    final_payload = {
                        "event": "error",
                        "thread_id": request.thread_id,
                        "message": (
                            "The report could not be generated because the model "
                            "returned incomplete or uncited output. Please try again."
                        ),
                    }
                else:
                    final_payload = {
                        "event": (
                            "completed"
                            if status == "completed"
                            else "awaiting_approval"
                        ),
                        "thread_id": request.thread_id,
                        "plan_approved": state.values.get("plan_approved"),
                        "status": status,
                        "ps": state.values.get("ps"),
                        "plan": state.values.get("plan"),
                        "final_answer": final_answer,
                        "citations": citations,
                    }
                # Send the complete final state payload to mark completion
                yield f"data: {json.dumps(final_payload)}\n\n"

            except Exception:
                logger.exception("Error while streaming research results")
                message = "Research failed. Check the server logs for details."
                yield f"data: {json.dumps({'event': 'error', 'message': message})}\n\n"
        finally:
            if active_tasks.get(request.thread_id) == current_task:
                active_tasks.pop(request.thread_id, None)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.get("/research/result/{thread_id}")
async def get_result(thread_id: str):
    valid_id = _validate_thread_id(thread_id)
    config = {"configurable": {"thread_id": valid_id}}
    state = await get_research_graph().aget_state(config)
    if not state.values:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {
        "ps": state.values.get("ps"),
        "final_answer": state.values.get("final_answer"),
        "citations": state.values.get("citations"),
        "status": state.values.get("status"),
    }


@router.post("/research/cancel")
async def cancel_research(request: CancelRequest):
    valid_id = _validate_thread_id(request.thread_id)
    task = active_tasks.get(valid_id)
    if task:
        task.cancel()
        return {"status": "success", "message": f"Cancelled research for thread {valid_id}"}
    return {"status": "not_running", "message": f"No active research found for thread {valid_id}"}
