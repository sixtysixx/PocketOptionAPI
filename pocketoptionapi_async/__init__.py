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