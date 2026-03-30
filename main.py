#!/usr/bin/env python3
"""
DVProxy - Entry Point
Run with: python main.py

Or with uvicorn directly:
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""
import sys
import os

# Add the dvproxy directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    import uvicorn
    from app.config import settings
    
    # Check if this is first run (no database exists)
    db_path = "./dvproxy.db"
    is_first_run = not os.path.exists(db_path)
    
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║                        DVProxy v1.0.0                        ║
║    Anthropic/OpenAI to DeepVLab GenAI Proxy Server           ║
╠══════════════════════════════════════════════════════════════╣
║  Endpoints:                                                  ║
║    • Anthropic:  POST /v1/messages                           ║
║    • OpenAI:     POST /v1/chat/completions                   ║
║    • Responses:  POST /v1/responses                          ║
║    • Admin:      /admin/*                                    ║
║                                                              ║
║  Admin Panel: http://{settings.host}:{settings.port}/admin/login
║  API Docs:    http://{settings.host}:{settings.port}/docs
╚══════════════════════════════════════════════════════════════╝
    """)
    
    # Show TOTP setup info only on first run or if explicitly requested
    if is_first_run or os.environ.get("DVPROXY_SHOW_TOTP"):
        print("""
╔══════════════════════════════════════════════════════════════╗
║  FIRST RUN - TOTP SETUP                                      ║
║  ----------------------------------------------------------- ║
║  Add this secret to your authenticator app:                  ║
║                                                              ║""")
        print(f"║  TOTP Secret: {settings.totp_secret}                        ║")
        print(f"║  Username:    {settings.admin_username}                                        ║")
        print("""║                                                              ║
║  Or set DVPROXY_TOTP_SECRET env var with your own secret.    ║
║  This message will only show once.                           ║
╚══════════════════════════════════════════════════════════════╝
        """)
    
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="info"
    )
