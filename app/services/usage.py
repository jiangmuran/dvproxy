"""
DVProxy - Usage Tracking Service
Handles logging, cost estimation, and analytics for API usage
"""
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc
from sqlalchemy.orm import selectinload

from app.models.database import APIKey, UsageLog, DailyStats


class UsageService:
    """Service for tracking and analyzing API usage"""
    
    @staticmethod
    async def log_usage(
        db: AsyncSession,
        api_key_id: int,
        endpoint: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        latency_ms: int = 0,
        success: bool = True,
        error_message: Optional[str] = None
    ) -> UsageLog:
        """Log a single API usage
        
        Thread-safe: Uses atomic SQL UPDATE for quota increment
        """
        from sqlalchemy import update as sql_update
        
        cost = UsageService._estimate_cost(model, input_tokens, output_tokens, cached_tokens)
        
        log = UsageLog(
            api_key_id=api_key_id,
            endpoint=endpoint,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cost_estimate=cost,
            ip_address=ip_address,
            user_agent=user_agent,
            latency_ms=latency_ms,
            success=success,
            error_message=error_message
        )
        
        db.add(log)
        
        # Atomic quota increment - prevents race conditions
        await db.execute(
            sql_update(APIKey)
            .where(APIKey.id == api_key_id)
            .values(
                quota_used=APIKey.quota_used + 1,
                last_used_at=datetime.utcnow()
            )
        )
        
        await db.commit()
        return log
    
    @staticmethod
    def _estimate_cost(model: str, input_tokens: int, output_tokens: int, cached_tokens: int) -> float:
        """Estimate cost based on model and tokens
        
        Pricing per 1M tokens (approximate):
        - Claude: $3/input, $15/output, $0.30/cached
        - GPT-4: $5/input, $15/output, $2.50/cached  
        - GPT-3.5: $0.50/input, $1.50/output
        - Gemini: $0.075/input, $0.30/output
        """
        pricing = {
            "claude": {"input": 3.0, "output": 15.0, "cached": 0.3},
            "gpt-4": {"input": 5.0, "output": 15.0, "cached": 2.5},
            "gpt-3.5": {"input": 0.5, "output": 1.5, "cached": 0.25},
            "gemini": {"input": 0.075, "output": 0.3, "cached": 0.01875},
            "default": {"input": 1.0, "output": 3.0, "cached": 0.1}
        }
        
        # Find matching pricing
        price = pricing["default"]
        model_lower = model.lower()
        for key in pricing:
            if key in model_lower:
                price = pricing[key]
                break
        
        # Calculate cost
        uncached_input = max(0, input_tokens - cached_tokens)
        cost = (
            (uncached_input / 1_000_000) * price["input"] +
            (cached_tokens / 1_000_000) * price["cached"] +
            (output_tokens / 1_000_000) * price["output"]
        )
        
        return round(cost, 6)
    
    @staticmethod
    async def get_key_stats(db: AsyncSession, api_key_id: int) -> Dict[str, Any]:
        """Get statistics for a specific API key"""
        # Get API key info
        result = await db.execute(select(APIKey).where(APIKey.id == api_key_id))
        api_key = result.scalar_one_or_none()
        
        if not api_key:
            return {}
        
        # Get total usage
        total_result = await db.execute(
            select(
                func.count(UsageLog.id).label("total_requests"),
                func.sum(UsageLog.input_tokens).label("total_input"),
                func.sum(UsageLog.output_tokens).label("total_output"),
                func.sum(UsageLog.cached_tokens).label("total_cached"),
                func.sum(UsageLog.cost_estimate).label("total_cost"),
                func.avg(UsageLog.latency_ms).label("avg_latency")
            ).where(UsageLog.api_key_id == api_key_id)
        )
        totals = total_result.first()
        
        # Get today's usage
        today = date.today()
        today_start = datetime.combine(today, datetime.min.time())
        
        today_result = await db.execute(
            select(
                func.count(UsageLog.id).label("requests"),
                func.sum(UsageLog.input_tokens).label("input"),
                func.sum(UsageLog.output_tokens).label("output"),
                func.sum(UsageLog.cost_estimate).label("cost")
            ).where(
                and_(
                    UsageLog.api_key_id == api_key_id,
                    UsageLog.created_at >= today_start
                )
            )
        )
        today_stats = today_result.first()
        
        return {
            "key_id": api_key.id,
            "key_name": api_key.name,
            "quota_limit": api_key.quota_limit,
            "quota_used": api_key.quota_used,
            "remaining_quota": (api_key.quota_limit - api_key.quota_used) if api_key.quota_limit else None,
            "total_requests": totals.total_requests or 0,
            "total_input_tokens": totals.total_input or 0,
            "total_output_tokens": totals.total_output or 0,
            "total_cached_tokens": totals.total_cached or 0,
            "total_cost": round(totals.total_cost or 0, 4),
            "avg_latency_ms": round(totals.avg_latency or 0, 2),
            "today_requests": today_stats.requests or 0,
            "today_input_tokens": today_stats.input or 0,
            "today_output_tokens": today_stats.output or 0,
            "today_cost": round(today_stats.cost or 0, 4)
        }
    
    @staticmethod
    async def get_usage_trend(
        db: AsyncSession,
        api_key_id: Optional[int] = None,
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """Get daily usage trend"""
        start_date = datetime.utcnow() - timedelta(days=days)
        
        query = select(
            func.date(UsageLog.created_at).label("date"),
            func.count(UsageLog.id).label("requests"),
            func.sum(UsageLog.input_tokens).label("input_tokens"),
            func.sum(UsageLog.output_tokens).label("output_tokens"),
            func.sum(UsageLog.cost_estimate).label("cost")
        ).where(UsageLog.created_at >= start_date)
        
        if api_key_id:
            query = query.where(UsageLog.api_key_id == api_key_id)
        
        query = query.group_by(func.date(UsageLog.created_at)).order_by(func.date(UsageLog.created_at))
        
        result = await db.execute(query)
        rows = result.all()
        
        return [
            {
                "date": str(row.date),
                "requests": row.requests,
                "input_tokens": row.input_tokens or 0,
                "output_tokens": row.output_tokens or 0,
                "cost": round(row.cost or 0, 4)
            }
            for row in rows
        ]
    
    @staticmethod
    async def get_model_breakdown(
        db: AsyncSession,
        api_key_id: Optional[int] = None,
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """Get usage breakdown by model"""
        start_date = datetime.utcnow() - timedelta(days=days)
        
        query = select(
            UsageLog.model,
            func.count(UsageLog.id).label("requests"),
            func.sum(UsageLog.input_tokens).label("input_tokens"),
            func.sum(UsageLog.output_tokens).label("output_tokens"),
            func.sum(UsageLog.cost_estimate).label("cost")
        ).where(UsageLog.created_at >= start_date)
        
        if api_key_id:
            query = query.where(UsageLog.api_key_id == api_key_id)
        
        query = query.group_by(UsageLog.model).order_by(desc(func.count(UsageLog.id)))
        
        result = await db.execute(query)
        rows = result.all()
        
        return [
            {
                "model": row.model,
                "requests": row.requests,
                "input_tokens": row.input_tokens or 0,
                "output_tokens": row.output_tokens or 0,
                "cost": round(row.cost or 0, 4)
            }
            for row in rows
        ]
    
    @staticmethod
    async def get_ip_breakdown(
        db: AsyncSession,
        api_key_id: Optional[int] = None,
        days: int = 7
    ) -> List[Dict[str, Any]]:
        """Get usage breakdown by IP address"""
        start_date = datetime.utcnow() - timedelta(days=days)
        
        query = select(
            UsageLog.ip_address,
            func.count(UsageLog.id).label("requests"),
            func.sum(UsageLog.input_tokens).label("input_tokens"),
            func.sum(UsageLog.output_tokens).label("output_tokens"),
            func.max(UsageLog.created_at).label("last_seen")
        ).where(
            and_(
                UsageLog.created_at >= start_date,
                UsageLog.ip_address.isnot(None)
            )
        )
        
        if api_key_id:
            query = query.where(UsageLog.api_key_id == api_key_id)
        
        query = query.group_by(UsageLog.ip_address).order_by(desc(func.count(UsageLog.id))).limit(50)
        
        result = await db.execute(query)
        rows = result.all()
        
        return [
            {
                "ip": row.ip_address or "unknown",
                "requests": row.requests,
                "input_tokens": row.input_tokens or 0,
                "output_tokens": row.output_tokens or 0,
                "last_seen": row.last_seen.isoformat() if row.last_seen else None
            }
            for row in rows
        ]
    
    @staticmethod
    async def get_global_stats(db: AsyncSession) -> Dict[str, Any]:
        """Get global statistics across all keys"""
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
        totals = total_result.first()
        
        # Today's stats
        today = date.today()
        today_start = datetime.combine(today, datetime.min.time())
        
        today_result = await db.execute(
            select(
                func.count(UsageLog.id).label("requests"),
                func.sum(UsageLog.input_tokens).label("input"),
                func.sum(UsageLog.output_tokens).label("output"),
                func.sum(UsageLog.cost_estimate).label("cost")
            ).where(UsageLog.created_at >= today_start)
        )
        today_stats = today_result.first()
        
        # This week's stats
        week_ago = today - timedelta(days=7)
        week_start = datetime.combine(week_ago, datetime.min.time())
        
        week_result = await db.execute(
            select(func.count(UsageLog.id)).where(UsageLog.created_at >= week_start)
        )
        week_requests = week_result.scalar() or 0
        
        # Active keys count
        keys_result = await db.execute(
            select(func.count(APIKey.id)).where(APIKey.is_active == True)
        )
        active_keys = keys_result.scalar() or 0
        
        # Unique IPs (last 30 days)
        month_ago = datetime.utcnow() - timedelta(days=30)
        ips_result = await db.execute(
            select(func.count(func.distinct(UsageLog.ip_address))).where(
                UsageLog.created_at >= month_ago
            )
        )
        unique_ips = ips_result.scalar() or 0
        
        return {
            "total_requests": totals.total_requests or 0,
            "total_input_tokens": totals.total_input or 0,
            "total_output_tokens": totals.total_output or 0,
            "total_cached_tokens": totals.total_cached or 0,
            "total_cost": round(totals.total_cost or 0, 4),
            "today_requests": today_stats.requests or 0,
            "today_input_tokens": today_stats.input or 0,
            "today_output_tokens": today_stats.output or 0,
            "today_cost": round(today_stats.cost or 0, 4),
            "week_requests": week_requests,
            "active_keys": active_keys,
            "unique_ips": unique_ips
        }
