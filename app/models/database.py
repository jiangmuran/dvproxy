"""
DVProxy - Database Models
SQLAlchemy models for API keys, usage logging, and statistics
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, Index, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class APIKey(Base):
    """API Key model for client authentication"""
    __tablename__ = "api_keys"
    
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(64), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    
    # Quota management (None = unlimited)
    quota_limit = Column(Integer, nullable=True)  # Max requests allowed (None = unlimited)
    quota_used = Column(Integer, default=0)  # Requests used
    
    # Rate limiting
    rate_limit = Column(Integer, default=60)  # Requests per minute
    
    # Status
    is_active = Column(Boolean, default=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    
    # Relations
    usage_logs = relationship("UsageLog", back_populates="api_key", cascade="all, delete-orphan")
    daily_stats = relationship("DailyStats", back_populates="api_key", cascade="all, delete-orphan")


class UsageLog(Base):
    """Usage log for tracking API calls"""
    __tablename__ = "usage_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    api_key_id = Column(Integer, ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False)
    
    # Request info
    endpoint = Column(String(50), nullable=False)  # anthropic/openai/responses
    model = Column(String(100), nullable=False)
    
    # Token usage
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    cached_tokens = Column(Integer, default=0)
    
    # Cost (estimated)
    cost_estimate = Column(Float, default=0.0)
    
    # Request metadata
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(500), nullable=True)
    
    # Status
    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)
    
    # Timing
    latency_ms = Column(Integer, default=0)
    
    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    # Relations
    api_key = relationship("APIKey", back_populates="usage_logs")
    
    # Indexes for analytics
    __table_args__ = (
        Index('idx_usage_created_at', 'created_at'),
        Index('idx_usage_api_key_created', 'api_key_id', 'created_at'),
        Index('idx_usage_model', 'model'),
        Index('idx_usage_ip', 'ip_address'),
        Index('idx_usage_endpoint', 'endpoint'),
    )


class DailyStats(Base):
    """Aggregated daily statistics"""
    __tablename__ = "daily_stats"
    
    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)  # Changed to Date type
    api_key_id = Column(Integer, ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=True)  # NULL for global stats
    
    # Aggregated metrics
    request_count = Column(Integer, default=0)  # Renamed for clarity
    success_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    cached_tokens = Column(Integer, default=0)
    
    cost_estimate = Column(Float, default=0.0)
    
    avg_latency_ms = Column(Float, default=0.0)
    
    # Relations
    api_key = relationship("APIKey", back_populates="daily_stats")
    
    __table_args__ = (
        Index('idx_daily_date_key', 'date', 'api_key_id'),
    )
