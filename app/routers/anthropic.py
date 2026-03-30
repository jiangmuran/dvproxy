"""
DVProxy - Anthropic API Router
Handles /v1/messages endpoint (Anthropic format)

Implements the full Anthropic Messages API including:
- Non-streaming and streaming responses
- Tool use with proper input_json_delta streaming
- Extended thinking with thinking_delta events
- Proper SSE event format with all required fields
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
from app.services.upstream import UpstreamClient
from app.services.usage import UsageService

router = APIRouter(prefix="/v1", tags=["Anthropic"])


def generate_id(prefix: str = "") -> str:
    """Generate a unique ID"""
    return f"{prefix}{uuid.uuid4().hex[:24]}"


@router.post("/messages")
async def create_message(
    request: Request,
    api_key: APIKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Anthropic Messages API endpoint
    
    Supports:
    - Text messages with string or array content
    - Images (base64 and URL)
    - Tool definitions and tool_use/tool_result
    - Extended thinking
    - Streaming and non-streaming
    """
    start_time = time.time()
    body = await request.json()
    
    # Check if streaming is requested
    stream = body.get("stream", False)
    model = body.get("model", "auto")
    
    # Get client info
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")
    
    # Generate message ID for this request
    msg_id = generate_id("msg_")
    
    # Convert to GenAI format
    genai_request = FormatConverter.anthropic_to_genai(body)
    
    # Create upstream client
    client = UpstreamClient()
    
    if stream:
        return StreamingResponse(
            _stream_anthropic_response(
                client, genai_request, model, msg_id, api_key.id, db,
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
            # Call upstream
            genai_response = await client.chat_messages(genai_request)
            
            # Convert response
            anthropic_response = FormatConverter.genai_to_anthropic(
                genai_response, model, msg_id
            )
            
            # Log usage
            usage = genai_response.get("usageMetadata", {})
            latency_ms = int((time.time() - start_time) * 1000)
            
            await UsageService.log_usage(
                db=db,
                api_key_id=api_key.id,
                endpoint="anthropic",
                model=model,
                input_tokens=usage.get("promptTokenCount", 0),
                output_tokens=usage.get("candidatesTokenCount", 0),
                cached_tokens=usage.get("cacheReadInputTokens", 0),
                ip_address=ip_address,
                user_agent=user_agent,
                latency_ms=latency_ms,
                success=True
            )
            
            return anthropic_response
            
        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            await UsageService.log_usage(
                db=db,
                api_key_id=api_key.id,
                endpoint="anthropic",
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


async def _stream_anthropic_response(
    client: UpstreamClient,
    genai_request: Dict[str, Any],
    model: str,
    msg_id: str,
    api_key_id: int,
    db: AsyncSession,
    ip_address: str,
    user_agent: str,
    start_time: float
):
    """Stream Anthropic-format response
    
    Event flow:
    1. message_start - contains Message with empty content
    2. For each content block:
       - content_block_start
       - content_block_delta (multiple)
       - content_block_stop
    3. message_delta - final stop_reason and usage
    4. message_stop
    
    Delta types:
    - text_delta: for text content
    - input_json_delta: for tool_use input (partial JSON)
    - thinking_delta: for thinking blocks
    - signature_delta: for thinking signature
    """
    total_input_tokens = 0
    total_output_tokens = 0
    cached_tokens = 0
    content_blocks = []
    current_block_idx = 0
    current_text_block_started = False
    current_tool_block_idx = -1
    tool_input_buffer = ""
    
    try:
        # Send message_start event
        message_start = {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0
                }
            }
        }
        yield f"event: message_start\ndata: {json.dumps(message_start)}\n\n"
        
        async for chunk in client.chat_stream(genai_request):
            # Skip connection messages
            if chunk.get("type") == "connection_established":
                continue
            
            # Handle ping events
            if chunk.get("type") == "ping":
                yield f"event: ping\ndata: {json.dumps({'type': 'ping'})}\n\n"
                continue
            
            # Process candidates
            candidates = chunk.get("candidates", [])
            if not candidates:
                continue
            
            candidate = candidates[0]
            parts = candidate.get("content", {}).get("parts", [])
            
            for part in parts:
                if "text" in part:
                    text = part["text"]
                    
                    # Start text content block if needed
                    if not current_text_block_started:
                        content_block_start = {
                            "type": "content_block_start",
                            "index": current_block_idx,
                            "content_block": {
                                "type": "text",
                                "text": ""
                            }
                        }
                        yield f"event: content_block_start\ndata: {json.dumps(content_block_start)}\n\n"
                        content_blocks.append({"type": "text", "text": ""})
                        current_text_block_started = True
                    
                    # Send text delta
                    text_delta = {
                        "type": "content_block_delta",
                        "index": current_block_idx,
                        "delta": {
                            "type": "text_delta",
                            "text": text
                        }
                    }
                    yield f"event: content_block_delta\ndata: {json.dumps(text_delta)}\n\n"
                    content_blocks[-1]["text"] += text
                
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    tool_id = fc.get("id") or generate_id("toolu_")
                    
                    # Close previous text block if any
                    if current_text_block_started:
                        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': current_block_idx})}\n\n"
                        current_block_idx += 1
                        current_text_block_started = False
                    
                    # Check if this is a new tool call or update to existing
                    if current_tool_block_idx != current_block_idx:
                        # Start new tool_use block
                        tool_block = {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": fc.get("name", ""),
                            "input": {}
                        }
                        
                        content_block_start = {
                            "type": "content_block_start",
                            "index": current_block_idx,
                            "content_block": tool_block
                        }
                        yield f"event: content_block_start\ndata: {json.dumps(content_block_start)}\n\n"
                        content_blocks.append(tool_block)
                        current_tool_block_idx = current_block_idx
                        tool_input_buffer = ""
                    
                    # Send input as partial JSON delta
                    # The input_json_delta contains partial JSON that accumulates
                    args = fc.get("args", {})
                    args_json = json.dumps(args)
                    
                    # Calculate the new portion of JSON
                    if len(args_json) > len(tool_input_buffer):
                        new_json = args_json[len(tool_input_buffer):]
                        tool_input_buffer = args_json
                        
                        input_delta = {
                            "type": "content_block_delta",
                            "index": current_block_idx,
                            "delta": {
                                "type": "input_json_delta",
                                "partial_json": new_json
                            }
                        }
                        yield f"event: content_block_delta\ndata: {json.dumps(input_delta)}\n\n"
                    
                    # Update the content block with final input
                    content_blocks[-1]["input"] = args
                
                elif "reasoning" in part:
                    # Extended thinking support
                    thinking_text = part["reasoning"]
                    
                    # Close previous blocks if needed
                    if current_text_block_started:
                        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': current_block_idx})}\n\n"
                        current_block_idx += 1
                        current_text_block_started = False
                    
                    # Start thinking block
                    thinking_block = {
                        "type": "thinking",
                        "thinking": ""
                    }
                    content_block_start = {
                        "type": "content_block_start",
                        "index": current_block_idx,
                        "content_block": thinking_block
                    }
                    yield f"event: content_block_start\ndata: {json.dumps(content_block_start)}\n\n"
                    content_blocks.append(thinking_block)
                    
                    # Send thinking delta
                    thinking_delta = {
                        "type": "content_block_delta",
                        "index": current_block_idx,
                        "delta": {
                            "type": "thinking_delta",
                            "thinking": thinking_text
                        }
                    }
                    yield f"event: content_block_delta\ndata: {json.dumps(thinking_delta)}\n\n"
                    content_blocks[-1]["thinking"] = thinking_text
                    
                    # Send signature delta (required for thinking blocks)
                    signature_delta = {
                        "type": "content_block_delta",
                        "index": current_block_idx,
                        "delta": {
                            "type": "signature_delta",
                            "signature": generate_id("sig_")
                        }
                    }
                    yield f"event: content_block_delta\ndata: {json.dumps(signature_delta)}\n\n"
                    
                    # Close thinking block
                    yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': current_block_idx})}\n\n"
                    current_block_idx += 1
            
            # Update usage from chunk
            usage = chunk.get("usageMetadata", {})
            if usage:
                total_input_tokens = usage.get("promptTokenCount", total_input_tokens)
                total_output_tokens = usage.get("candidatesTokenCount", total_output_tokens)
                cached_tokens = usage.get("cacheReadInputTokens", cached_tokens)
        
        # Close any open text block
        if current_text_block_started:
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': current_block_idx})}\n\n"
        
        # Close any open tool block
        if current_tool_block_idx >= 0 and current_tool_block_idx == current_block_idx:
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': current_block_idx})}\n\n"
        
        # Determine stop reason
        stop_reason = "end_turn"
        if any(b.get("type") == "tool_use" for b in content_blocks):
            stop_reason = "tool_use"
        
        # Send message_delta with final stop_reason and usage
        message_delta = {
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_reason,
                "stop_sequence": None
            },
            "usage": {
                "output_tokens": total_output_tokens
            }
        }
        yield f"event: message_delta\ndata: {json.dumps(message_delta)}\n\n"
        
        # Send message_stop
        yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
        
        # Log usage
        latency_ms = int((time.time() - start_time) * 1000)
        await UsageService.log_usage(
            db=db,
            api_key_id=api_key_id,
            endpoint="anthropic",
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
        # Send error event
        error_event = {
            "type": "error",
            "error": {
                "type": "api_error",
                "message": str(e)
            }
        }
        yield f"event: error\ndata: {json.dumps(error_event)}\n\n"
        
        latency_ms = int((time.time() - start_time) * 1000)
        await UsageService.log_usage(
            db=db,
            api_key_id=api_key_id,
            endpoint="anthropic",
            model=model,
            input_tokens=0,
            output_tokens=0,
            ip_address=ip_address,
            user_agent=user_agent,
            latency_ms=latency_ms,
            success=False,
            error_message=str(e)
        )
