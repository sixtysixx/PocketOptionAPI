"""
Professional Async PocketOption API - Core module
Fully async implementation with modern Python practices
"""

from .client import AsyncPocketOptionClient
from .exceptions import (
    PocketOptionError,
    AuthenticationError,
    OrderError,
    InvalidParameterError,
    WebSocketError,
)
from .models import (
    Balance,
    Candle,
    Order,
    OrderResult,
    OrderStatus,
    OrderDirection,
    Asset,
    ConnectionStatus,
)
from .constants import ASSETS, Regions
from .config import *
from .connection_keep_alive import *
from .connection_monitor import *
from .utils import *
from .websocket_client import *

# Import monitoring components
from .monitoring import (
    ErrorMonitor,
    HealthChecker,
    ErrorSeverity,
    ErrorCategory,
    CircuitBreaker,
    RetryPolicy,
    error_monitor,
    health_checker,
)

# Create REGIONS instance
REGIONS = Regions()

__version__ = "2.0.0"
__author__ = "PocketOptionAPI Team"

# __all__ intentionally omitted to export all imported names
