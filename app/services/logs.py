"""
DVProxy - Log streamer for real-time web viewing
Collects logs in memory buffer and streams them
"""
import logging
import threading
from collections import deque
from datetime import datetime
from typing import List, Optional

# In-memory log buffer
class LogBuffer:
    def __init__(self, max_lines: int = 1000):
        self.buffer: deque = deque(maxlen=max_lines)
        self.lock = threading.Lock()
        self.last_read_index = 0
    
    def add(self, record: logging.LogRecord):
        """Add a log record"""
        with self.lock:
            formatted = self._format_record(record)
            self.buffer.append(formatted)
    
    def _format_record(self, record: logging.LogRecord) -> dict:
        """Format log record for JSON"""
        return {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
    
    def get_all(self) -> List[dict]:
        """Get all logs"""
        with self.lock:
            return list(self.buffer)
    
    def get_recent(self, since_index: int = 0, limit: int = 100) -> tuple[List[dict], int]:
        """Get logs since index
        
        Returns:
            (logs, new_index) where new_index is the total count
        """
        with self.lock:
            total = len(self.buffer)
            if since_index >= total:
                return [], total
            
            logs = list(self.buffer)[since_index:since_index+limit]
            return logs, total


class LogStreamHandler(logging.Handler):
    """Handler that streams logs to buffer"""
    
    def __init__(self, log_buffer: LogBuffer):
        super().__init__()
        self.log_buffer = log_buffer
    
    def emit(self, record: logging.LogRecord):
        try:
            self.log_buffer.add(record)
        except Exception:
            self.handleError(record)


# Global buffer
_log_buffer = LogBuffer()


def get_log_buffer() -> LogBuffer:
    """Get the global log buffer"""
    return _log_buffer


def setup_log_streaming():
    """Setup log streaming to buffer"""
    handler = LogStreamHandler(_log_buffer)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    
    # Add to root logger
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.DEBUG)
