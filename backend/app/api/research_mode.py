import asyncio
import json
import logging
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from backend.app.agents.research_mode.writing import EV_MARKER_RE

# Resolved lazily rather than importing the compiled graph: lifespan recompiles it
# with the SQLite checkpointer, and a module-level binding would keep the old object.
from backend.app.graph.research_mode_builder import get_research_mode_graph
from backend.app.report_guard import research_paper_is_valid
from backend.app.stats import get_paper_count, increment_paper_count
from backend.app.tools.docx_generator import generate_paper_docx
from backend.app.tools.pdf_generator import generate_paper_pdf

logger = logging.getLogger(__name__)

REPORT_REJECTED_MESSAGE = (
    "The report could not be generated because the model returned "
    "incomplete or uncited output. Please try again."
)

router = APIRouter()

THREAD_ID_PATTERN = re.compile(r"[a-zA-Z0-9\-_]{1,64}")


def _sanitize_node_output(value: Any) -> Any:
    """Mask [EV:<id>] provenance tags on the live event path.

    claims_linker strips these markers before final rendering, but writer nodes
    stream raw output long before that pass runs; without this, unresolved tags
    are visible to the user during live streaming.
    """
    if isinstance(value, str):
        return re.sub(r"[ \t]{2,}", " ", EV_MARKER_RE.sub("", value))
    if isinstance(value, dict):
        return {k: _sanitize_node_output(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_node_output(v) for v in value]
    return value


def _validate_thread_id(thread_id: str) -> str:
    if not thread_id or not THREAD_ID_PATTERN.fullmatch(thread_id):
        raise HTTPException(status_code=400, detail="Invalid thread ID format")
    return thread_id

# Active streaming tasks per thread_id
active_research_tasks: dict[str, asyncio.Task] = {}

# Per-thread event buffer for SSE reconnects
# Structure: {thread_id: {"events": [], "task": task, "updated_at": timestamp, "completed": bool, "listeners": set()}}
thread_buffers: dict[str, dict[str, Any]] = {}

BUFFER_TTL_SECONDS = 3600  # 1 hour TTL for inactive thread buffers


def prune_old_buffers():
    now = time.time()
    to_delete = []
    for tid, buf in list(thread_buffers.items()):
        task = buf.get("task")
        if (task is None or task.done()) and (now - buf.get("updated_at", 0) > BUFFER_TTL_SECONDS):
            to_delete.append(tid)
    for tid in to_delete:
        thread_buffers.pop(tid, None)
        active_research_tasks.pop(tid, None)


# Bounds on request fields: these texts go into LLM prompts paid for by the
# server's key, and FastAPI parses the body before any handler runs.
MAX_PROBLEM_CHARS = 4000
MAX_LIST_ITEMS = 10
MAX_ITEM_CHARS = 1000
MAX_MESSAGE_CHARS = 4000
MAX_THREAD_ID_CHARS = 100
BoundedItem = Annotated[str, Field(max_length=MAX_ITEM_CHARS)]
ModelName = Annotated[str, Field(max_length=200)]


class ResearchModeStartRequest(BaseModel):
    problem_statement: str = Field(max_length=MAX_PROBLEM_CHARS)
    research_objectives: list[BoundedItem] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    research_questions: list[BoundedItem] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    models: dict[ModelName, ModelName] = Field(default_factory=dict, max_length=MAX_LIST_ITEMS)


class ResearchModeApproveRequest(BaseModel):
    thread_id: str = Field(max_length=MAX_THREAD_ID_CHARS)
    message: str = Field(default="", max_length=MAX_MESSAGE_CHARS)
    from_seq: int | None = None


class ResearchModeCancelRequest(BaseModel):
    thread_id: str = Field(max_length=MAX_THREAD_ID_CHARS)


async def _broadcast_event(thread_id: str, payload: dict[str, Any], is_node_event: bool = True):
    buf = thread_buffers.get(thread_id)
    if not buf:
        return
    buf["updated_at"] = time.time()
    if is_node_event:
        seq = len(buf["events"]) + 1
        payload["seq"] = seq
        buf["events"].append(payload)

    # Broadcast copy to active listeners
    listeners = list(buf.get("listeners", set()))
    for q in listeners:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


def _resolve_checkpoint_state(state) -> tuple[bool, str | None]:
    """Accurately resolves whether the graph is at an HITL checkpoint, and which checkpoint."""
    if not state:
        return False, None
    values = state.values or {}

    # 1. Check tasks and interrupts
    pending_interrupt = None
    if getattr(state, "interrupts", None):
        pending_interrupt = state.interrupts[0]
    elif getattr(state, "tasks", None):
        for t in state.tasks:
            if getattr(t, "interrupts", None):
                pending_interrupt = t.interrupts[0]
                break

    if pending_interrupt is not None:
        val = getattr(pending_interrupt, "value", None)
        if isinstance(val, dict) and val.get("checkpoint"):
            return True, val.get("checkpoint")
        elif isinstance(val, str) and val.startswith("checkpoint_"):
            return True, val

    # 2. Check state.next for checkpoint nodes
    if getattr(state, "next", None):
        for n in state.next:
            if n in ("checkpoint_1", "checkpoint_2", "checkpoint_3"):
                return True, n

    # 3. Check values["hitl_checkpoint"] or values["status"]
    cp = values.get("hitl_checkpoint")
    if cp and not cp.endswith("_approved") and cp in ("checkpoint_1", "checkpoint_2", "checkpoint_3"):
        return True, cp

    # No genuine interrupt found by any of the checks above: the graph is either
    # actively executing a non-checkpoint node right now, or it's finished. Either
    # way, it is NOT paused, and must not be reported as one.
    #
    # A fourth branch used to infer "paused at checkpoint N" from which result
    # fields happened to be non-empty (e.g. hypotheses present -> "must be at
    # checkpoint_3"). That heuristic is unsound: fields accumulate monotonically
    # and are never cleared, so it fired identically whether the graph was
    # genuinely paused OR actively mid-run between checkpoints - e.g. while
    # paper_screener or the hypotheses agent was still running, with no interrupt
    # pending at all. Reload during that window and every checkpoint_1_node's
    # actual interrupt() call (see research_mode_builder.py) already reports the
    # correct checkpoint via branch 1 above; there's no real case this branch
    # covers that branch 1 doesn't. Removing it fixes reload showing a stale,
    # seemingly-random checkpoint number with no live progress: the frontend was
    # trusting this false "paused" signal to skip reconnecting to the SSE stream.
    return False, None


async def _execute_research_graph(thread_id: str, message: str):
    config = {"configurable": {"thread_id": thread_id}}
    graph = get_research_mode_graph()
    buf = thread_buffers.get(thread_id)
    if buf:
        buf["completed"] = False

    try:
        resume_payload = {
            "event": "resume",
            "thread_id": thread_id
        }
        await _broadcast_event(thread_id, resume_payload, is_node_event=True)

        async for event in graph.astream_events(
            Command(resume={"message": message}),
            config=config,
            version="v2",
        ):
            event_type = event["event"]

            # Node execution starts
            if event_type == "on_chain_start":
                node_name = event.get("metadata", {}).get("langgraph_node")
                if node_name and event.get("name") == node_name and not node_name.startswith("__"):
                    payload = {
                        "event": "node_start",
                        "node": node_name,
                        "thread_id": thread_id
                    }
                    await _broadcast_event(thread_id, payload, is_node_event=True)

            # LLM token streaming (live only, NOT stored in buffer)
            elif event_type == "on_chat_model_stream":
                chunk = event["data"].get("chunk")
                node_name = event.get("metadata", {}).get("langgraph_node")
                if chunk and hasattr(chunk, "content") and chunk.content and node_name:
                    payload = {
                        "event": "token_stream",
                        "node": node_name,
                        "token": chunk.content
                    }
                    await _broadcast_event(thread_id, payload, is_node_event=False)

            # Node completion updates
            elif event_type == "on_chain_end":
                node_name = event.get("metadata", {}).get("langgraph_node")
                if node_name and event.get("name") == node_name and not node_name.startswith("__"):
                    node_output = _sanitize_node_output(event["data"].get("output"))
                    payload = {
                        "event": "node_update",
                        "node": node_name,
                        "data": node_output
                    }
                    await _broadcast_event(thread_id, payload, is_node_event=True)

        # Retrieve state after stream pause / completion
        state = await graph.aget_state(config)
        values = state.values or {} if state else {}

        # Check if paused at next interrupt or finished
        is_completed = values.get("status") == "completed" or not (state and state.next)
        is_checkpoint, hitl_checkpoint = _resolve_checkpoint_state(state)

        # A "completed" graph with no written sections or references is junk, not
        # a paper: report an error instead of a success.
        report_rejected = is_completed and not research_paper_is_valid(values)

        # Determine the correct terminal event:
        # - "error" if the finished paper failed the report guard
        # - "completed" if the graph finished
        # - "checkpoint" if genuinely paused at an HITL interrupt
        # - "running" if the graph is actively executing (no interrupt, not finished)
        #   In this case we don't send a terminal event; the stream simply ends
        #   and the frontend will reconnect via pipeline-status polling.
        if report_rejected:
            event_name = "error"
        elif is_completed:
            event_name = "completed"
        elif is_checkpoint:
            event_name = "checkpoint"
        else:
            # Graph is running but not at a checkpoint and not completed.
            # This can happen if the stream was cut mid-execution.
            # Don't send a terminal event; let the frontend handle reconnection.
            event_name = None

        final_payload = {
            "event": event_name,
            "thread_id": thread_id,
            "status": values.get("status"),
            "hitl_checkpoint": hitl_checkpoint or values.get("hitl_checkpoint"),
            "state": {
                "problem_statement": values.get("problem_statement"),
                "research_objectives": values.get("research_objectives"),
                "research_questions": values.get("research_questions"),
                "keywords": values.get("keywords"),
                "search_protocol": values.get("search_protocol"),
                "raw_papers_count": (
                    values.get("prisma_tracker", {}).get("records_identified")
                    if isinstance(values.get("prisma_tracker"), dict) and "records_identified" in values.get("prisma_tracker")
                    else len(values.get("raw_papers") or values.get("paper_records") or [])
                ),
                "screened_papers_count": (
                    values.get("prisma_tracker", {}).get("studies_included")
                    if isinstance(values.get("prisma_tracker"), dict) and "studies_included" in values.get("prisma_tracker")
                    else len(values.get("screened_papers") or values.get("paper_records") or [])
                ),
                "evidence_records_count": len(values.get("evidence_records") or []),
                "evidence_records": values.get("evidence_records") or [],
                "evidence_spans": values.get("evidence_spans") or [],
                "claims": values.get("claims") or [],
                "review_claims": values.get("review_claims") or [],
                "prisma_tracker": values.get("prisma_tracker"),
                "taxonomy": values.get("taxonomy"),
                "validation_report": values.get("validation_report"),
                "literature_review": values.get("literature_review"),
                "research_gap": values.get("research_gap"),
                "conceptual_framework": values.get("conceptual_framework"),
                "hypotheses": values.get("hypotheses"),
                "research_design": values.get("research_design"),
                "data_collection_plan": values.get("data_collection_plan"),
                "data_analysis_plan": values.get("data_analysis_plan"),
                "results": values.get("results"),
                "discussion": values.get("discussion"),
                "implications": values.get("implications"),
                "limitations": values.get("limitations"),
                "conclusion": values.get("conclusion"),
                "future_scope": values.get("future_scope"),
                "references": values.get("references"),
                "appendices": values.get("appendices"),
                "introduction": values.get("introduction"),
                "abstract": values.get("abstract"),
                "title": values.get("title")
            }
        }
        if report_rejected:
            logger.warning(
                "Research Mode finished without a usable paper; failing the run"
            )
            final_payload["message"] = REPORT_REJECTED_MESSAGE
        if event_name is not None:
            await _broadcast_event(thread_id, final_payload, is_node_event=True)
        if buf:
            buf["completed"] = is_completed
            buf["report_rejected"] = report_rejected

    except Exception:
        logger.exception("SSE Error in Research Mode approve")
        error_payload = {
            "event": "error",
            "message": "Research Mode run failed. Check the server logs for details."
        }
        await _broadcast_event(thread_id, error_payload, is_node_event=True)
        if buf:
            buf["completed"] = True
    finally:
        current_task = asyncio.current_task()
        if active_research_tasks.get(thread_id) == current_task:
            active_research_tasks.pop(thread_id, None)
        if buf:
            listeners = list(buf.get("listeners", set()))
            for q in listeners:
                try:
                    q.put_nowait(None)
                except asyncio.QueueFull:
                    pass


@router.post("/research-mode/start")
@router.post("/research/mode/start")
async def start_research_mode(request: ResearchModeStartRequest):
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    graph = get_research_mode_graph()

    initial_state = {
        "thread_id": thread_id,
        "mode": "research",
        "problem_statement": request.problem_statement,
        "research_objectives": request.research_objectives or [],
        "research_questions": request.research_questions or [],
        "keywords": [],
        "raw_papers": [],
        "screened_papers": [],
        "model_overrides": request.models or {},
        "status": "initializing"
    }

    try:
        # Run graph until first interrupt (Checkpoint 1)
        async for event in graph.astream(initial_state, config=config):
            pass

        state = await graph.aget_state(config)
        values = state.values or {}

        # Count this as one "paper" launched. Best-effort: a stats-DB failure must
        # never block or fail the research run itself.
        papers_total = 0
        try:
            papers_total = await increment_paper_count()
        except Exception:
            logger.warning("Failed to increment paper counter", exc_info=True)

        return {
            "thread_id": thread_id,
            "problem_statement": values.get("problem_statement"),
            "research_objectives": values.get("research_objectives"),
            "research_questions": values.get("research_questions"),
            "keywords": values.get("keywords"),
            "hitl_checkpoint": values.get("hitl_checkpoint", "checkpoint_1"),
            "status": values.get("status", "awaiting_approval"),
            "papers_total": papers_total,
            "error": values.get("error")
        }
    except Exception:
        logger.exception("Error starting Research Mode")
        return {
            "thread_id": thread_id,
            "status": "error",
            "error": "Failed to start Research Mode. Check the server logs for details."
        }


@router.get("/research-mode/total-papers")
@router.get("/research/mode/total-papers")
async def total_papers():
    """Cumulative count of research-mode papers launched (live display)."""
    try:
        return {"total_papers": await get_paper_count()}
    except Exception:  # noqa: BLE001
        return {"total_papers": 0}


@router.post("/research-mode/approve")
@router.post("/research/mode/approve")
async def approve_research_mode(
    request: ResearchModeApproveRequest,
    last_event_id: str | None = Header(None, alias="Last-Event-ID")
):
    prune_old_buffers()

    from_seq = request.from_seq
    if from_seq is None and last_event_id is not None:
        try:
            from_seq = int(last_event_id)
        except ValueError:
            pass

    thread_id = _validate_thread_id(request.thread_id)
    if thread_id not in thread_buffers:
        thread_buffers[thread_id] = {
            "events": [],
            "task": None,
            "updated_at": time.time(),
            "completed": False,
            "listeners": set()
        }

    buf = thread_buffers[thread_id]

    task = buf.get("task")
    need_new_task = False
    if task is None or task.done():
        events_count = len(buf.get("events", []))
        if not buf.get("completed") or from_seq is None or from_seq >= events_count:
            need_new_task = True

    if need_new_task:
        new_task = asyncio.create_task(_execute_research_graph(thread_id, request.message))
        buf["task"] = new_task
        active_research_tasks[thread_id] = new_task

    async def event_generator():
        listener_queue = asyncio.Queue()
        buf["listeners"].add(listener_queue)

        try:
            # Replay missed buffered events (start from seq 0 if from_seq is None)
            start_seq = 0 if from_seq is None else from_seq
            missed = [e for e in buf.get("events", []) if e.get("seq", 0) > start_seq]
            for evt in missed:
                seq_str = f"id: {evt['seq']}\n" if "seq" in evt else ""
                yield f"{seq_str}data: {json.dumps(evt)}\n\n"
                await asyncio.sleep(0.001)

            # PROXY ERROR HANDLING:
            # Stream live events; send SSE comment heartbeats every 20s so that
            # a hosting proxy's idle-connection timeout (often ~60-90s) is never triggered
            # during long LLM calls where no data frames flow for minutes at a time.
            HEARTBEAT_INTERVAL = 20.0
            task_ref = buf.get("task")
            while True:
                try:
                    evt = await asyncio.wait_for(listener_queue.get(), timeout=HEARTBEAT_INTERVAL)
                    if evt is None:
                        break
                    seq_str = f"id: {evt['seq']}\n" if "seq" in evt else ""
                    yield f"{seq_str}data: {json.dumps(evt)}\n\n"
                    await asyncio.sleep(0.001)
                except TimeoutError:
                    if (task_ref is None or task_ref.done()) and listener_queue.empty():
                        break
                    # PROXY ERROR HANDLING:
                    # Send SSE comment heartbeat (': heartbeat\n\n') — reverse proxies
                    # forward these bytes to keep the TCP connection alive without breaking event streams.
                    yield ": heartbeat\n\n"

        finally:
            buf["listeners"].discard(listener_queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# PROXY ERROR HANDLING:
# Lightweight endpoint allowing frontend to poll pipeline health if the host's proxy cuts the SSE stream.
@router.get("/research-mode/pipeline-status/{thread_id}")
@router.get("/research/mode/pipeline-status/{thread_id}")
async def get_pipeline_status(thread_id: str):
    """PROXY ERROR HANDLING:
    Lightweight endpoint for the frontend to poll whether the backend task is still alive.
    Returns running=True if the asyncio task for this thread is running, and completed=True
    if it has finished. Used by the frontend when the SSE connection drops mid-execution.
    """
    valid_id = _validate_thread_id(thread_id)
    buf = thread_buffers.get(valid_id)
    if not buf:
        return {"running": False, "completed": False, "events_count": 0}
    task = buf.get("task")
    running = task is not None and not task.done()
    return {
        "running": running,
        "completed": buf.get("completed", False),
        "report_rejected": buf.get("report_rejected", False),
        "events_count": len(buf.get("events", []))
    }


@router.get("/research-mode/result/{thread_id}")
@router.get("/research/mode/result/{thread_id}")
async def get_research_mode_result(thread_id: str):
    valid_id = _validate_thread_id(thread_id)
    config = {"configurable": {"thread_id": valid_id}}
    graph = get_research_mode_graph()
    state = await graph.aget_state(config)
    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Research Mode thread not found")

    values = state.values

    is_checkpoint, hitl_checkpoint = _resolve_checkpoint_state(state)
    finished = not bool(state.next) and values.get("status") == "completed"
    # Apply the same report guard as the SSE stream, so a client that missed the
    # stream's error event cannot render a rejected paper as a success.
    report_rejected = finished and not research_paper_is_valid(values)
    is_completed = finished and not report_rejected

    result = {
        "values": values,
        "next": list(state.next) if state.next else [],
        "is_checkpoint": is_checkpoint,
        "is_completed": is_completed,
        "report_rejected": report_rejected,
        "hitl_checkpoint": hitl_checkpoint,
        "status": values.get("status")
    }
    if report_rejected:
        result["message"] = REPORT_REJECTED_MESSAGE
    return result


EXPORT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "exports"


def _remove_quietly(path: Path) -> None:
    path.unlink(missing_ok=True)


async def _export_paper(thread_id: str, generate, suffix: str, media_type: str, label: str) -> FileResponse:
    """Render a finished paper to a one-off file, send it, then delete it.

    The file name is random rather than derived from the thread id, so no request
    data reaches the filesystem path, and exports do not pile up on disk.
    """
    valid_id = _validate_thread_id(thread_id)
    config = {"configurable": {"thread_id": valid_id}}
    graph = get_research_mode_graph()
    state = await graph.aget_state(config)
    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Research Mode thread not found")

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="paper_", suffix=suffix, dir=EXPORT_DIR)
    os.close(fd)
    out_path = Path(name)

    try:
        generate(state.values, str(out_path))
    except Exception:
        _remove_quietly(out_path)
        logger.exception(f"Failed to generate {label}")
        raise HTTPException(status_code=500, detail=f"{label} generation failed. Check the server logs for details.") from None

    return FileResponse(
        path=str(out_path),
        filename=f"research_paper_{valid_id[:8]}{suffix}",
        media_type=media_type,
        background=BackgroundTask(_remove_quietly, out_path),
    )


@router.post("/research-mode/export/{thread_id}")
@router.post("/research/mode/export/{thread_id}")
async def export_research_mode_pdf(thread_id: str):
    return await _export_paper(thread_id, generate_paper_pdf, ".pdf", "application/pdf", "PDF")


@router.post("/research-mode/export/docx/{thread_id}")
@router.post("/research/mode/export/docx/{thread_id}")
async def export_research_mode_docx(thread_id: str):
    return await _export_paper(
        thread_id,
        generate_paper_docx,
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "DOCX",
    )

