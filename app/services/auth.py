"""
DVProxy - Authentication Service
Handles TOTP verification, JWT token management, and API key validation
"""
import pyotp
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status, Depends, Request, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.models.database import APIKey
from app.models.db import get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)


class AuthService:
    """Authentication service for admin and API keys"""
    
    @staticmethod
    def verify_totp(code: str) -> bool:
        """Verify TOTP code for admin login"""
        totp = pyotp.TOTP(settings.totp_secret)
        return totp.verify(code, valid_window=1)
    
    @staticmethod
    def get_totp_uri() -> str:
        """Get TOTP provisioning URI for QR code"""
        totp = pyotp.TOTP(settings.totp_secret)
        return totp.provisioning_uri(
            name=settings.admin_username,
            issuer_name="DVProxy"
        )
    
    @staticmethod
    def create_admin_token(username: str = None) -> str:
        """Create JWT token for admin session"""
        expire = datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes)
        to_encode = {
            "sub": username or settings.admin_username,
            "exp": expire,
            "type": "admin"
        }
        return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    
    @staticmethod
    def verify_admin_token(token: str) -> Optional[dict]:
        """Verify admin JWT token"""
        try:
            payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            if payload.get("type") != "admin":
                return None
            return payload
        except JWTError:
            return None
    
    @staticmethod
    def get_token_from_header(
        authorization: Optional[str] = Header(None)
    ) -> str:
        """Extract token from Authorization header"""
        if not authorization:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authorization header required",
                headers={"WWW-Authenticate": "Bearer"}
            )
        
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authorization header format",
                headers={"WWW-Authenticate": "Bearer"}
            )
        
        return token
    
    @staticmethod
    async def verify_api_key(key: str, db: AsyncSession) -> Optional[APIKey]:
        """Verify API key and return the key object"""
        result = await db.execute(
            select(APIKey).where(APIKey.key == key, APIKey.is_active == True)
        )
        api_key = result.scalar_one_or_none()
        
        if not api_key:
            return None
        
        # Check expiration if set
        if hasattr(api_key, 'expires_at') and api_key.expires_at:
            if api_key.expires_at < datetime.utcnow():
                return None
        
        # Check quota if set
        if api_key.quota_limit is not None and api_key.quota_used >= api_key.quota_limit:
            return None
        
        return api_key


async def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """Dependency to get current admin user"""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    payload = AuthService.verify_admin_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )
    
    return payload


async def get_api_key(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> APIKey:
    """Dependency to get and verify API key from request"""
    # Try Authorization header first
    auth_header = request.headers.get("Authorization", "")
    
    api_key_str = None
    
    if auth_header.startswith("Bearer "):
        api_key_str = auth_header[7:]
    elif auth_header.startswith("x-api-key "):
        api_key_str = auth_header[10:]
    
    # Also check x-api-key header
    if not api_key_str:
        api_key_str = request.headers.get("x-api-key")
    
    if not api_key_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required"
        )
    
    api_key = await AuthService.verify_api_key(api_key_str, db)
    
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key or quota exceeded"
        )
    
    return api_key
