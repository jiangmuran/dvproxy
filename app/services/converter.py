"""
DVProxy - Format Converter
Convert between Anthropic/OpenAI formats and GenAI (upstream) format

Supports:
- Anthropic Messages API (POST /v1/messages)
- OpenAI Chat Completions (POST /v1/chat/completions)  
- OpenAI Responses API (POST /v1/responses)

All converted to/from DeepVLab GenAI format (Google Gemini-like).
"""
from typing import Any, Dict, List, Optional, Union
import json
import time
import uuid


def generate_id(prefix: str = "") -> str:
    """Generate a unique ID with optional prefix"""
    return f"{prefix}{uuid.uuid4().hex[:24]}"


class FormatConverter:
    """Convert between different API formats"""

    # ==================== Message Sanitization ====================

    @staticmethod
    def _sanitize_anthropic_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Ensure every tool_use block is immediately followed by a matching tool_result.

        Bedrock Claude validates that:
          1. Every tool_use id has a tool_result with the same tool_use_id in the
             very next user message.
          2. text content blocks are non-empty.

        Strategy: walk the messages in order.  For each assistant message, collect
        the tool_use ids it emits.  Then look at the immediately following user
        message to see which ids have a tool_result.  Any tool_use id that is NOT
        covered by the next user message is dropped from the assistant message.
        If the assistant message becomes empty after dropping, it is removed too.
        """
        if not messages:
            return messages

        result = list(messages)  # shallow copy so we can patch in-place by index

        i = 0
        while i < len(result):
            msg = result[i]
            role = msg.get("role", "")
            content = msg.get("content", "")

            # --- collect tool_use ids emitted by this assistant message ---
            if role == "assistant" and isinstance(content, list):
                tool_use_ids = [
                    b.get("id", "") for b in content
                    if b.get("type") == "tool_use" and b.get("id", "")
                ]

                if tool_use_ids:
                    # Find the immediately following user message
                    next_user_content: List[Dict] = []
                    if i + 1 < len(result):
                        next_msg = result[i + 1]
                        if next_msg.get("role") == "user" and isinstance(next_msg.get("content"), list):
                            next_user_content = next_msg["content"]

                    covered_ids = {
                        b.get("tool_use_id", "")
                        for b in next_user_content
                        if b.get("type") == "tool_result"
                    }

                    # Drop tool_use blocks whose id is not covered
                    new_content = [
                        b for b in content
                        if not (b.get("type") == "tool_use" and b.get("id", "") not in covered_ids)
                    ]

                    if not new_content:
                        # Entire assistant message was tool_use with no results — remove it
                        result.pop(i)
                        continue
                    elif len(new_content) != len(content):
                        result[i] = {**msg, "content": new_content}

            # --- drop tool_result blocks whose tool_use_id has no preceding tool_use ---
            # (handles orphaned tool_result left after prior cleanup)
            elif role == "user" and isinstance(content, list):
                # Collect all tool_use ids seen so far (in result[:i])
                seen_tool_use_ids: set = set()
                for prev in result[:i]:
                    prev_content = prev.get("content", "")
                    if isinstance(prev_content, list):
                        for b in prev_content:
                            if b.get("type") == "tool_use" and b.get("id", ""):
                                seen_tool_use_ids.add(b["id"])

                new_content = [
                    b for b in content
                    if not (
                        b.get("type") == "tool_result"
                        and b.get("tool_use_id", "") not in seen_tool_use_ids
                    )
                ]
                if not new_content:
                    result.pop(i)
                    continue
                elif len(new_content) != len(content):
                    result[i] = {**msg, "content": new_content}

            i += 1

        return result

    @staticmethod
    def _sanitize_openai_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Ensure every tool_calls entry (or Anthropic-style tool_use in content array)
        has a matching tool-role message immediately after.

        Walk in order: for each assistant message with tool_calls or tool_use content
        blocks, look ahead to collect which ids are answered.  Drop unanswered ones.
        """
        if not messages:
            return messages

        result = list(messages)

        i = 0
        while i < len(result):
            msg = result[i]
            if msg.get("role") == "assistant":
                tool_calls = msg.get("tool_calls") or []
                content = msg.get("content")

                # Also collect Anthropic-style tool_use blocks inside content array
                content_tool_uses = []
                if isinstance(content, list):
                    content_tool_uses = [
                        b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"
                    ]

                all_tool_ids = (
                    [tc.get("id", "") for tc in tool_calls] +
                    [b.get("id", "") for b in content_tool_uses]
                )

                if all_tool_ids:
                    # Collect covered ids from immediately following tool messages
                    # Also accept Anthropic-style tool_result blocks inside user content
                    covered_ids: set = set()
                    j = i + 1
                    while j < len(result):
                        next_msg = result[j]
                        next_role = next_msg.get("role", "")
                        if next_role == "tool":
                            tid = next_msg.get("tool_call_id", "")
                            if tid:
                                covered_ids.add(tid)
                            j += 1
                        elif next_role == "user":
                            next_content = next_msg.get("content", "")
                            if isinstance(next_content, list):
                                has_tool_result = any(
                                    b.get("type") == "tool_result"
                                    for b in next_content if isinstance(b, dict)
                                )
                                if has_tool_result:
                                    for b in next_content:
                                        if isinstance(b, dict) and b.get("type") == "tool_result":
                                            tid = b.get("tool_use_id", "")
                                            if tid:
                                                covered_ids.add(tid)
                                    j += 1
                                    continue
                            break
                        else:
                            break

                    uncovered = [tid for tid in all_tool_ids if tid not in covered_ids]
                    if uncovered:
                        uncovered_set = set(uncovered)
                        # Filter tool_calls
                        valid_calls = [tc for tc in tool_calls if tc.get("id", "") not in uncovered_set]
                        # Filter content tool_use blocks
                        new_content = content
                        if isinstance(content, list):
                            new_content = [
                                b for b in content
                                if not (isinstance(b, dict) and b.get("type") == "tool_use"
                                        and b.get("id", "") in uncovered_set)
                            ]
                            # Also drop empty text blocks
                            new_content = [
                                b for b in new_content
                                if not (isinstance(b, dict) and b.get("type") == "text"
                                        and not b.get("text", "").strip())
                            ]

                        new_msg = {**msg}
                        if valid_calls != tool_calls:
                            if valid_calls:
                                new_msg["tool_calls"] = valid_calls
                            else:
                                new_msg.pop("tool_calls", None)
                        if new_content != content:
                            new_msg["content"] = new_content if new_content else None

                        # Drop message entirely if nothing left
                        has_content = bool(new_msg.get("content")) or bool(new_msg.get("tool_calls"))
                        if not has_content:
                            result.pop(i)
                            continue
                        result[i] = new_msg

            i += 1

        return result

    @staticmethod
    def _sanitize_responses_input(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove function_call items that have no matching function_call_output.

        Bedrock raises ValidationException if a tool_use has no immediately
        following tool_result.  Walk the input list and drop any function_call
        whose call_id is never answered by a function_call_output further along.
        """
        # Collect all answered call_ids
        answered: set = set()
        for item in items:
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                cid = item.get("call_id", "")
                if cid:
                    answered.add(cid)

        result = []
        for item in items:
            if isinstance(item, dict) and item.get("type") == "function_call":
                cid = item.get("call_id", item.get("id", ""))
                if cid and cid not in answered:
                    import logging
                    logging.getLogger("dvproxy.converter").warning(
                        f"Dropping unanswered function_call id={cid!r} from Responses API input"
                    )
                    continue
            result.append(item)
        return result

    # ==================== Anthropic -> GenAI ====================

    @staticmethod
    def anthropic_to_genai(request: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Anthropic Messages API request to GenAI format
        
        Handles:
        - Text content (string or array of content blocks)
        - Images (base64 and URL)
        - Tool use (tool_use blocks from assistant)
        - Tool results (tool_result blocks from user)
        - Thinking blocks (thinking/redacted_thinking)
        - Documents (PDF, text)
        """
        contents = []

        # Sanitize: drop orphaned tool_use blocks before conversion
        messages = FormatConverter._sanitize_anthropic_messages(request.get("messages", []))

        # Convert messages
        for msg in messages:
            role = "model" if msg["role"] == "assistant" else "user"
            parts = []
            
            content = msg.get("content", "")
            
            if isinstance(content, str):
                if content.strip():
                    parts.append({"text": content})
            elif isinstance(content, list):
                for block in content:
                    block_type = block.get("type", "")
                    
                    if block_type == "text":
                        text = block.get("text", "")
                        if text.strip():
                            parts.append({"text": text})
                    
                    elif block_type == "image":
                        source = block.get("source", {})
                        if source.get("type") == "base64":
                            parts.append({
                                "inlineData": {
                                    "mimeType": source.get("media_type", "image/png"),
                                    "data": source.get("data", "")
                                }
                            })
                        elif source.get("type") == "url":
                            # URL images - pass through as file data reference
                            parts.append({
                                "fileData": {
                                    "mimeType": source.get("media_type", "image/png"),
                                    "fileUri": source.get("url", "")
                                }
                            })
                    
                    elif block_type == "document":
                        # PDF and text documents
                        source = block.get("source", {})
                        if source.get("type") == "base64":
                            parts.append({
                                "inlineData": {
                                    "mimeType": source.get("media_type", "application/pdf"),
                                    "data": source.get("data", "")
                                }
                            })
                    
                    elif block_type == "tool_use":
                        parts.append({
                            "functionCall": {
                                "name": block.get("name", ""),
                                "args": block.get("input", {}),
                                "id": block.get("id", "")
                            }
                        })
                    
                    elif block_type == "tool_result":
                        tool_content = block.get("content", "")
                        # Handle content as string or array
                        if isinstance(tool_content, list):
                            # Extract text from content blocks
                            text_parts = []
                            for c in tool_content:
                                if isinstance(c, dict):
                                    if c.get("type") == "text":
                                        text_parts.append(c.get("text", ""))
                                    elif c.get("type") == "image":
                                        # Images in tool results - include as base64
                                        pass
                                else:
                                    text_parts.append(str(c))
                            tool_content = "\n".join(text_parts) if text_parts else json.dumps(tool_content)
                        if not isinstance(tool_content, str):
                            tool_content = json.dumps(tool_content)
                        if not tool_content.strip():
                            tool_content = "(empty)"
                        
                        # tool_result references tool_use_id, map to functionResponse
                        tool_use_id = block.get("tool_use_id", "")
                        parts.append({
                            "functionResponse": {
                                "name": tool_use_id,  # GenAI uses the ID as reference
                                "response": {
                                    "output": tool_content,
                                    "is_error": block.get("is_error", False)
                                },
                                "id": tool_use_id
                            }
                        })
                    
                    elif block_type == "thinking":
                        # Extended thinking - convert to reasoning part
                        parts.append({
                            "reasoning": block.get("thinking", "")
                        })
                    
                    elif block_type == "redacted_thinking":
                        # Redacted thinking - skip or add placeholder
                        parts.append({
                            "reasoning": "[redacted]"
                        })
            
            if parts:
                contents.append({"role": role, "parts": parts})
        
        # Build GenAI request
        genai_request = {
            "model": request.get("model", "auto"),
            "contents": contents,
            "config": {}
        }
        
        # Handle system prompt (can be string or array of text blocks)
        system = request.get("system")
        if system:
            if isinstance(system, str):
                system_text = system
            elif isinstance(system, list):
                # Array of content blocks with type: "text" and cache_control
                parts = []
                for s in system:
                    if isinstance(s, str):
                        parts.append(s)
                    elif isinstance(s, dict) and s.get("type") == "text":
                        parts.append(s.get("text", ""))
                system_text = "\n".join(parts)
            else:
                system_text = str(system)
            
            genai_request["config"]["systemInstruction"] = {
                "parts": [{"text": system_text}]
            }
        
        # Handle tools (Anthropic format)
        tools = request.get("tools", [])
        if tools:
            func_decls = []
            for tool in tools:
                # Standard tool definition
                if "name" in tool:
                    func_decls.append({
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", {})
                    })
            if func_decls:
                genai_request["config"]["tools"] = [{"functionDeclarations": func_decls}]
        
        # Handle tool_choice
        tool_choice = request.get("tool_choice")
        if tool_choice:
            if isinstance(tool_choice, dict):
                choice_type = tool_choice.get("type", "")
                if choice_type == "auto":
                    genai_request["config"]["toolConfig"] = {"mode": "AUTO"}
                elif choice_type == "any":
                    genai_request["config"]["toolConfig"] = {"mode": "ANY"}
                elif choice_type == "tool":
                    genai_request["config"]["toolConfig"] = {
                        "mode": "SINGLE",
                        "allowedFunctionNames": [tool_choice.get("name", "")]
                    }
            elif tool_choice == "auto":
                genai_request["config"]["toolConfig"] = {"mode": "AUTO"}
            elif tool_choice == "none":
                genai_request["config"]["toolConfig"] = {"mode": "NONE"}
        
        # Handle max_tokens
        max_tokens = request.get("max_tokens")
        if max_tokens:
            genai_request["config"]["maxOutputTokens"] = max_tokens
        
        # Handle temperature
        temperature = request.get("temperature")
        if temperature is not None:
            genai_request["config"]["temperature"] = temperature
        
        # Handle top_p
        top_p = request.get("top_p")
        if top_p is not None:
            genai_request["config"]["topP"] = top_p
        
        # Handle top_k
        top_k = request.get("top_k")
        if top_k is not None:
            genai_request["config"]["topK"] = top_k
        
        # Handle stop_sequences
        stop_sequences = request.get("stop_sequences")
        if stop_sequences:
            genai_request["config"]["stopSequences"] = stop_sequences
        
        # Handle thinking (extended thinking)
        thinking = request.get("thinking")
        if thinking:
            if isinstance(thinking, dict) and thinking.get("type") == "enabled":
                genai_request["config"]["thinkingConfig"] = {
                    "thinkingBudget": thinking.get("budget_tokens", 10000)
                }
        
        return genai_request
    
    @staticmethod
    def genai_to_anthropic(response: Dict[str, Any], model: str, request_id: str = None) -> Dict[str, Any]:
        """Convert GenAI response to Anthropic format
        
        Handles:
        - Text parts -> text content blocks
        - Function calls -> tool_use content blocks
        - Reasoning -> thinking content blocks
        - Proper stop_reason mapping
        - Complete usage statistics
        """
        content = []
        stop_reason = "end_turn"
        
        candidate = response.get("candidates", [{}])[0]
        parts = candidate.get("content", {}).get("parts", [])
        
        for idx, part in enumerate(parts):
            if "text" in part:
                content.append({
                    "type": "text",
                    "text": part["text"]
                })
            
            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_id = fc.get("id") or generate_id("toolu_")
                content.append({
                    "type": "tool_use",
                    "id": tool_id,
                    "name": fc.get("name", ""),
                    "input": fc.get("args", {})
                })
                stop_reason = "tool_use"
            
            elif "reasoning" in part:
                # Extended thinking support
                content.append({
                    "type": "thinking",
                    "thinking": part["reasoning"]
                })
        
        # Map finish reason
        finish_reason = candidate.get("finishReason", "STOP")
        if finish_reason == "MAX_TOKENS":
            stop_reason = "max_tokens"
        elif finish_reason == "STOP":
            # Check if we have tool_use blocks
            if any(c.get("type") == "tool_use" for c in content):
                stop_reason = "tool_use"
            else:
                stop_reason = "end_turn"
        elif finish_reason == "SAFETY":
            stop_reason = "end_turn"  # Anthropic doesn't have a specific safety stop
        
        usage = response.get("usageMetadata", {})
        
        # Generate message ID if not provided
        msg_id = request_id or generate_id("msg_")
        
        return {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "content": content if content else [{"type": "text", "text": ""}],
            "model": model,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": usage.get("promptTokenCount", 0),
                "output_tokens": usage.get("candidatesTokenCount", 0),
                "cache_creation_input_tokens": usage.get("cacheCreationInputTokens", 0),
                "cache_read_input_tokens": usage.get("cacheReadInputTokens", 0)
            }
        }
    
    # ==================== OpenAI -> GenAI ====================
    
    @staticmethod
    def openai_to_genai(request: Dict[str, Any]) -> Dict[str, Any]:
        """Convert OpenAI Chat Completions request to GenAI format
        
        Handles:
        - System/developer messages -> systemInstruction
        - User messages with text and images
        - Assistant messages with content and tool_calls
        - Tool messages (function responses)
        - Function definitions
        - All sampling parameters
        """
        contents = []
        system_instruction = None

        # Sanitize: drop orphaned tool_calls with no matching tool response
        messages = FormatConverter._sanitize_openai_messages(request.get("messages", []))

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content")

            # Handle system message (or developer message in newer API)
            if role in ("system", "developer"):
                if isinstance(content, str):
                    if system_instruction:
                        system_instruction += "\n" + content
                    else:
                        system_instruction = content
                elif isinstance(content, list):
                    # Array of content parts
                    texts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            texts.append(part.get("text", ""))
                        elif isinstance(part, str):
                            texts.append(part)
                    combined = "\n".join(texts)
                    if system_instruction:
                        system_instruction += "\n" + combined
                    else:
                        system_instruction = combined
                continue
            
            # Handle tool/function response
            if role == "tool":
                tool_call_id = msg.get("tool_call_id", "")
                tool_content = content if isinstance(content, str) else json.dumps(content) if content else ""
                if not tool_content.strip():
                    tool_content = "(empty)"
                parts = [{
                    "functionResponse": {
                        "name": tool_call_id,
                        "response": {"output": tool_content},
                        "id": tool_call_id
                    }
                }]
                contents.append({"role": "user", "parts": parts})
                continue
            
            # Handle function role (legacy)
            if role == "function":
                func_name = msg.get("name", "")
                func_content = content if isinstance(content, str) else json.dumps(content) if content else ""
                parts = [{
                    "functionResponse": {
                        "name": func_name,
                        "response": {"output": func_content},
                        "id": func_name
                    }
                }]
                contents.append({"role": "user", "parts": parts})
                continue
            
            # Convert role
            genai_role = "model" if role == "assistant" else "user"
            parts = []
            
            # Handle content
            if content is not None:
                if isinstance(content, str):
                    if content:
                        parts.append({"text": content})
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, str):
                            if part.strip():
                                parts.append({"text": part})
                        elif isinstance(part, dict):
                            part_type = part.get("type", "")
                            if part_type == "text":
                                text = part.get("text", "")
                                if text and text.strip():
                                    parts.append({"text": text})
                            elif part_type == "tool_use":
                                # Anthropic-style tool_use block inside OpenAI content array
                                try:
                                    args = part.get("input", {})
                                    if isinstance(args, str):
                                        args = json.loads(args)
                                except (json.JSONDecodeError, TypeError):
                                    args = {}
                                parts.append({
                                    "functionCall": {
                                        "name": part.get("name", ""),
                                        "args": args,
                                        "id": part.get("id", "")
                                    }
                                })
                            elif part_type == "tool_result":
                                # Anthropic-style tool_result block inside OpenAI content array
                                tool_content = part.get("content", "")
                                if isinstance(tool_content, list):
                                    text_parts = [
                                        c.get("text", "") for c in tool_content
                                        if isinstance(c, dict) and c.get("type") == "text"
                                    ]
                                    tool_content = "\n".join(text_parts) if text_parts else json.dumps(tool_content)
                                tool_use_id = part.get("tool_use_id", "")
                                parts.append({
                                    "functionResponse": {
                                        "name": tool_use_id,
                                        "response": {"output": tool_content},
                                        "id": tool_use_id
                                    }
                                })
                            elif part_type == "image_url":
                                image_url = part.get("image_url", {})
                                url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url)
                                # Handle data URL
                                if url.startswith("data:"):
                                    try:
                                        header, data = url.split(",", 1)
                                        mime_type = header.split(":")[1].split(";")[0]
                                        parts.append({
                                            "inlineData": {
                                                "mimeType": mime_type,
                                                "data": data
                                            }
                                        })
                                    except (ValueError, IndexError):
                                        pass
                                else:
                                    # Regular URL - pass as file reference
                                    parts.append({
                                        "fileData": {
                                            "fileUri": url,
                                            "mimeType": "image/jpeg"
                                        }
                                    })
                            elif part_type == "input_audio":
                                # Audio input (realtime API)
                                audio_data = part.get("input_audio", {})
                                if audio_data:
                                    parts.append({
                                        "inlineData": {
                                            "mimeType": f"audio/{audio_data.get('format', 'wav')}",
                                            "data": audio_data.get("data", "")
                                        }
                                    })
            
            # Handle tool calls in assistant message
            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                tc_type = tc.get("type", "function")
                if tc_type == "function":
                    func = tc.get("function", {})
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    parts.append({
                        "functionCall": {
                            "name": func.get("name", ""),
                            "args": args,
                            "id": tc.get("id", "")
                        }
                    })
            
            # Handle legacy function_call in assistant message
            function_call = msg.get("function_call")
            if function_call:
                try:
                    args = json.loads(function_call.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}
                parts.append({
                    "functionCall": {
                        "name": function_call.get("name", ""),
                        "args": args,
                        "id": generate_id("call_")
                    }
                })
            
            if parts:
                contents.append({"role": genai_role, "parts": parts})
        
        # Build GenAI request
        genai_request = {
            "model": request.get("model", "auto"),
            "contents": contents,
            "config": {}
        }
        
        # Add system instruction
        if system_instruction:
            genai_request["config"]["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }
        
        # Handle tools (function type)
        tools = request.get("tools", [])
        if tools:
            func_decls = []
            for tool in tools:
                tool_type = tool.get("type", "function")
                if tool_type == "function":
                    func = tool.get("function", {})
                    func_decls.append({
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "parameters": func.get("parameters", {})
                    })
            if func_decls:
                genai_request["config"]["tools"] = [{"functionDeclarations": func_decls}]
        
        # Handle legacy functions parameter
        functions = request.get("functions", [])
        if functions and not tools:
            func_decls = []
            for func in functions:
                func_decls.append({
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "parameters": func.get("parameters", {})
                })
            if func_decls:
                genai_request["config"]["tools"] = [{"functionDeclarations": func_decls}]
        
        # Handle tool_choice
        tool_choice = request.get("tool_choice")
        if tool_choice:
            if isinstance(tool_choice, str):
                if tool_choice == "auto":
                    genai_request["config"]["toolConfig"] = {"mode": "AUTO"}
                elif tool_choice == "none":
                    genai_request["config"]["toolConfig"] = {"mode": "NONE"}
                elif tool_choice == "required":
                    genai_request["config"]["toolConfig"] = {"mode": "ANY"}
            elif isinstance(tool_choice, dict):
                choice_type = tool_choice.get("type", "")
                if choice_type == "function":
                    func_name = tool_choice.get("function", {}).get("name", "")
                    genai_request["config"]["toolConfig"] = {
                        "mode": "SINGLE",
                        "allowedFunctionNames": [func_name]
                    }
        
        # Handle max_tokens / max_completion_tokens
        max_tokens = request.get("max_tokens") or request.get("max_completion_tokens")
        if max_tokens:
            genai_request["config"]["maxOutputTokens"] = max_tokens
        
        # Handle temperature
        temperature = request.get("temperature")
        if temperature is not None:
            genai_request["config"]["temperature"] = temperature
        
        # Handle top_p
        top_p = request.get("top_p")
        if top_p is not None:
            genai_request["config"]["topP"] = top_p
        
        # Handle stop sequences
        stop = request.get("stop")
        if stop:
            if isinstance(stop, str):
                genai_request["config"]["stopSequences"] = [stop]
            elif isinstance(stop, list):
                genai_request["config"]["stopSequences"] = stop
        
        # Handle presence_penalty and frequency_penalty
        presence_penalty = request.get("presence_penalty")
        if presence_penalty is not None:
            genai_request["config"]["presencePenalty"] = presence_penalty
        
        frequency_penalty = request.get("frequency_penalty")
        if frequency_penalty is not None:
            genai_request["config"]["frequencyPenalty"] = frequency_penalty
        
        # Handle seed
        seed = request.get("seed")
        if seed is not None:
            genai_request["config"]["seed"] = seed
        
        return genai_request
    
    @staticmethod
    def genai_to_openai(response: Dict[str, Any], model: str, request_id: str = None) -> Dict[str, Any]:
        """Convert GenAI response to OpenAI Chat Completions format
        
        Handles:
        - Text parts -> content string
        - Function calls -> tool_calls array
        - Proper finish_reason mapping
        - Complete usage statistics with details
        """
        candidate = response.get("candidates", [{}])[0]
        parts = candidate.get("content", {}).get("parts", [])
        
        content = None
        tool_calls = []
        text_parts = []
        
        for idx, part in enumerate(parts):
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                call_id = fc.get("id") or generate_id("call_")
                tool_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": fc.get("name", ""),
                        "arguments": json.dumps(fc.get("args", {}))
                    }
                })
        
        if text_parts:
            content = "".join(text_parts)
        
        # Determine finish reason
        finish_reason = "stop"
        genai_finish = candidate.get("finishReason", "STOP")
        if genai_finish == "MAX_TOKENS":
            finish_reason = "length"
        elif genai_finish == "SAFETY":
            finish_reason = "content_filter"
        elif tool_calls:
            finish_reason = "tool_calls"
        
        usage = response.get("usageMetadata", {})
        prompt_tokens = usage.get("promptTokenCount", 0)
        completion_tokens = usage.get("candidatesTokenCount", 0)
        cached_tokens = usage.get("cacheReadInputTokens", 0)
        
        # Build message
        message = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        
        # Generate completion ID
        completion_id = request_id or generate_id("chatcmpl-")
        
        result = {
            "id": completion_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
                "logprobs": None
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens
            }
        }
        
        # Add prompt_tokens_details if we have cached tokens
        if cached_tokens:
            result["usage"]["prompt_tokens_details"] = {
                "cached_tokens": cached_tokens
            }
        
        # Add system_fingerprint for compatibility
        result["system_fingerprint"] = f"fp_{generate_id('')[:12]}"
        
        return result
    
    # ==================== OpenAI Responses API -> GenAI ====================
    
    @staticmethod
    def openai_responses_to_genai(request: Dict[str, Any]) -> Dict[str, Any]:
        """Convert OpenAI Responses API request to GenAI format
        
        The Responses API has a different structure:
        - input: string or array of input items (messages, function outputs, etc.)
        - instructions: system prompt
        - tools: function definitions with different structure
        """
        contents = []
        system_instruction = None
        
        # Handle instructions (system prompt)
        instructions = request.get("instructions")
        if instructions:
            system_instruction = instructions
        
        # Sanitize input: remove function_call items that have no matching
        # function_call_output, which would cause Bedrock ValidationException.
        input_data = request.get("input", "")
        if isinstance(input_data, list):
            input_data = FormatConverter._sanitize_responses_input(input_data)
        
        if isinstance(input_data, str):
            # Simple string input
            contents.append({"role": "user", "parts": [{"text": input_data}]})
        elif isinstance(input_data, list):
            for item in input_data:
                if isinstance(item, str):
                    # String item is user text
                    contents.append({"role": "user", "parts": [{"text": item}]})
                    continue
                
                item_type = item.get("type", "")
                
                if item_type == "message":
                    role = "model" if item.get("role") == "assistant" else "user"
                    parts = []
                    
                    for content_item in item.get("content", []):
                        ct = content_item.get("type", "")
                        if ct in ("input_text", "output_text", "text"):
                            text = content_item.get("text", "")
                            if text:
                                parts.append({"text": text})
                        elif ct == "input_image":
                            # Base64 or URL image
                            image_url = content_item.get("image_url", "")
                            if isinstance(image_url, dict):
                                image_url = image_url.get("url", "")
                            if image_url.startswith("data:"):
                                try:
                                    header, data = image_url.split(",", 1)
                                    mime_type = header.split(":")[1].split(";")[0]
                                    parts.append({
                                        "inlineData": {"mimeType": mime_type, "data": data}
                                    })
                                except (ValueError, IndexError):
                                    pass
                            elif image_url:
                                parts.append({
                                    "fileData": {"fileUri": image_url, "mimeType": "image/jpeg"}
                                })
                        elif ct == "input_file":
                            # File input
                            file_data = content_item.get("file", {})
                            if file_data:
                                # Could be URL or base64
                                pass
                        elif ct == "refusal":
                            # Model refusal - pass through
                            parts.append({"text": content_item.get("refusal", "[refusal]")})
                    
                    if parts:
                        contents.append({"role": role, "parts": parts})
                
                elif item_type == "function_call":
                    # Previous function call from assistant
                    parts = [{
                        "functionCall": {
                            "name": item.get("name", ""),
                            "args": json.loads(item.get("arguments", "{}")) if isinstance(item.get("arguments"), str) else item.get("arguments", {}),
                            "id": item.get("call_id", item.get("id", ""))
                        }
                    }]
                    contents.append({"role": "model", "parts": parts})
                
                elif item_type == "function_call_output":
                    # Function result from user
                    output = item.get("output", "")
                    if not isinstance(output, str):
                        output = json.dumps(output)
                    call_id = item.get("call_id", "")
                    contents.append({
                        "role": "user",
                        "parts": [{
                            "functionResponse": {
                                "name": call_id,
                                "response": {"output": output},
                                "id": call_id
                            }
                        }]
                    })
                
                elif item_type == "reasoning":
                    # Reasoning block
                    summary = item.get("summary", [])
                    if summary:
                        text = " ".join(s.get("text", "") for s in summary if s.get("type") == "summary_text")
                        if text:
                            contents.append({
                                "role": "model",
                                "parts": [{"reasoning": text}]
                            })
        
        genai_request = {
            "model": request.get("model", "auto"),
            "contents": contents,
            "config": {}
        }
        
        # Add system instruction
        if system_instruction:
            genai_request["config"]["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }
        
        # Handle tools (Responses API format)
        tools = request.get("tools", [])
        if tools:
            func_decls = []
            for tool in tools:
                tool_type = tool.get("type", "")
                if tool_type == "function":
                    # Can have nested function object or flat structure
                    func = tool.get("function", tool)
                    name = func.get("name", tool.get("name", ""))
                    func_decls.append({
                        "name": name,
                        "description": func.get("description", tool.get("description", "")),
                        "parameters": func.get("parameters", tool.get("parameters", {}))
                    })
            if func_decls:
                genai_request["config"]["tools"] = [{"functionDeclarations": func_decls}]
        
        # Handle max_output_tokens
        max_tokens = request.get("max_output_tokens")
        if max_tokens:
            genai_request["config"]["maxOutputTokens"] = max_tokens
        
        # Handle temperature
        temperature = request.get("temperature")
        if temperature is not None:
            genai_request["config"]["temperature"] = temperature
        
        # Handle top_p
        top_p = request.get("top_p")
        if top_p is not None:
            genai_request["config"]["topP"] = top_p
        
        # Handle reasoning
        reasoning = request.get("reasoning")
        if reasoning:
            if isinstance(reasoning, dict) and reasoning.get("effort"):
                # Map reasoning effort to thinking config
                effort = reasoning.get("effort", "medium")
                budget = {"low": 5000, "medium": 10000, "high": 20000}.get(effort, 10000)
                genai_request["config"]["thinkingConfig"] = {"thinkingBudget": budget}
        
        return genai_request
    
    @staticmethod
    def genai_to_openai_responses(response: Dict[str, Any], model: str, request_id: str = None) -> Dict[str, Any]:
        """Convert GenAI response to OpenAI Responses API format
        
        The Responses API has a rich output structure:
        - output: array of output items (messages, function_calls, reasoning, etc.)
        - usage: token counts
        - status: completed/incomplete/failed
        """
        candidate = response.get("candidates", [{}])[0]
        parts = candidate.get("content", {}).get("parts", [])
        
        output = []
        output_text_parts = []
        function_calls = []
        
        for idx, part in enumerate(parts):
            if "text" in part:
                output_text_parts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                call_id = fc.get("id") or generate_id("call_")
                function_calls.append({
                    "type": "function_call",
                    "id": generate_id("fc_"),
                    "call_id": call_id,
                    "name": fc.get("name", ""),
                    "arguments": json.dumps(fc.get("args", {}))
                })
            elif "reasoning" in part:
                output.append({
                    "type": "reasoning",
                    "id": generate_id("rs_"),
                    "summary": [{"type": "summary_text", "text": part["reasoning"]}]
                })
        
        # Add message output if there's text
        if output_text_parts:
            msg_id = generate_id("msg_")
            output.append({
                "type": "message",
                "id": msg_id,
                "role": "assistant",
                "status": "completed",
                "content": [{
                    "type": "output_text",
                    "text": "".join(output_text_parts),
                    "annotations": []
                }]
            })
        
        # Add function calls after message
        output.extend(function_calls)
        
        usage = response.get("usageMetadata", {})
        prompt_tokens = usage.get("promptTokenCount", 0)
        output_tokens = usage.get("candidatesTokenCount", 0)
        
        # Determine status
        finish_reason = candidate.get("finishReason", "STOP")
        status = "completed"
        incomplete_details = None
        if finish_reason == "MAX_TOKENS":
            status = "incomplete"
            incomplete_details = {"reason": "max_output_tokens"}
        elif finish_reason == "SAFETY":
            status = "failed"
        
        # Generate response ID
        resp_id = request_id or generate_id("resp_")
        
        result = {
            "id": resp_id,
            "object": "response",
            "created_at": int(time.time()),
            "model": model,
            "status": status,
            "output": output if output else [{
                "type": "message",
                "id": generate_id("msg_"),
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "", "annotations": []}]
            }],
            "usage": {
                "input_tokens": prompt_tokens,
                "output_tokens": output_tokens,
                "total_tokens": prompt_tokens + output_tokens
            },
            "error": None
        }
        
        if incomplete_details:
            result["incomplete_details"] = incomplete_details
        
        return result
