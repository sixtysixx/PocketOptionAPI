"""
Constants and configuration for the PocketOption API.
This module centralizes various configuration settings, including asset mappings,
WebSocket region URLs, timeframes, connection parameters, API limits, and default headers.
It also provides utility classes for managing regions and selecting random user agents.
"""

from typing import Dict, List, Optional
import random

# List of various user agents to rotate, improving anonymity and reducing detection risk.
# These user agents represent different browsers and operating systems.
USER_AGENTS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
]


# Asset mappings with their corresponding IDs.
# This dictionary provides a lookup for tradable assets on the PocketOption platform,
# including Forex pairs, commodities, cryptocurrencies, stock indices, and individual stocks.
ASSETS: Dict[str, int] = {
    # Major Forex Pairs
    "EURUSD": 1,
    "GBPUSD": 56,
    "USDJPY": 63,
    "USDCHF": 62,
    "USDCAD": 61,
    "AUDUSD": 40,
    "NZDUSD": 90,
    # OTC Forex Pairs
    "EURUSD_otc": 66,
    "GBPUSD_otc": 86,
    "USDJPY_otc": 93,
    "USDCHF_otc": 92,
    "USDCAD_otc": 91,
    "AUDUSD_otc": 71,
    "AUDNZD_otc": 70,
    "AUDCAD_otc": 67,
    "AUDCHF_otc": 68,
    "AUDJPY_otc": 69,
    "CADCHF_otc": 72,
    "CADJPY_otc": 73,
    "CHFJPY_otc": 74,
    "EURCHF_otc": 77,
    "EURGBP_otc": 78,
    "EURJPY_otc": 79,
    "EURNZD_otc": 80,
    "GBPAUD_otc": 81,
    "GBPJPY_otc": 84,
    "NZDJPY_otc": 89,
    "NZDUSD_otc": 90,
    # Commodities
    "XAUUSD": 2,  # Gold
    "XAUUSD_otc": 169,
    "XAGUSD": 65,  # Silver
    "XAGUSD_otc": 167,
    "UKBrent": 50,  # Oil
    "UKBrent_otc": 164,
    "USCrude": 64,
    "USCrude_otc": 165,
    "XNGUSD": 311,  # Natural Gas
    "XNGUSD_otc": 399,
    "XPTUSD": 312,  # Platinum
    "XPTUSD_otc": 400,
    "XPDUSD": 313,  # Palladium
    "XPDUSD_otc": 401,
    # Cryptocurrencies
    "BTCUSD": 197,
    "ETHUSD": 272,
    "DASH_USD": 209,
    "BTCGBP": 453,
    "BTCJPY": 454,
    "BCHEUR": 450,
    "BCHGBP": 451,
    "BCHJPY": 452,
    "DOTUSD": 458,
    "LNKUSD": 464,
    # Stock Indices
    "SP500": 321,
    "SP500_otc": 408,
    "NASUSD": 323,
    "NASUSD_otc": 410,
    "DJI30": 322,
    "DJI30_otc": 409,
    "JPN225": 317,
    "JPN225_otc": 405,
    "D30EUR": 318,
    "D30EUR_otc": 406,
    "E50EUR": 319,
    "E50EUR_otc": 407,
    "F40EUR": 316,
    "F40EUR_otc": 404,
    "E35EUR": 314,
    "E35EUR_otc": 402,
    "100GBP": 315,
    "100GBP_otc": 403,
    "AUS200": 305,
    "AUS200_otc": 306,
    "CAC40": 455,
    "AEX25": 449,
    "SMI20": 466,
    "H33HKD": 463,
    # US Stocks
    "#AAPL": 5,
    "#AAPL_otc": 170,
    "#MSFT": 24,
    "#MSFT_otc": 176,
    "#TSLA": 186,
    "#TSLA_otc": 196,
    "#FB": 177,
    "#FB_otc": 187,
    "#AMZN_otc": 412,
    "#NFLX": 182,
    "#NFLX_otc": 429,
    "#INTC": 180,
    "#INTC_otc": 190,
    "#BA": 8,
    "#BA_otc": 292,
    "#JPM": 20,
    "#JNJ": 144,
    "#JNJ_otc": 296,
    "#PFE": 147,
    "#PFE_otc": 297,
    "#XOM": 153,
    "#XOM_otc": 426,
    "#AXP": 140,
    "#AXP_otc": 291,
    "#MCD": 23,
    "#MCD_otc": 175,
    "#CSCO": 154,
    "#CSCO_otc": 427,
    "#VISA_otc": 416,
    "#CITI": 326,
    "#CITI_otc": 413,
    "#FDX_otc": 414,
    "#TWITTER": 330,
    "#TWITTER_otc": 415,
    "#BABA": 183,
    "#BABA_otc": 428,
    # Additional assets
    "EURRUB_otc": 200,
    "USDRUB_otc": 199,
    "EURHUF_otc": 460,
    "CHFNOK_otc": 457,
    # Microsoft and other tech stocks
    "Microsoft_otc": 521,
    "Facebook_OTC": 522,
    "Tesla_otc": 523,
    "Boeing_OTC": 524,
    "American_Express_otc": 525,
}


# WebSocket regions with their URLs.
# This class provides methods to access all, specific, or demo region URLs,
# with an option to randomize the order for load balancing or avoiding connection issues.
class Regions:
    """WebSocket region endpoints for PocketOption."""

    _REGIONS: Dict[str, str] = {
        "EUROPA": "wss://api-eu.po.market/socket.io/?EIO=4&transport=websocket",
        "SEYCHELLES": "wss://api-sc.po.market/socket.io/?EIO=4&transport=websocket",
        "HONGKONG": "wss://api-hk.po.market/socket.io/?EIO=4&transport=websocket",
        "SERVER1": "wss://api-spb.po.market/socket.io/?EIO=4&transport=websocket",
        "FRANCE2": "wss://api-fr2.po.market/socket.io/?EIO=4&transport=websocket",
        "UNITED_STATES4": "wss://api-us4.po.market/socket.io/?EIO=4&transport=websocket",
        "UNITED_STATES3": "wss://api-us3.po.market/socket.io/?EIO=4&transport=websocket",
        "UNITED_STATES2": "wss://api-us2.po.market/socket.io/?EIO=4&transport=websocket",
        "DEMO": "wss://demo-api-eu.po.market/socket.io/?EIO=4&transport=websocket",
        "DEMO_2": "wss://try-demo-eu.po.market/socket.io/?EIO=4&transport=websocket",
        "UNITED_STATES": "wss://api-us-north.po.market/socket.io/?EIO=4&transport=websocket",
        "RUSSIA": "wss://api-msk.po.market/socket.io/?EIO=4&transport=websocket",
        "SERVER2": "wss://api-l.po.market/socket.io/?EIO=4&transport=websocket",
        "INDIA": "wss://api-in.po.market/socket.io/?EIO=4&transport=websocket",
        "FRANCE": "wss://api-fr.po.market/socket.io/?EIO=4&transport=websocket",
        "FINLAND": "wss://api-fin.po.market/socket.io/?EIO=4&transport=websocket",
        "SERVER3": "wss://api-c.po.market/socket.io/?EIO=4&transport=websocket",
        "ASIA": "wss://api-asia.po.market/socket.io/?EIO=4&transport=websocket",
        "SERVER4": "wss://api-us-south.po.market/socket.io/?EIO=4&transport=websocket",
    }

    @classmethod
    def get_all(cls, randomize: bool = True) -> List[str]:
        """
        Retrieves all available WebSocket URLs.

        Args:
            randomize: If True, the list of URLs will be shuffled randomly.

        Returns:
            List[str]: A list of WebSocket URLs.
        """
        urls = list(cls._REGIONS.values())
        if randomize:
            random.shuffle(urls)  # Randomize order for distribution or fallback
        return urls

    @classmethod
    def get_all_regions(cls) -> Dict[str, str]:
        """
        Retrieves all region names and their corresponding URLs.

        Returns:
            Dict[str, str]: A dictionary mapping region names to their WebSocket URLs.
        """
        return cls._REGIONS.copy()

    @classmethod
    def get_region(cls, region_name: str) -> Optional[str]:
        """
        Retrieves the WebSocket URL for a specific region.

        Args:
            region_name: The name of the region (case-insensitive, e.g., "EUROPA", "DEMO").

        Returns:
            Optional[str]: The WebSocket URL for the specified region, or None if not found.
        """
        return cls._REGIONS.get(region_name.upper())

    @classmethod
    def get_demo_regions(cls) -> List[str]:
        """
        Retrieves all WebSocket URLs specifically designated for demo accounts.

        Returns:
            List[str]: A list of demo account WebSocket URLs.
        """
        return [url for name, url in cls._REGIONS.items() if "DEMO" in name.upper()]


# Global instance of Regions for easy access throughout the application.
REGIONS = Regions()

# Timeframes for candlestick data in seconds.
# This dictionary maps common timeframe abbreviations (e.g., "1m" for 1 minute)
# to their equivalent duration in seconds, as required by the API.
TIMEFRAMES: Dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
}

# Connection settings for the WebSocket client.
# These parameters control the behavior of the WebSocket connection,
# including ping intervals, timeouts, and reconnection strategies.
CONNECTION_SETTINGS: Dict[str, int] = {
    "ping_interval": 20,  # seconds: how often to send a ping frame to keep connection alive
    "ping_timeout": 10,  # seconds: how long to wait for a pong response before considering connection dead
    "close_timeout": 10,  # seconds: timeout for the WebSocket close handshake
    "max_reconnect_attempts": 5,  # Maximum number of times to attempt reconnection on failure
    "reconnect_delay": 5,  # seconds: initial delay before attempting a reconnect
    "message_timeout": 30,  # seconds: timeout for receiving a single message
}

# API Limits for trading operations.
# These define constraints on order amounts, durations, and API request rates
# to ensure compliance with platform rules and prevent abuse.
API_LIMITS: Dict[str, float] = {
    "min_order_amount": 1.0,  # Minimum amount for a single trade
    "max_order_amount": 50000.0,  # Maximum amount for a single trade
    "min_duration": 5,  # seconds: Minimum duration for an option trade
    "max_duration": 43200,  # 12 hours in seconds: Maximum duration for an option trade
    "max_concurrent_orders": 10,  # Maximum number of open orders at any given time
    "rate_limit": 100,  # requests per minute: General API request rate limit
}

# Default headers to be sent with WebSocket connection requests.
# The User-Agent is dynamically selected from a list to mimic different browsers,
# which can help in avoiding detection or specific server-side filtering.
DEFAULT_HEADERS: Dict[str, str] = {
    "Origin": "https://pocketoption.com",
    "User-Agent": random.choice(USER_AGENTS),  # Selects a random User-Agent string
}
