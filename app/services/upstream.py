"""
DVProxy - Upstream Client
Handles communication with DeepVLab upstream API
"""
import json
import time
import platform
import httpx
import logging
from typing import Any, Dict, AsyncGenerator, Optional
from app.config import settings

logger = logging.getLogger("dvproxy.upstream")


class UpstreamError(Exception):
    """Raised when the upstream returns a non-2xx response.

    Carries the parsed error payload so routers can pass it through to clients
    without losing the original error type / message.
    """

    def __init__(self, status_code: int, error: Dict[str, Any], raw: bytes = b""):
        self.status_code = status_code
        self.error = error          # parsed JSON error dict (may be empty)
        self.raw = raw              # raw response bytes for fallback
        message = error.get("message") or error.get("error") or raw.decode(errors="replace")[:200]
        super().__init__(message)


class UpstreamClient:
    """Client for communicating with DeepVLab upstream API
    
    Features:
    - Mimics DeepVCode client headers
    - Supports both streaming and non-streaming requests
    - Thread-safe credential access
    - Automatic error handling and logging
    """
    
    def __init__(self, token: Optional[str] = None):
        self.base_url = settings.upstream_url
        self.token = token
        self.timeout = httpx.Timeout(300.0, connect=30.0)
    
    def _get_token(self) -> Optional[str]:
        """Get the token to use for authentication
        
        Priority:
        1. Instance token (passed during init)
        2. DeepVLab JWT token (from credential store)
        3. Static upstream token from settings
        """
        if self.token:
            return self.token
        
        # Try to get DeepVLab token (sync version for headers)
        try:
            from app.services.credentials import get_deepvlab_access_token_sync
            dv_token = get_deepvlab_access_token_sync()
            if dv_token:
                return dv_token
        except Exception:
            pass
        
        # Fall back to static token
        return settings.upstream_token
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers that mimic DeepVCode client"""
        token = self._get_token()
        headers = {
            "Content-Type": "application/json",
            "X-Client-Version": settings.client_version,
            "User-Agent": f"DeepVCode/CLI/{settings.client_version} ({platform.system()}; {platform.machine()})",
        }
        
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            logger.warning("No authentication token available for upstream request")
        
        return headers
    
    async def chat_messages(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Call /v1/chat/messages endpoint (non-streaming)
        
        Args:
            request: GenAI format request body
            
        Returns:
            GenAI format response
            
        Raises:
            httpx.HTTPStatusError: On API errors
        """
        logger.debug(f"Sending non-streaming request to upstream")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/messages",
                json=request,
                headers=self._get_headers()
            )

            if response.status_code != 200:
                raw = response.content
                logger.error(f"Upstream error: {response.status_code} - {raw[:500]}")
                try:
                    error_body = json.loads(raw)
                except Exception:
                    error_body = {}
                raise UpstreamError(response.status_code, error_body, raw)

            return response.json()
    
    async def chat_stream(self, request: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        """Call /v1/chat/stream endpoint (streaming)
        
        Args:
            request: GenAI format request body
            
        Yields:
            Parsed JSON objects from SSE stream
        """
        # Ensure stream is enabled in config
        if "config" not in request:
            request["config"] = {}
        request["config"]["stream"] = True
        
        logger.debug(f"Starting streaming request to upstream")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/chat/stream",
                json=request,
                headers={
                    **self._get_headers(),
                    "Accept": "text/event-stream",
                    "Cache-Control": "no-cache",
                }
            ) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    logger.error(f"Upstream stream error: {response.status_code} - {error_body[:500]}")
                    try:
                        parsed_error = json.loads(error_body)
                    except Exception:
                        parsed_error = {}
                    raise UpstreamError(response.status_code, parsed_error, error_body)
                
                buffer = ""
                chunk_count = 0
                
                async for chunk in response.aiter_text():
                    buffer += chunk
                    lines = buffer.split("\n")
                    buffer = lines.pop()
                    
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                logger.debug(f"Stream completed with {chunk_count} chunks")
                                return
                            
                            try:
                                parsed = json.loads(data)
                                chunk_count += 1
                                yield parsed
                            except json.JSONDecodeError as e:
                                logger.warning(f"Failed to parse SSE data: {e}")
                
                # Process any remaining data in buffer
                if buffer.strip():
                    if buffer.strip().startswith("data: "):
                        data = buffer.strip()[6:]
                        if data != "[DONE]":
                            try:
                                parsed = json.loads(data)
                                yield parsed
                            except json.JSONDecodeError:
                                pass
    
    async def count_tokens(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Call /v1/chat/count-tokens endpoint
        
        Args:
            request: GenAI format request body
            
        Returns:
            Token count response
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/count-tokens",
                json=request,
                headers=self._get_headers()
            )
            response.raise_for_status()
            return response.json()
    
    async def health_check(self) -> bool:
        """Check if upstream is reachable
        
        Returns:
            True if upstream is healthy
        """
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                response = await client.get(
                    f"{self.base_url.rstrip('/v1/chat')}/health",
                    headers={"User-Agent": "DVProxy/1.0.0"}
                )
                return response.status_code == 200
        except Exception as e:
            logger.warning(f"Upstream health check failed: {e}")
            return False
