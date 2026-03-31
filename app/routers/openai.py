"""
DVProxy - OpenAI API Router
Handles /v1/chat/completions and /v1/responses endpoints (OpenAI format)

Implements the full OpenAI Chat Completions API including:
- Non-streaming and streaming responses  
- Tool calls with proper streaming format
- stream_options.include_usage support
- Proper SSE format with data: prefix

Also implements the OpenAI Responses API:
- Rich event types for agentic workflows
- Function calls and tool outputs
"""
import time
import json
import uuid
from typing import Any, Dict, Optional
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import APIKey
from app.models.db import get_db
from app.services.auth import get_api_key
from app.services.converter import FormatConverter
from app.services.upstream import UpstreamClient, UpstreamError
from app.services.usage import UsageService

router = APIRouter(prefix="/v1", tags=["OpenAI"])


def generate_id(prefix: str = "") -> str:
    """Generate a unique ID"""
    return f"{prefix}{uuid.uuid4().hex[:24]}"


@router.post("/chat/completions")
async def create_chat_completion(
    request: Request,
    api_key: APIKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """OpenAI Chat Completions API endpoint
    
    Supports:
    - System, user, assistant, tool messages
    - Images (base64 data URLs)
    - Tool definitions and tool_calls
    - Streaming with stream_options.include_usage
    """
    start_time = time.time()
    body = await request.json()
    
    stream = body.get("stream", False)
    model = body.get("model", "auto")
    
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")
    
    # Generate completion ID
    completion_id = generate_id("chatcmpl-")
    
    # Convert to GenAI format
    genai_request = FormatConverter.openai_to_genai(body)
    
    client = UpstreamClient()
    
    if stream:
        return StreamingResponse(
            _stream_openai_response(
                client, genai_request, model, completion_id, api_key.id, db,
                ip_address, user_agent, start_time,
                include_usage=body.get("stream_options", {}).get("include_usage", False)
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
    else:
        try:
            genai_response = await client.chat_messages(genai_request)
            openai_response = FormatConverter.genai_to_openai(
                genai_response, model, completion_id
            )
            
            usage = genai_response.get("usageMetadata", {})
            latency_ms = int((time.time() - start_time) * 1000)
            
            await UsageService.log_usage(
                db=db,
                api_key_id=api_key.id,
                endpoint="openai",
                model=model,
                input_tokens=usage.get("promptTokenCount", 0),
                output_tokens=usage.get("candidatesTokenCount", 0),
                cached_tokens=usage.get("cacheReadInputTokens", 0),
                ip_address=ip_address,
                user_agent=user_agent,
                latency_ms=latency_ms,
                success=True
            )
            
            return openai_response
            
        except UpstreamError as e:
            latency_ms = int((time.time() - start_time) * 1000)
            await UsageService.log_usage(
                db=db,
                api_key_id=api_key.id,
                endpoint="openai",
                model=model,
                input_tokens=0,
                output_tokens=0,
                ip_address=ip_address,
                user_agent=user_agent,
                latency_ms=latency_ms,
                success=False,
                error_message=str(e)
            )
            raise HTTPException(status_code=e.status_code, detail=e.error or str(e))
        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            await UsageService.log_usage(
                db=db,
                api_key_id=api_key.id,
                endpoint="openai",
                model=model,
                input_tokens=0,
                output_tokens=0,
                ip_address=ip_address,
                user_agent=user_agent,
                latency_ms=latency_ms,
                success=False,
                error_message=str(e)
            )
            raise HTTPException(status_code=500, detail=str(e))


async def _stream_openai_response(
    client: UpstreamClient,
    genai_request: Dict[str, Any],
    model: str,
    completion_id: str,
    api_key_id: int,
    db: AsyncSession,
    ip_address: str,
    user_agent: str,
    start_time: float,
    include_usage: bool = False
):
    """Stream OpenAI Chat Completions format
    
    Chunk format:
    {
        "id": "chatcmpl-xxx",
        "object": "chat.completion.chunk",
        "created": timestamp,
        "model": "...",
        "choices": [{
            "index": 0,
            "delta": {"role": "assistant", "content": "..."} or {"content": "..."} or {"tool_calls": [...]},
            "finish_reason": null or "stop" or "tool_calls" or "length"
        }]
    }
    
    Key points:
    - First chunk MUST include "role": "assistant" in delta
    - Tool calls are streamed with incremental arguments
    - Final chunk has finish_reason set
    - If include_usage, send final chunk with usage and empty choices
    """
    created = int(time.time())
    total_input_tokens = 0
    total_output_tokens = 0
    cached_tokens = 0
    
    # Track tool calls: {index: {id, name, arguments_buffer}}
    tool_calls: Dict[int, Dict] = {}
    first_chunk_sent = False
    finish_reason_sent = False
    accumulated_text = ""
    
    try:
        async for chunk in client.chat_stream(genai_request):
            if chunk.get("type") == "connection_established":
                continue
            
            candidates = chunk.get("candidates", [])
            if not candidates:
                continue
            
            candidate = candidates[0]
            parts = candidate.get("content", {}).get("parts", [])
            genai_finish = candidate.get("finishReason")
            finish_reason = None
            
            for part in parts:
                if "text" in part:
                    text = part["text"]
                    accumulated_text += text
                    
                    delta: Dict[str, Any] = {"content": text}
                    
                    # First chunk must include role
                    if not first_chunk_sent:
                        delta["role"] = "assistant"
                        first_chunk_sent = True
                    
                    chunk_data = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{
                            "index": 0,
                            "delta": delta,
                            "logprobs": None,
                            "finish_reason": None
                        }]
                    }
                    yield f"data: {json.dumps(chunk_data)}\n\n"
                
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    call_id = fc.get("id") or generate_id("call_")
                    func_name = fc.get("name", "")
                    args = fc.get("args", {})
                    args_str = json.dumps(args)
                    
                    # Find or create tool call entry
                    tool_idx = None
                    for idx, tc in tool_calls.items():
                        if tc["id"] == call_id or tc["name"] == func_name:
                            tool_idx = idx
                            break
                    
                    if tool_idx is None:
                        tool_idx = len(tool_calls)
                        tool_calls[tool_idx] = {
                            "id": call_id,
                            "name": func_name,
                            "arguments_buffer": ""
                        }
                        
                        # Send tool call start with name
                        delta: Dict[str, Any] = {
                            "tool_calls": [{
                                "index": tool_idx,
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": func_name,
                                    "arguments": ""
                                }
                            }]
                        }
                        
                        # First chunk must include role
                        if not first_chunk_sent:
                            delta["role"] = "assistant"
                            first_chunk_sent = True
                        
                        chunk_data = {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{
                                "index": 0,
                                "delta": delta,
                                "logprobs": None,
                                "finish_reason": None
                            }]
                        }
                        yield f"data: {json.dumps(chunk_data)}\n\n"
                    
                    # Send arguments incrementally
                    prev_buffer = tool_calls[tool_idx]["arguments_buffer"]
                    if len(args_str) > len(prev_buffer):
                        new_args = args_str[len(prev_buffer):]
                        tool_calls[tool_idx]["arguments_buffer"] = args_str
                        
                        args_chunk = {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{
                                "index": 0,
                                "delta": {
                                    "tool_calls": [{
                                        "index": tool_idx,
                                        "function": {
                                            "arguments": new_args
                                        }
                                    }]
                                },
                                "logprobs": None,
                                "finish_reason": None
                            }]
                        }
                        yield f"data: {json.dumps(args_chunk)}\n\n"
            
            # Update usage
            usage = chunk.get("usageMetadata", {})
            if usage:
                total_input_tokens = usage.get("promptTokenCount", total_input_tokens)
                total_output_tokens = usage.get("candidatesTokenCount", total_output_tokens)
                cached_tokens = usage.get("cacheReadInputTokens", cached_tokens)
            
            # Determine finish_reason AFTER processing parts so tool_calls state is up-to-date
            if genai_finish:
                if genai_finish in ("STOP", "FUNCTION_CALL"):
                    finish_reason = "tool_calls" if tool_calls else "stop"
                elif genai_finish == "MAX_TOKENS":
                    finish_reason = "length"
                elif genai_finish == "SAFETY":
                    finish_reason = "content_filter"
                else:
                    # Unknown finishReason — treat as stop so client gets a proper finish chunk
                    finish_reason = "tool_calls" if tool_calls else "stop"
            
            # Send finish chunk if we have finish_reason
            if finish_reason and not finish_reason_sent:
                finish_chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "logprobs": None,
                        "finish_reason": finish_reason
                    }]
                }
                yield f"data: {json.dumps(finish_chunk)}\n\n"
                finish_reason_sent = True
                if tool_calls:
                    import logging as _tc_log
                    _tc_log.getLogger("dvproxy.openai").info(
                        f"Emitting finish_reason={finish_reason!r} tool_calls={[(v['id'],v['name']) for v in tool_calls.values()]}"
                    )
        
        # If we never sent any chunks, send an empty one with role
        if not first_chunk_sent:
            empty_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": ""},
                    "logprobs": None,
                    "finish_reason": "stop"
                }]
            }
            yield f"data: {json.dumps(empty_chunk)}\n\n"
        elif not finish_reason_sent:
            # Send final finish chunk
            final_finish = "tool_calls" if tool_calls else "stop"
            finish_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "logprobs": None,
                    "finish_reason": final_finish
                }]
            }
            yield f"data: {json.dumps(finish_chunk)}\n\n"
        
        # Send usage if requested
        if include_usage:
            usage_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [],
                "usage": {
                    "prompt_tokens": total_input_tokens,
                    "completion_tokens": total_output_tokens,
                    "total_tokens": total_input_tokens + total_output_tokens
                }
            }
            if cached_tokens:
                usage_chunk["usage"]["prompt_tokens_details"] = {
                    "cached_tokens": cached_tokens
                }
            yield f"data: {json.dumps(usage_chunk)}\n\n"
        
        yield "data: [DONE]\n\n"
        
        # Log usage
        latency_ms = int((time.time() - start_time) * 1000)
        await UsageService.log_usage(
            db=db,
            api_key_id=api_key_id,
            endpoint="openai",
            model=model,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cached_tokens=cached_tokens,
            ip_address=ip_address,
            user_agent=user_agent,
            latency_ms=latency_ms,
            success=True
        )
        
    except UpstreamError as e:
        # Pass through the upstream error structure directly
        error_chunk = {"error": e.error} if e.error else {
            "error": {"message": str(e), "type": "upstream_error", "code": str(e.status_code)}
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"

        latency_ms = int((time.time() - start_time) * 1000)
        await UsageService.log_usage(
            db=db,
            api_key_id=api_key_id,
            endpoint="openai",
            model=model,
            input_tokens=0,
            output_tokens=0,
            ip_address=ip_address,
            user_agent=user_agent,
            latency_ms=latency_ms,
            success=False,
            error_message=str(e)
        )

    except Exception as e:
        error_chunk = {
            "error": {
                "message": str(e),
                "type": "api_error",
                "code": "internal_error"
            }
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"

        latency_ms = int((time.time() - start_time) * 1000)
        await UsageService.log_usage(
            db=db,
            api_key_id=api_key_id,
            endpoint="openai",
            model=model,
            input_tokens=0,
            output_tokens=0,
            ip_address=ip_address,
            user_agent=user_agent,
            latency_ms=latency_ms,
            success=False,
            error_message=str(e)
        )


@router.post("/responses")
async def create_response(
    request: Request,
    api_key: APIKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """OpenAI Responses API endpoint
    
    This is the newer API for agentic workflows with richer
    event types and structured outputs.
    """
    import logging as _logging
    _resp_logger = _logging.getLogger("dvproxy.responses")

    start_time = time.time()
    body = await request.json()
    
    # Log input structure for diagnostics
    _input = body.get("input", "")
    if isinstance(_input, list):
        _item_types = [(item.get("type"), item.get("call_id") or item.get("id")) for item in _input if isinstance(item, dict)]
        _resp_logger.info(f"Responses input items: {_item_types}")
        # Log items with type=None to understand their structure
        for _idx, _item in enumerate(_input):
            if isinstance(_item, dict) and _item.get("type") is None:
                _resp_logger.info(f"  input[{_idx}] has no type, keys={list(_item.keys())}, role={_item.get('role')}, content_types={[c.get('type') for c in _item.get('content',[]) if isinstance(c,dict)]}")
    
    stream = body.get("stream", False)
    model = body.get("model", "auto")
    
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")
    
    # Generate response ID
    response_id = generate_id("resp_")
    
    # Convert to GenAI format
    genai_request = FormatConverter.openai_responses_to_genai(body)
    
    client = UpstreamClient()
    
    if stream:
        return StreamingResponse(
            _stream_responses_api(
                client, genai_request, model, response_id, api_key.id, db,
                ip_address, user_agent, start_time
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
    else:
        try:
            genai_response = await client.chat_messages(genai_request)
            responses_response = FormatConverter.genai_to_openai_responses(
                genai_response, model, response_id
            )
            
            usage = genai_response.get("usageMetadata", {})
            latency_ms = int((time.time() - start_time) * 1000)
            
            await UsageService.log_usage(
                db=db,
                api_key_id=api_key.id,
                endpoint="responses",
                model=model,
                input_tokens=usage.get("promptTokenCount", 0),
                output_tokens=usage.get("candidatesTokenCount", 0),
                cached_tokens=usage.get("cacheReadInputTokens", 0),
                ip_address=ip_address,
                user_agent=user_agent,
                latency_ms=latency_ms,
                success=True
            )
            
            return responses_response
            
        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            await UsageService.log_usage(
                db=db,
                api_key_id=api_key.id,
                endpoint="responses",
                model=model,
                input_tokens=0,
                output_tokens=0,
                ip_address=ip_address,
                user_agent=user_agent,
                latency_ms=latency_ms,
                success=False,
                error_message=str(e)
            )
            raise HTTPException(status_code=500, detail=str(e))


async def _stream_responses_api(
    client: UpstreamClient,
    genai_request: Dict[str, Any],
    model: str,
    response_id: str,
    api_key_id: int,
    db: AsyncSession,
    ip_address: str,
    user_agent: str,
    start_time: float
):
    """Stream OpenAI Responses API format
    
    Event types:
    - response.created
    - response.in_progress
    - response.output_item.added
    - response.content_part.added
    - response.output_text.delta
    - response.output_text.done
    - response.content_part.done
    - response.output_item.done
    - response.function_call_arguments.delta
    - response.function_call_arguments.done
    - response.completed
    """
    created_at = int(time.time())
    total_input_tokens = 0
    total_output_tokens = 0
    cached_tokens = 0
    
    msg_id = generate_id("msg_")
    output_text = ""
    output_index = 0
    function_calls: Dict[str, Dict] = {}  # call_id -> {name, arguments}
    
    try:
        # Send response.created
        created_event = {
            "type": "response.created",
            "response": {
                "id": response_id,
                "object": "response",
                "created_at": created_at,
                "model": model,
                "status": "in_progress",
                "output": []
            }
        }
        yield f"event: response.created\ndata: {json.dumps(created_event)}\n\n"
        
        # Send response.in_progress
        in_progress_event = {
            "type": "response.in_progress",
            "response": {
                "id": response_id,
                "status": "in_progress"
            }
        }
        yield f"event: response.in_progress\ndata: {json.dumps(in_progress_event)}\n\n"
        
        # Add message output item
        item_added = {
            "type": "response.output_item.added",
            "output_index": output_index,
            "item": {
                "type": "message",
                "id": msg_id,
                "role": "assistant",
                "status": "in_progress",
                "content": []
            }
        }
        yield f"event: response.output_item.added\ndata: {json.dumps(item_added)}\n\n"
        
        # Add content part
        content_part_added = {
            "type": "response.content_part.added",
            "item_id": msg_id,
            "output_index": output_index,
            "content_index": 0,
            "part": {
                "type": "output_text",
                "text": "",
                "annotations": []
            }
        }
        yield f"event: response.content_part.added\ndata: {json.dumps(content_part_added)}\n\n"
        
        async for chunk in client.chat_stream(genai_request):
            if chunk.get("type") == "connection_established":
                continue
            
            candidates = chunk.get("candidates", [])
            if not candidates:
                continue
            
            parts = candidates[0].get("content", {}).get("parts", [])
            
            for part in parts:
                if "text" in part:
                    text = part["text"]
                    output_text += text
                    
                    # Send text delta
                    text_delta = {
                        "type": "response.output_text.delta",
                        "item_id": msg_id,
                        "output_index": output_index,
                        "content_index": 0,
                        "delta": text
                    }
                    yield f"event: response.output_text.delta\ndata: {json.dumps(text_delta)}\n\n"
                
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    call_id = fc.get("id") or generate_id("call_")
                    func_name = fc.get("name", "")
                    args = json.dumps(fc.get("args", {}))
                    
                    if call_id not in function_calls:
                        function_calls[call_id] = {
                            "name": func_name,
                            "arguments": "",
                            "output_index": output_index + 1 + len(function_calls)
                        }
                        fc_idx = function_calls[call_id]["output_index"]
                        fc_id = generate_id("fc_")
                        
                        # Send function call item added
                        fc_added = {
                            "type": "response.output_item.added",
                            "output_index": fc_idx,
                            "item": {
                                "type": "function_call",
                                "id": fc_id,
                                "call_id": call_id,
                                "name": func_name,
                                "arguments": "",
                                "status": "in_progress"
                            }
                        }
                        yield f"event: response.output_item.added\ndata: {json.dumps(fc_added)}\n\n"
                    
                    # Send arguments delta
                    prev_args = function_calls[call_id]["arguments"]
                    if len(args) > len(prev_args):
                        new_args = args[len(prev_args):]
                        function_calls[call_id]["arguments"] = args
                        
                        args_delta = {
                            "type": "response.function_call_arguments.delta",
                            "item_id": msg_id,
                            "output_index": function_calls[call_id]["output_index"],
                            "call_id": call_id,
                            "delta": new_args
                        }
                        yield f"event: response.function_call_arguments.delta\ndata: {json.dumps(args_delta)}\n\n"
            
            # Update usage
            usage = chunk.get("usageMetadata", {})
            if usage:
                total_input_tokens = usage.get("promptTokenCount", total_input_tokens)
                total_output_tokens = usage.get("candidatesTokenCount", total_output_tokens)
                cached_tokens = usage.get("cacheReadInputTokens", cached_tokens)
        
        # Send text done
        text_done = {
            "type": "response.output_text.done",
            "item_id": msg_id,
            "output_index": output_index,
            "content_index": 0,
            "text": output_text
        }
        yield f"event: response.output_text.done\ndata: {json.dumps(text_done)}\n\n"
        
        # Send content part done
        content_done = {
            "type": "response.content_part.done",
            "item_id": msg_id,
            "output_index": output_index,
            "content_index": 0,
            "part": {
                "type": "output_text",
                "text": output_text,
                "annotations": []
            }
        }
        yield f"event: response.content_part.done\ndata: {json.dumps(content_done)}\n\n"
        
        # Send output item done for message
        item_done = {
            "type": "response.output_item.done",
            "output_index": output_index,
            "item": {
                "type": "message",
                "id": msg_id,
                "role": "assistant",
                "status": "completed",
                "content": [{
                    "type": "output_text",
                    "text": output_text,
                    "annotations": []
                }]
            }
        }
        yield f"event: response.output_item.done\ndata: {json.dumps(item_done)}\n\n"
        
        # Send function call done events
        for call_id, fc_data in function_calls.items():
            args_done = {
                "type": "response.function_call_arguments.done",
                "output_index": fc_data["output_index"],
                "call_id": call_id,
                "arguments": fc_data["arguments"]
            }
            yield f"event: response.function_call_arguments.done\ndata: {json.dumps(args_done)}\n\n"
            
            fc_done = {
                "type": "response.output_item.done",
                "output_index": fc_data["output_index"],
                "item": {
                    "type": "function_call",
                    "id": generate_id("fc_"),
                    "call_id": call_id,
                    "name": fc_data["name"],
                    "arguments": fc_data["arguments"],
                    "status": "completed"
                }
            }
            yield f"event: response.output_item.done\ndata: {json.dumps(fc_done)}\n\n"
        
        # Build output for completed response
        output_items = [{
            "type": "message",
            "id": msg_id,
            "role": "assistant",
            "status": "completed",
            "content": [{
                "type": "output_text",
                "text": output_text,
                "annotations": []
            }]
        }]
        
        for call_id, fc_data in function_calls.items():
            output_items.append({
                "type": "function_call",
                "id": generate_id("fc_"),
                "call_id": call_id,
                "name": fc_data["name"],
                "arguments": fc_data["arguments"],
                "status": "completed"
            })
        
        # Send response.completed
        completed_event = {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "object": "response",
                "created_at": created_at,
                "model": model,
                "status": "completed",
                "output": output_items,
                "usage": {
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "total_tokens": total_input_tokens + total_output_tokens
                }
            }
        }
        yield f"event: response.completed\ndata: {json.dumps(completed_event)}\n\n"
        
        yield "data: [DONE]\n\n"
        
        # Log usage
        latency_ms = int((time.time() - start_time) * 1000)
        await UsageService.log_usage(
            db=db,
            api_key_id=api_key_id,
            endpoint="responses",
            model=model,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cached_tokens=cached_tokens,
            ip_address=ip_address,
            user_agent=user_agent,
            latency_ms=latency_ms,
            success=True
        )
        
    except Exception as e:
        # Send response.failed
        failed_event = {
            "type": "response.failed",
            "response": {
                "id": response_id,
                "status": "failed",
                "error": {
                    "message": str(e),
                    "type": "api_error"
                }
            }
        }
        yield f"event: response.failed\ndata: {json.dumps(failed_event)}\n\n"
        
        latency_ms = int((time.time() - start_time) * 1000)
        await UsageService.log_usage(
            db=db,
            api_key_id=api_key_id,
            endpoint="responses",
            model=model,
            input_tokens=0,
            output_tokens=0,
            ip_address=ip_address,
            user_agent=user_agent,
            latency_ms=latency_ms,
            success=False,
            error_message=str(e)
        )
