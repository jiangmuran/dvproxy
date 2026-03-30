"""
DVProxy - Database Connection

Configured for concurrent SQLite access with:
- WAL journal mode for concurrent reads
- Connection pooling
- Proper timeout settings
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import event, text
from app.config import settings
from app.models.database import Base

# SQLite optimization for concurrent access
connect_args = {}
if "sqlite" in settings.database_url:
    connect_args = {
        "check_same_thread": False,
        "timeout": 30,  # 30s lock timeout
    }

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    connect_args=connect_args,
    pool_pre_ping=True,  # Verify connections before use
)

# Configure SQLite for better concurrency
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Set SQLite pragmas for performance and concurrency"""
    if "sqlite" in settings.database_url:
        cursor = dbapi_connection.cursor()
        # WAL mode allows concurrent reads while writing
        cursor.execute("PRAGMA journal_mode=WAL")
        # Normal sync mode for better performance
        cursor.execute("PRAGMA synchronous=NORMAL")
        # 64MB cache
        cursor.execute("PRAGMA cache_size=-64000")
        # Foreign key enforcement
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db():
    """Initialize database tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """Dependency for getting database session"""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
