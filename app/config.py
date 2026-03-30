"""
DVProxy - Configuration
"""
import secrets
import os
from pydantic_settings import BaseSettings
from typing import Optional


def _get_or_create_jwt_secret() -> str:
    """Get JWT secret from env or file, create if not exists
    
    Ensures JWT tokens survive server restarts.
    """
    # Check env var first
    secret = os.environ.get("DVPROXY_JWT_SECRET")
    if secret:
        return secret
    
    # Check file in data directory
    secret_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".jwt_secret")
    if os.path.exists(secret_file):
        try:
            with open(secret_file, 'r') as f:
                secret = f.read().strip()
                if secret:
                    return secret
        except Exception:
            pass
    
    # Generate new secret and persist
    secret = secrets.token_urlsafe(32)
    try:
        with open(secret_file, 'w') as f:
            f.write(secret)
        os.chmod(secret_file, 0o600)
    except Exception:
        pass  # Non-critical, will generate new on next restart
    
    return secret


class Settings(BaseSettings):
    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False
    
    # Database
    database_url: str = "sqlite+aiosqlite:///./dvproxy.db"
    
    # Upstream DeepVLab API
    upstream_url: str = "https://api-code.deepvlab.ai"
    upstream_base_url: str = "https://api-code.deepvlab.ai"
    upstream_token: Optional[str] = None
    
    # Admin credentials
    admin_username: str = "jmr"
    # TOTP secret for admin (base32 encoded) - set via DVPROXY_TOTP_SECRET env var
    totp_secret: str = "JBSWY3DPEHPK3PXP"
    
    # JWT for session - persisted across restarts
    jwt_secret: str = _get_or_create_jwt_secret()
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 24 hours
    
    # Client version to mimic DeepVCode
    client_version: str = "1.0.93"
    
    class Config:
        env_file = ".env"
        env_prefix = "DVPROXY_"


settings = Settings()
