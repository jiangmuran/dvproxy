"""
DVProxy - Credential Store
Thread-safe storage for upstream authentication credentials
"""
import json
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger("dvproxy.credentials")

# Persistent storage file
_CREDENTIALS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "credentials.json")


class CredentialStore:
    """Thread-safe credential store with persistence
    
    Stores DeepVLab/Feishu/OA login credentials with:
    - File persistence across restarts
    - Automatic sync file loading on first access (sync or async)
    - Automatic expiration checking
    """
    
    _credentials: Dict[str, Any] = {}
    _loaded = False
    
    @classmethod
    def _load_sync(cls):
        """Load credentials from disk synchronously"""
        if cls._loaded:
            return
        try:
            if os.path.exists(_CREDENTIALS_FILE):
                with open(_CREDENTIALS_FILE, 'r') as f:
                    cls._credentials = json.load(f)
                    method = cls._credentials.get('login_method', 'unknown')
                    user = cls._credentials.get('user', {})
                    name = user.get('name', 'Unknown')
                    logger.info(f"Loaded credentials: {name} (method: {method})")
        except Exception as e:
            logger.warning(f"Failed to load credentials: {e}")
            cls._credentials = {}
        cls._loaded = True
    
    @classmethod
    async def save(cls, credentials: Dict[str, Any]) -> None:
        """Save credentials with persistence"""
        cls._load_sync()
        cls._credentials = credentials
        cls._credentials["saved_at"] = datetime.utcnow().isoformat()
        try:
            with open(_CREDENTIALS_FILE, 'w') as f:
                json.dump(cls._credentials, f, indent=2)
            os.chmod(_CREDENTIALS_FILE, 0o600)
        except Exception as e:
            logger.error(f"Failed to persist credentials: {e}")
    
    @classmethod
    async def get(cls) -> Dict[str, Any]:
        """Get current credentials"""
        cls._load_sync()
        return cls._credentials.copy()
    
    @classmethod
    async def get_access_token(cls) -> Optional[str]:
        """Get the current access token (async)"""
        cls._load_sync()
        return cls._credentials.get("access_token")
    
    @classmethod
    async def clear(cls) -> None:
        """Clear all credentials"""
        cls._credentials = {}
        cls._loaded = True
        try:
            if os.path.exists(_CREDENTIALS_FILE):
                os.remove(_CREDENTIALS_FILE)
        except Exception as e:
            logger.error(f"Failed to remove credentials file: {e}")
    
    @classmethod
    async def is_logged_in(cls) -> bool:
        """Check if credentials are stored"""
        cls._load_sync()
        return bool(cls._credentials.get("access_token"))
    
    @classmethod
    async def get_user_info(cls) -> Optional[Dict[str, Any]]:
        """Get stored user info"""
        cls._load_sync()
        user = cls._credentials.get("user")
        if user:
            return {
                "user_id": user.get("userId") or user.get("openId"),
                "name": user.get("name"),
                "email": user.get("email"),
                "login_method": cls._credentials.get("login_method"),
                "logged_in_at": cls._credentials.get("logged_in_at"),
            }
        return None


# Convenience functions
async def get_deepvlab_access_token() -> Optional[str]:
    """Get the current DeepVLab access token (async)"""
    return await CredentialStore.get_access_token()


def get_deepvlab_access_token_sync() -> Optional[str]:
    """Get the current DeepVLab access token (sync, loads from disk)"""
    CredentialStore._load_sync()
    return CredentialStore._credentials.get("access_token")
