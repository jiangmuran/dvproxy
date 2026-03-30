"""
DVProxy - FastAPI Application
Main application entry point with all routers and middleware configured
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
import logging
import os
import jinja2

from app.config import settings
from app.models.db import init_db, get_db
from app.routers import anthropic, openai, admin


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("dvproxy")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    logger.info("Starting DVProxy...")
    await init_db()
    logger.info("Database initialized")
    
    yield
    
    logger.info("Shutting down DVProxy...")


# Create FastAPI application
app = FastAPI(
    title="DVProxy",
    description="Anthropic/OpenAI to DeepVLab GenAI Proxy Server",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS - restrict in production
allowed_origins = os.environ.get("DVPROXY_CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)


# Include routers
app.include_router(anthropic.router)
app.include_router(openai.router)
app.include_router(admin.router)


# Mount static files if directory exists
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


# Setup templates - fix Jinja2 cache compatibility issue
templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = None
if os.path.exists(templates_dir):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(templates_dir),
        autoescape=True,
        auto_reload=settings.debug,
    )
    templates = Jinja2Templates(env=env)


@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "name": "DVProxy",
        "version": "1.0.0",
        "description": "Anthropic/OpenAI to DeepVLab GenAI Proxy Server",
        "endpoints": {
            "anthropic": "/v1/messages",
            "openai": "/v1/chat/completions",
            "responses": "/v1/responses",
            "admin": "/admin"
        },
        "docs": "/docs"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "dvproxy"}


@app.get("/v1/models")
async def list_models(request: Request):
    """List available models (OpenAI compatible)
    
    Fetches models from the upstream DeepVLab server and converts
    them to OpenAI-compatible format.
    """
    import httpx
    from app.routers.admin import get_deepvlab_access_token
    
    try:
        access_token = get_deepvlab_access_token()
        
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "DVProxy/1.0.0",
        }
        
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        
        auth_header = request.headers.get("authorization")
        if auth_header and not access_token:
            headers["Authorization"] = auth_header
        
        proxy_server_url = settings.upstream_base_url.rstrip('/v1/chat')
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{proxy_server_url}/web-api/models",
                headers=headers
            )
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get("success") and data.get("data"):
                    models = []
                    for model in data["data"]:
                        models.append({
                            "id": model.get("name", "unknown"),
                            "object": "model",
                            "created": 1700000000,
                            "owned_by": "deepvlab",
                            "permission": [],
                            "root": model.get("name", "unknown"),
                            "parent": None,
                            "display_name": model.get("displayName", model.get("name")),
                            "available": model.get("available", True),
                            "max_tokens": model.get("maxToken", 0),
                            "credits_per_request": model.get("creditsPerRequest", 0),
                        })
                    
                    models.insert(0, {
                        "id": "auto",
                        "object": "model",
                        "created": 1700000000,
                        "owned_by": "deepvlab",
                        "permission": [],
                        "root": "auto",
                        "parent": None,
                        "display_name": "Auto (Server decides)",
                        "available": True,
                    })
                    
                    return {"object": "list", "data": models}
            
            logger.warning(f"Failed to fetch models from upstream: {response.status_code}")
            
    except Exception as e:
        logger.warning(f"Error fetching models from upstream: {e}")
    
    return {
        "object": "list",
        "data": [{
            "id": "auto",
            "object": "model",
            "created": 1700000000,
            "owned_by": "deepvlab",
            "permission": [],
            "root": "auto",
            "parent": None,
            "display_name": "Auto (Server decides)",
            "available": True,
        }]
    }


@app.get("/web-api/models")
async def web_api_models(request: Request):
    """DeepVCode compatible models endpoint"""
    import httpx
    from app.routers.admin import get_deepvlab_access_token
    
    try:
        access_token = get_deepvlab_access_token()
        
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "DVProxy/1.0.0",
        }
        
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        
        auth_header = request.headers.get("authorization")
        if auth_header and not access_token:
            headers["Authorization"] = auth_header
        
        proxy_server_url = settings.upstream_base_url.rstrip('/v1/chat')
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{proxy_server_url}/web-api/models",
                headers=headers
            )
            
            if response.status_code == 200:
                return response.json()
            
            logger.warning(f"Upstream models API returned {response.status_code}")
            
    except Exception as e:
        logger.warning(f"Error proxying models request: {e}")
    
    return {
        "success": False,
        "message": "Failed to fetch models from upstream server. Please login first.",
        "data": []
    }


@app.get("/admin/panel", response_class=HTMLResponse)
async def admin_panel(request: Request):
    """Serve admin panel HTML - dashboard page"""
    if templates:
        return templates.TemplateResponse(request, "dashboard.html")
    else:
        return HTMLResponse(_get_fallback_html())


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    """Serve admin login page"""
    if templates:
        return templates.TemplateResponse(request, "login.html")
    else:
        return HTMLResponse(_get_fallback_html())


def _get_fallback_html():
    """Fallback HTML when templates are not configured"""
    return """<!DOCTYPE html>
<html><head><title>DVProxy Admin</title></head>
<body>
    <h1>DVProxy Admin Panel</h1>
    <p>Templates not configured. Use the <a href="/docs">API docs</a>.</p>
</body></html>"""


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "type": "internal_error",
                "message": "An internal error occurred"
            }
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug
    )
