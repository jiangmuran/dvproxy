"""
DVProxy - Admin Panel API Router
Handles admin authentication and API key management

Features:
- TOTP-based authentication with QR code generation
- JWT tokens for admin sessions
- CRUD operations for API keys
- Usage statistics and analytics
"""
import io
import base64
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.config import settings
from app.models.database import APIKey, UsageLog, DailyStats
from app.models.db import get_db
from app.services.auth import AuthService


router = APIRouter(prefix="/admin", tags=["Admin"])


# ==================== Request/Response Models ====================

class LoginRequest(BaseModel):
    username: str
    totp_code: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600


class APIKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    quota_limit: Optional[int] = None  # None = unlimited
    rate_limit: Optional[int] = 60  # requests per minute
    is_active: bool = True


class APIKeyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    quota_limit: Optional[int] = None
    rate_limit: Optional[int] = None
    is_active: Optional[bool] = None


class APIKeyResponse(BaseModel):
    id: int
    key: str
    name: str
    description: Optional[str]
    quota_limit: Optional[int]
    quota_used: int
    rate_limit: int
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime]
    
    class Config:
        from_attributes = True


class GlobalStats(BaseModel):
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_cached_tokens: int
    total_cost_estimate: float
    requests_today: int
    requests_this_week: int
    active_keys: int
    unique_ips: int


class KeyStats(BaseModel):
    key_id: int
    key_name: str
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_cached_tokens: int
    total_cost_estimate: float
    requests_today: int
    avg_latency_ms: float
    error_rate: float


class TrendPoint(BaseModel):
    date: str
    requests: int
    input_tokens: int
    output_tokens: int
    cost_estimate: float


class ModelBreakdown(BaseModel):
    model: str
    requests: int
    input_tokens: int
    output_tokens: int
    cost_estimate: float


class IPBreakdown(BaseModel):
    ip_address: str
    requests: int
    last_seen: datetime


# ==================== Dependencies ====================

async def get_current_admin(
    token: str = Depends(AuthService.get_token_from_header)
) -> str:
    """Verify admin JWT token"""
    payload = AuthService.verify_admin_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return payload.get("sub")


# ==================== Rate Limiting ====================

import time as _time
from collections import defaultdict as _defaultdict

# Simple in-memory rate limiter (per-IP)
_login_attempts: dict = {}  # ip -> [(timestamp, success), ...]
_LOGIN_WINDOW = 300  # 5 minutes
_LOGIN_MAX_ATTEMPTS = 10  # Max 10 failed attempts per window
_LOGIN_LOCKOUT_SECONDS = 60  # 1 minute lockout after exceeding


def _check_login_rate_limit(ip: str) -> bool:
    """Check if IP is rate limited for login attempts
    
    Returns True if allowed, False if rate limited
    """
    now = _time.time()
    
    # Clean old entries
    if ip in _login_attempts:
        _login_attempts[ip] = [
            (t, s) for t, s in _login_attempts[ip]
            if now - t < _LOGIN_WINDOW
        ]
    
    attempts = _login_attempts.get(ip, [])
    failed = [t for t, s in attempts if not s]
    
    if len(failed) >= _LOGIN_MAX_ATTEMPTS:
        # Check if lockout period has passed
        last_failed = max(failed)
        if now - last_failed < _LOGIN_LOCKOUT_SECONDS:
            return False
    
    return True


def _record_login_attempt(ip: str, success: bool) -> None:
    """Record a login attempt"""
    now = _time.time()
    if ip not in _login_attempts:
        _login_attempts[ip] = []
    _login_attempts[ip].append((now, success))


# ==================== Auth Endpoints ====================

@router.post("/login", response_model=LoginResponse)
async def admin_login(request: LoginRequest, req: Request):
    """Admin login with TOTP verification
    
    Rate limited: Max 10 failed attempts per 5 minutes per IP.
    """
    ip = req.client.host if req.client else "unknown"
    
    # Check rate limit
    if not _check_login_rate_limit(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please wait before retrying.",
            headers={"Retry-After": str(_LOGIN_LOCKOUT_SECONDS)}
        )
    
    # Verify username
    if request.username != settings.admin_username:
        _record_login_attempt(ip, success=False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )
    
    # Verify TOTP
    if not AuthService.verify_totp(request.totp_code):
        _record_login_attempt(ip, success=False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid TOTP code"
        )
    
    # Record successful login
    _record_login_attempt(ip, success=True)
    
    # Generate JWT token
    token = AuthService.create_admin_token(request.username)
    
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=3600
    )


@router.get("/totp-qr")
async def get_totp_qr(admin: str = Depends(get_current_admin)):
    """Generate TOTP QR code for setup
    
    SECURITY: This endpoint requires admin authentication.
    Only returns QR code image, never exposes the secret in response.
    """
    try:
        import pyotp
        import qrcode
        
        # Generate provisioning URI
        totp = pyotp.TOTP(settings.totp_secret)
        uri = totp.provisioning_uri(
            name=settings.admin_username,
            issuer_name="DVProxy"
        )
        
        # Generate QR code
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(uri)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Convert to base64
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        img_str = base64.b64encode(buffer.getvalue()).decode()
        
        # Only return QR code, NEVER expose secret in API response
        return {
            "qr_code": f"data:image/png;base64,{img_str}",
            "message": "Scan this QR code with your authenticator app"
        }
    except ImportError:
        return {
            "error": "QR code generation not available. Install: pip install qrcode[pil]",
            "message": "Please check server logs for TOTP secret during initial setup"
        }


@router.get("/verify")
async def verify_token(admin: str = Depends(get_current_admin)):
    """Verify the current admin token"""
    return {"valid": True, "username": admin}


# ==================== API Key Management ====================

@router.get("/keys", response_model=List[APIKeyResponse])
async def list_api_keys(
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """List all API keys"""
    result = await db.execute(select(APIKey).order_by(APIKey.created_at.desc()))
    keys = result.scalars().all()
    return keys


@router.post("/keys", response_model=APIKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    key_data: APIKeyCreate,
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Create a new API key"""
    import secrets
    
    # Generate unique API key
    key_value = f"dvp_{secrets.token_urlsafe(32)}"
    
    new_key = APIKey(
        key=key_value,
        name=key_data.name,
        description=key_data.description,
        quota_limit=key_data.quota_limit,
        rate_limit=key_data.rate_limit or 60,
        is_active=key_data.is_active
    )
    
    db.add(new_key)
    await db.commit()
    await db.refresh(new_key)
    
    return new_key


@router.get("/keys/{key_id}", response_model=APIKeyResponse)
async def get_api_key(
    key_id: int,
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get a specific API key"""
    result = await db.execute(select(APIKey).where(APIKey.id == key_id))
    key = result.scalar_one_or_none()
    
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")
    
    return key


@router.put("/keys/{key_id}", response_model=APIKeyResponse)
async def update_api_key(
    key_id: int,
    key_data: APIKeyUpdate,
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Update an API key"""
    result = await db.execute(select(APIKey).where(APIKey.id == key_id))
    key = result.scalar_one_or_none()
    
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")
    
    # Update fields if provided
    if key_data.name is not None:
        key.name = key_data.name
    if key_data.description is not None:
        key.description = key_data.description
    if key_data.quota_limit is not None:
        key.quota_limit = key_data.quota_limit
    if key_data.rate_limit is not None:
        key.rate_limit = key_data.rate_limit
    if key_data.is_active is not None:
        key.is_active = key_data.is_active
    
    await db.commit()
    await db.refresh(key)
    
    return key


@router.delete("/keys/{key_id}")
async def delete_api_key(
    key_id: int,
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Delete an API key"""
    result = await db.execute(select(APIKey).where(APIKey.id == key_id))
    key = result.scalar_one_or_none()
    
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")
    
    await db.delete(key)
    await db.commit()
    
    return {"deleted": True, "id": key_id}


@router.post("/keys/{key_id}/regenerate", response_model=APIKeyResponse)
async def regenerate_api_key(
    key_id: int,
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Regenerate an API key value"""
    import secrets
    
    result = await db.execute(select(APIKey).where(APIKey.id == key_id))
    key = result.scalar_one_or_none()
    
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")
    
    # Generate new key value
    key.key = f"dvp_{secrets.token_urlsafe(32)}"
    
    await db.commit()
    await db.refresh(key)
    
    return key


# ==================== Statistics Endpoints ====================

@router.get("/stats/global", response_model=GlobalStats)
async def get_global_stats(
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get global usage statistics"""
    # Total stats
    total_result = await db.execute(
        select(
            func.count(UsageLog.id).label("total_requests"),
            func.sum(UsageLog.input_tokens).label("total_input"),
            func.sum(UsageLog.output_tokens).label("total_output"),
            func.sum(UsageLog.cached_tokens).label("total_cached"),
            func.sum(UsageLog.cost_estimate).label("total_cost")
        )
    )
    total = total_result.one()
    
    # Today's stats
    today = datetime.utcnow().date()
    today_result = await db.execute(
        select(func.count(UsageLog.id)).where(
            func.date(UsageLog.created_at) == today
        )
    )
    requests_today = today_result.scalar() or 0
    
    # This week's stats
    week_ago = today - timedelta(days=7)
    week_result = await db.execute(
        select(func.count(UsageLog.id)).where(
            func.date(UsageLog.created_at) >= week_ago
        )
    )
    requests_this_week = week_result.scalar() or 0
    
    # Active keys count
    keys_result = await db.execute(
        select(func.count(APIKey.id)).where(APIKey.is_active == True)
    )
    active_keys = keys_result.scalar() or 0
    
    # Unique IPs
    ips_result = await db.execute(
        select(func.count(func.distinct(UsageLog.ip_address)))
    )
    unique_ips = ips_result.scalar() or 0
    
    return GlobalStats(
        total_requests=total.total_requests or 0,
        total_input_tokens=total.total_input or 0,
        total_output_tokens=total.total_output or 0,
        total_cached_tokens=total.total_cached or 0,
        total_cost_estimate=float(total.total_cost or 0),
        requests_today=requests_today,
        requests_this_week=requests_this_week,
        active_keys=active_keys,
        unique_ips=unique_ips
    )


@router.get("/stats/key/{key_id}", response_model=KeyStats)
async def get_key_stats(
    key_id: int,
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get statistics for a specific API key"""
    # Verify key exists
    key_result = await db.execute(select(APIKey).where(APIKey.id == key_id))
    key = key_result.scalar_one_or_none()
    
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")
    
    # Get stats
    stats_result = await db.execute(
        select(
            func.count(UsageLog.id).label("total_requests"),
            func.sum(UsageLog.input_tokens).label("total_input"),
            func.sum(UsageLog.output_tokens).label("total_output"),
            func.sum(UsageLog.cached_tokens).label("total_cached"),
            func.sum(UsageLog.cost_estimate).label("total_cost"),
            func.avg(UsageLog.latency_ms).label("avg_latency")
        ).where(UsageLog.api_key_id == key_id)
    )
    stats = stats_result.one()
    
    # Today's requests
    today = datetime.utcnow().date()
    today_result = await db.execute(
        select(func.count(UsageLog.id)).where(
            and_(
                UsageLog.api_key_id == key_id,
                func.date(UsageLog.created_at) == today
            )
        )
    )
    requests_today = today_result.scalar() or 0
    
    # Error rate
    errors_result = await db.execute(
        select(func.count(UsageLog.id)).where(
            and_(
                UsageLog.api_key_id == key_id,
                UsageLog.success == False
            )
        )
    )
    errors = errors_result.scalar() or 0
    total = stats.total_requests or 1
    error_rate = (errors / total) * 100 if total > 0 else 0
    
    return KeyStats(
        key_id=key_id,
        key_name=key.name,
        total_requests=stats.total_requests or 0,
        total_input_tokens=stats.total_input or 0,
        total_output_tokens=stats.total_output or 0,
        total_cached_tokens=stats.total_cached or 0,
        total_cost_estimate=float(stats.total_cost or 0),
        requests_today=requests_today,
        avg_latency_ms=float(stats.avg_latency or 0),
        error_rate=error_rate
    )


@router.get("/stats/trend", response_model=List[TrendPoint])
async def get_usage_trend(
    days: int = 30,
    key_id: Optional[int] = None,
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get usage trend over time"""
    # Get daily stats
    start_date = datetime.utcnow().date() - timedelta(days=days)
    
    query = select(
        DailyStats.date,
        func.sum(DailyStats.request_count).label("requests"),
        func.sum(DailyStats.input_tokens).label("input_tokens"),
        func.sum(DailyStats.output_tokens).label("output_tokens"),
        func.sum(DailyStats.cost_estimate).label("cost")
    ).where(DailyStats.date >= start_date)
    
    if key_id:
        query = query.where(DailyStats.api_key_id == key_id)
    
    query = query.group_by(DailyStats.date).order_by(DailyStats.date)
    
    result = await db.execute(query)
    rows = result.all()
    
    return [
        TrendPoint(
            date=row.date.isoformat(),
            requests=row.requests or 0,
            input_tokens=row.input_tokens or 0,
            output_tokens=row.output_tokens or 0,
            cost_estimate=float(row.cost or 0)
        )
        for row in rows
    ]


@router.get("/stats/models", response_model=List[ModelBreakdown])
async def get_model_breakdown(
    days: int = 30,
    key_id: Optional[int] = None,
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get usage breakdown by model"""
    start_date = datetime.utcnow() - timedelta(days=days)
    
    query = select(
        UsageLog.model,
        func.count(UsageLog.id).label("requests"),
        func.sum(UsageLog.input_tokens).label("input_tokens"),
        func.sum(UsageLog.output_tokens).label("output_tokens"),
        func.sum(UsageLog.cost_estimate).label("cost")
    ).where(UsageLog.created_at >= start_date)
    
    if key_id:
        query = query.where(UsageLog.api_key_id == key_id)
    
    query = query.group_by(UsageLog.model).order_by(func.count(UsageLog.id).desc())
    
    result = await db.execute(query)
    rows = result.all()
    
    return [
        ModelBreakdown(
            model=row.model or "unknown",
            requests=row.requests,
            input_tokens=row.input_tokens or 0,
            output_tokens=row.output_tokens or 0,
            cost_estimate=float(row.cost or 0)
        )
        for row in rows
    ]


@router.get("/stats/ips", response_model=List[IPBreakdown])
async def get_ip_breakdown(
    days: int = 30,
    key_id: Optional[int] = None,
    limit: int = 50,
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get usage breakdown by IP address"""
    start_date = datetime.utcnow() - timedelta(days=days)
    
    query = select(
        UsageLog.ip_address,
        func.count(UsageLog.id).label("requests"),
        func.max(UsageLog.created_at).label("last_seen")
    ).where(
        and_(
            UsageLog.created_at >= start_date,
            UsageLog.ip_address.isnot(None)
        )
    )
    
    if key_id:
        query = query.where(UsageLog.api_key_id == key_id)
    
    query = query.group_by(UsageLog.ip_address).order_by(
        func.count(UsageLog.id).desc()
    ).limit(limit)
    
    result = await db.execute(query)
    rows = result.all()
    
    return [
        IPBreakdown(
            ip_address=row.ip_address or "unknown",
            requests=row.requests,
            last_seen=row.last_seen
        )
        for row in rows
    ]


@router.get("/stats/endpoints")
async def get_endpoint_breakdown(
    days: int = 30,
    key_id: Optional[int] = None,
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get usage breakdown by endpoint (anthropic/openai/responses)"""
    start_date = datetime.utcnow() - timedelta(days=days)
    
    query = select(
        UsageLog.endpoint,
        func.count(UsageLog.id).label("requests"),
        func.sum(UsageLog.input_tokens).label("input_tokens"),
        func.sum(UsageLog.output_tokens).label("output_tokens"),
        func.avg(UsageLog.latency_ms).label("avg_latency")
    ).where(UsageLog.created_at >= start_date)
    
    if key_id:
        query = query.where(UsageLog.api_key_id == key_id)
    
    query = query.group_by(UsageLog.endpoint)
    
    result = await db.execute(query)
    rows = result.all()
    
    return [
        {
            "endpoint": row.endpoint or "unknown",
            "requests": row.requests,
            "input_tokens": row.input_tokens or 0,
            "output_tokens": row.output_tokens or 0,
            "avg_latency_ms": float(row.avg_latency or 0)
        }
        for row in rows
    ]


# ==================== DeepVLab Login Endpoints ====================

class DeepVLabLoginRequest(BaseModel):
    token: str
    user_id: str


class DeepVLabLoginResponse(BaseModel):
    success: bool
    user_id: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    error: Optional[str] = None


@router.post("/deepvlab/login", response_model=DeepVLabLoginResponse)
async def deepvlab_login(
    request: DeepVLabLoginRequest,
    admin: str = Depends(get_current_admin)
):
    """Process DeepVLab OAuth callback and store credentials
    
    This endpoint receives the token and user_id from the callback URL
    and validates them with the DeepVLab backend.
    
    Thread-safe: Uses CredentialStore for concurrent access.
    """
    import httpx
    from app.services.credentials import CredentialStore
    
    # Verify JWT token format (basic check)
    parts = request.token.split('.')
    if len(parts) != 3:
        return DeepVLabLoginResponse(
            success=False,
            error="Invalid token format"
        )
    
    try:
        # Exchange token with DeepVLab backend
        proxy_server_url = settings.upstream_base_url.rstrip('/v1/chat')
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{proxy_server_url}/auth/jwt/deepvlab-login",
                json={
                    "plat": "deepvlab",
                    "token": request.token,
                    "user_id": request.user_id,
                    "clientInfo": {
                        "platform": "dvproxy",
                        "version": "1.0.0",
                        "userAgent": "DVProxy/1.0.0"
                    }
                },
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "DVProxy/1.0.0"
                }
            )
            
            if response.status_code != 200:
                return DeepVLabLoginResponse(
                    success=False,
                    error=f"Backend authentication failed (HTTP {response.status_code})"
                )
            
            data = response.json()
        
        # Store credentials thread-safely
        await CredentialStore.save({
            "access_token": data.get("accessToken"),
            "refresh_token": data.get("refreshToken"),
            "expires_in": data.get("expiresIn", 900),
            "user": data.get("user", {}),
            "login_method": "deepvlab",
            "logged_in_at": datetime.utcnow().isoformat()
        })
        
        user = data.get("user", {})
        
        return DeepVLabLoginResponse(
            success=True,
            user_id=user.get("userId") or request.user_id,
            name=user.get("name", "DeepVLab User"),
            email=user.get("email", "")
        )
        
    except httpx.RequestError as e:
        return DeepVLabLoginResponse(
            success=False,
            error=f"Network error: {str(e)}"
        )
    except Exception as e:
        return DeepVLabLoginResponse(
            success=False,
            error=f"Login failed: {str(e)}"
        )


@router.post("/deepvlab/logout")
async def deepvlab_logout(admin: str = Depends(get_current_admin)):
    """Clear stored DeepVLab credentials"""
    from app.services.credentials import CredentialStore
    await CredentialStore.clear()
    return {"success": True, "message": "Credentials cleared"}


@router.get("/deepvlab/status")
async def deepvlab_status(admin: str = Depends(get_current_admin)):
    """Get current DeepVLab login status"""
    from app.services.credentials import CredentialStore
    
    is_logged_in = await CredentialStore.is_logged_in()
    
    if not is_logged_in:
        return {"logged_in": False, "user": None}
    
    user_info = await CredentialStore.get_user_info()
    return {
        "logged_in": True,
        "user": user_info,
        "login_method": user_info.get("login_method") if user_info else None
    }


def get_deepvlab_access_token() -> Optional[str]:
    """Get the current DeepVLab access token for upstream requests
    
    Uses sync version - returns None if credentials not loaded yet.
    For full thread-safe access, use CredentialStore.get_access_token() directly.
    """
    from app.services.credentials import get_deepvlab_access_token_sync
    return get_deepvlab_access_token_sync()


# ==================== Feishu Login Endpoints ====================

class FeishuAuthUrlResponse(BaseModel):
    success: bool
    auth_url: Optional[str] = None
    error: Optional[str] = None
    manual_mode: bool = False  # 是否需要手动模式
    manual_instructions: Optional[str] = None


class FeishuCallbackRequest(BaseModel):
    code: str
    redirect_uri: str = "http://localhost:7863/callback"


class FeishuLoginResponse(BaseModel):
    success: bool
    user_id: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    avatar: Optional[str] = None
    error: Optional[str] = None


@router.get("/feishu/auth-url", response_model=FeishuAuthUrlResponse)
async def get_feishu_auth_url(admin: str = Depends(get_current_admin)):
    """Generate Feishu OAuth authorization URL
    
    Returns an authorization URL that the user should open in a browser.
    If the backend is unreachable, returns manual mode instructions.
    """
    import httpx
    import secrets
    from urllib.parse import urlencode
    
    # Try to get Feishu app config from backend
    proxy_server_url = settings.upstream_base_url.rstrip('/v1/chat')
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{proxy_server_url}/api/config/feishu",
                headers={"User-Agent": "DVProxy/1.0.0"}
            )
            
            if response.status_code == 200:
                config = response.json()
                app_id = config.get("appId")
                
                if app_id:
                    # Build Feishu OAuth URL
                    state = secrets.token_hex(16)
                    redirect_uri = "http://localhost:7863/callback"
                    
                    params = {
                        "app_id": app_id,
                        "redirect_uri": redirect_uri,
                        "response_type": "code",
                        "scope": "contact:user.employee_id:readonly",
                        "state": state
                    }
                    
                    auth_url = f"https://open.feishu.cn/open-apis/authen/v1/authorize?{urlencode(params)}"
                    
                    return FeishuAuthUrlResponse(
                        success=True,
                        auth_url=auth_url,
                        manual_mode=False
                    )
    
    except Exception as e:
        logger.warning(f"Failed to get Feishu config: {e}")
    
    # Fallback to manual mode
    return FeishuAuthUrlResponse(
        success=True,
        auth_url=None,
        manual_mode=True,
        manual_instructions=(
            "无法自动获取飞书授权URL，请按以下步骤操作:\n"
            "1. 在本地 DeepVCode 中登录飞书账号\n"
            "2. 登录成功后，浏览器会跳转到一个 localhost URL\n"
            "3. 复制完整的回调URL（包含 code 参数）\n"
            "4. 粘贴到下方的回调URL输入框中\n"
            "5. 点击完成登录"
        )
    )


@router.post("/feishu/login", response_model=FeishuLoginResponse)
async def feishu_login(
    request: FeishuCallbackRequest,
    admin: str = Depends(get_current_admin)
):
    """Process Feishu OAuth callback
    
    Takes the authorization code from the callback URL and exchanges it
    for an access token and user info.
    """
    import httpx
    from app.services.credentials import CredentialStore
    
    try:
        proxy_server_url = settings.upstream_base_url.rstrip('/v1/chat')
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1: Exchange code for Feishu access token
            exchange_response = await client.post(
                f"{proxy_server_url}/api/auth/feishu/exchange",
                json={
                    "code": request.code,
                    "redirect_uri": request.redirect_uri
                },
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "DVProxy/1.0.0"
                }
            )
            
            if exchange_response.status_code != 200:
                return FeishuLoginResponse(
                    success=False,
                    error=f"Feishu token exchange failed (HTTP {exchange_response.status_code})"
                )
            
            exchange_data = exchange_response.json()
            if not exchange_data.get("success"):
                return FeishuLoginResponse(
                    success=False,
                    error=exchange_data.get("error", "Token exchange failed")
                )
            
            feishu_token = exchange_data.get("data", {}).get("accessToken")
            
            # Step 2: Exchange Feishu token for JWT
            jwt_response = await client.post(
                f"{proxy_server_url}/auth/jwt/feishu-login",
                json={
                    "feishuAccessToken": feishu_token,
                    "clientInfo": {
                        "platform": "dvproxy",
                        "version": "1.0.0",
                        "userAgent": "DVProxy/1.0.0"
                    }
                },
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "DVProxy/1.0.0"
                }
            )
            
            if jwt_response.status_code != 200:
                return FeishuLoginResponse(
                    success=False,
                    error=f"JWT exchange failed (HTTP {jwt_response.status_code})"
                )
            
            jwt_data = jwt_response.json()
        
        # Store credentials thread-safely
        user = jwt_data.get("user", {})
        await CredentialStore.save({
            "access_token": jwt_data.get("accessToken"),
            "refresh_token": jwt_data.get("refreshToken"),
            "expires_in": jwt_data.get("expiresIn", 900),
            "user": user,
            "login_method": "feishu",
            "logged_in_at": datetime.utcnow().isoformat()
        })
        
        return FeishuLoginResponse(
            success=True,
            user_id=user.get("userId") or user.get("openId"),
            name=user.get("name"),
            email=user.get("email"),
            avatar=user.get("avatar")
        )
        
    except httpx.RequestError as e:
        return FeishuLoginResponse(
            success=False,
            error=f"Network error: {str(e)}"
        )
    except Exception as e:
        return FeishuLoginResponse(
            success=False,
            error=f"Feishu login failed: {str(e)}"
        )


# ==================== Cheetah OA Login Endpoints ====================

class CheetahOALoginRequest(BaseModel):
    email: str
    password: str


class CheetahOALoginResponse(BaseModel):
    success: bool
    user_id: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    error: Optional[str] = None


@router.post("/cheetah/login", response_model=CheetahOALoginResponse)
async def cheetah_oa_login(
    request: CheetahOALoginRequest,
    admin: str = Depends(get_current_admin)
):
    """Login with Cheetah OA credentials (email/password)
    
    This is a direct username/password authentication without OAuth flow.
    """
    import httpx
    from app.services.credentials import CredentialStore
    
    if not request.email or not request.password:
        return CheetahOALoginResponse(
            success=False,
            error="Email and password are required"
        )
    
    try:
        proxy_server_url = settings.upstream_base_url.rstrip('/v1/chat')
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{proxy_server_url}/auth/jwt/cheetah-login",
                json={
                    "email": request.email,
                    "password": request.password,
                    "clientInfo": {
                        "platform": "dvproxy",
                        "version": "1.0.0",
                        "userAgent": "DVProxy/1.0.0"
                    }
                },
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "DVProxy/1.0.0"
                }
            )
            
            if response.status_code != 200:
                return CheetahOALoginResponse(
                    success=False,
                    error="Login failed. Please check your credentials."
                )
            
            jwt_data = response.json()
        
        # Store credentials thread-safely
        user = jwt_data.get("user", {})
        await CredentialStore.save({
            "access_token": jwt_data.get("accessToken"),
            "refresh_token": jwt_data.get("refreshToken"),
            "expires_in": jwt_data.get("expiresIn", 900),
            "user": user,
            "login_method": "cheetah_oa",
            "logged_in_at": datetime.utcnow().isoformat()
        })
        
        return CheetahOALoginResponse(
            success=True,
            user_id=user.get("userId") or user.get("openId"),
            name=user.get("name"),
            email=user.get("email") or request.email
        )
        
    except httpx.RequestError as e:
        return CheetahOALoginResponse(
            success=False,
            error=f"Network error: {str(e)}"
        )
    except Exception as e:
        return CheetahOALoginResponse(
            success=False,
            error=f"OA login failed: {str(e)}"
        )


# ==================== Check Login Methods Available ====================

@router.get("/login-methods")
async def get_login_methods(admin: str = Depends(get_current_admin)):
    """Check which login methods are available
    
    Returns availability of DeepVLab, Feishu, and Cheetah OA login.
    """
    import httpx
    
    methods = {
        "deepvlab": True,  # Always available
        "feishu": True,    # Force enable - user can get URL or paste callback
        "cheetah_oa": True  # Always available (username/password)
    }
    
    return {"methods": methods}


# ==================== Logs Page ====================

from fastapi.responses import FileResponse
from pathlib import Path

@router.get("/logs")
async def logs_page(admin: str = Depends(get_current_admin)):
    """Serve logs viewer HTML page"""
    template_path = Path(__file__).parent.parent / "templates" / "logs.html"
    return FileResponse(template_path, media_type="text/html")

@router.get("/dashboard")
async def dashboard_page(admin: str = Depends(get_current_admin)):
    """Serve admin dashboard HTML page"""
    template_path = Path(__file__).parent.parent / "templates" / "admin.html"
    return FileResponse(template_path, media_type="text/html")
