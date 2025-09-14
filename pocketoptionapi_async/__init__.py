"""
Professional Async PocketOption API - Core module
Fully async implementation with modern Python practices
"""

from .constants import Regions
from .config import *
from .connection_keep_alive import *
from .connection_monitor import *
from .utils import *
from .websocket_client import *

# Create REGIONS instance
REGIONS = Regions()

__version__ = "2.0.0"
__author__ = "PocketOptionAPI Team"

__all__ = [
    "AsyncPocketOptionClient",
    "PocketOptionError",
    "AuthenticationError",
    "OrderError",
    "InvalidParameterError",
    "WebSocketError",
    "Balance",
    "Candle",
    "Order",
    "OrderResult",
    "OrderStatus",
    "OrderDirection",
    "Asset",
    "ConnectionStatus",
    "ASSETS",
    "REGIONS",
    "ErrorMonitor",
    "HealthChecker",
    "ErrorSeverity",
    "ErrorCategory",
    "CircuitBreaker",
    "RetryPolicy",
    "error_monitor",
    "health_checker",
]
