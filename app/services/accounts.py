"""
DVProxy - Multi-account Management
Manage multiple upstream API accounts and switch between them
"""
import json
import os
import logging
from typing import Dict, Optional, List
from pathlib import Path

logger = logging.getLogger("dvproxy.accounts")


class AccountManager:
    """Manage multiple upstream API accounts"""
    
    def __init__(self, data_dir: str = "."):
        self.accounts_file = os.path.join(data_dir, ".accounts.json")
        self.current_file = os.path.join(data_dir, ".current_account")
        self.accounts: Dict[str, Dict] = {}
        self.current_account: Optional[str] = None
        self._load()
    
    def _load(self):
        """Load accounts from persistent storage"""
        if os.path.exists(self.accounts_file):
            try:
                with open(self.accounts_file, 'r') as f:
                    data = json.load(f)
                    self.accounts = data.get("accounts", {})
                logger.info(f"Loaded {len(self.accounts)} accounts from {self.accounts_file}")
            except Exception as e:
                logger.error(f"Failed to load accounts: {e}")
        
        if os.path.exists(self.current_file):
            try:
                with open(self.current_file, 'r') as f:
                    self.current_account = f.read().strip()
                    if self.current_account not in self.accounts:
                        self.current_account = None
            except Exception as e:
                logger.error(f"Failed to load current account: {e}")
    
    def _save(self):
        """Persist accounts to storage"""
        try:
            with open(self.accounts_file, 'w') as f:
                json.dump({"accounts": self.accounts}, f, indent=2)
            os.chmod(self.accounts_file, 0o600)
        except Exception as e:
            logger.error(f"Failed to save accounts: {e}")
    
    def _save_current(self):
        """Persist current account selection"""
        try:
            if self.current_account:
                with open(self.current_file, 'w') as f:
                    f.write(self.current_account)
                os.chmod(self.current_file, 0o600)
        except Exception as e:
            logger.error(f"Failed to save current account: {e}")
    
    def add_account(self, name: str, token: str) -> bool:
        """Add or update an account"""
        if not name or not token:
            return False
        self.accounts[name] = {
            "token": token,
            "created": True
        }
        self._save()
        logger.info(f"Added/updated account: {name}")
        return True
    
    def list_accounts(self) -> List[Dict]:
        """List all accounts (without tokens)"""
        return [
            {
                "name": name,
                "current": name == self.current_account
            }
            for name in self.accounts.keys()
        ]
    
    def get_current_token(self) -> Optional[str]:
        """Get token of current active account"""
        if not self.current_account or self.current_account not in self.accounts:
            return None
        return self.accounts[self.current_account].get("token")
    
    def switch_account(self, name: str) -> bool:
        """Switch to a different account"""
        if name not in self.accounts:
            return False
        self.current_account = name
        self._save_current()
        logger.info(f"Switched to account: {name}")
        return True
    
    def delete_account(self, name: str) -> bool:
        """Delete an account"""
        if name not in self.accounts:
            return False
        del self.accounts[name]
        if self.current_account == name:
            self.current_account = None
            self._save_current()
        self._save()
        logger.info(f"Deleted account: {name}")
        return True


# Global instance
_account_manager: Optional[AccountManager] = None


def get_account_manager() -> AccountManager:
    """Get or create the account manager"""
    global _account_manager
    if _account_manager is None:
        data_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        _account_manager = AccountManager(data_dir)
    return _account_manager
