"""
Utility functions for the PocketOption API
"""

import asyncio
import time
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import pandas as pd
from loguru import logger

from .models import Candle, OrderResult


def format_session_id(
    session_id: str,
    is_demo: bool = True,
    uid: int = 0,
    platform: int = 1,
    is_fast_history: bool = True,
) -> Dict[str, Any]:
    """
    Format session ID for authentication into a dictionary suitable for
    `python-socketio`'s `auth` parameter.

    Args:
        session_id: The raw session ID or a complete PHP-serialized string.
        is_demo: A boolean indicating whether the session is for a demo account (True) or a real account (False).
                 This is converted to 1 for demo and 0 for live as required by the API.
        uid: The user ID associated with the session.
        platform: An integer representing the platform from which the connection is made (e.g., 1 for web, 3 for mobile).
        is_fast_history: A boolean flag to enable or disable fast history loading.

    Returns:
        Dict[str, Any]: A dictionary containing authentication data.
    """
    # Construct the dictionary containing authentication data.
    auth_data = {
        "session": session_id,
        "isDemo": 1 if is_demo else 0,  # Convert boolean to integer (1 or 0)
        "uid": uid,
        "platform": platform,
    }

    # Add isFastHistory only if it's true, as it might not always be required.
    if is_fast_history:
        auth_data["isFastHistory"] = True

    # This dictionary will be passed directly to the `auth` parameter of
    # `socketio.AsyncClient.connect()`, and `python-socketio` will handle
    # the correct serialization and framing (e.g., into '42["auth", {...}]').
    return auth_data


def calculate_payout_percentage(
    entry_price: float, exit_price: float, direction: str, payout_rate: float = 0.8
) -> float:
    """
    Calculate payout percentage for an order based on entry/exit prices and direction.

    Args:
        entry_price: The price at which the order was opened.
        exit_price: The price at which the order was closed.
        direction: The direction of the trade ('call' for up, 'put' for down).
        payout_rate: The percentage of the investment returned on a winning trade (e.g., 0.8 for 80%).

    Returns:
        float: The payout percentage. Returns `payout_rate` for a win, and -1.0 for a loss.
    """
    # Determine if the trade was a win based on direction and price movement.
    if direction.lower() == "call":
        win = exit_price > entry_price
    else:  # 'put' direction
        win = exit_price < entry_price

    # Return the payout rate if it's a win, otherwise return -1.0 to indicate a loss.
    return payout_rate if win else -1.0


def analyze_candles(candles: List[Candle]) -> Dict[str, Any]:
    """
    Analyze candle data to provide basic statistical insights.

    Args:
        candles: A list of Candle objects, representing historical price data.

    Returns:
        Dict[str, Any]: A dictionary containing various analysis results, such as
                        count, first/last prices, price change, highest/lowest points,
                        average close, volatility, and trend. Returns an empty dictionary
                        if no candles are provided.
    """
    if not candles:
        # Return an empty dictionary if there's no data to analyze.
        return {}

    # Extract closing, high, and low prices for calculations.
    prices = [candle.close for candle in candles]
    highs = [candle.high for candle in candles]
    lows = [candle.low for candle in candles]

    # Calculate various metrics.
    return {
        "count": len(candles),
        "first_price": prices[0],
        "last_price": prices[-1],
        "price_change": prices[-1] - prices[0],
        # Calculate percentage change, handling division by zero for initial price.
        "price_change_percent": ((prices[-1] - prices[0]) / prices[0]) * 100
        if prices[0] != 0
        else 0,
        "highest": max(highs),
        "lowest": min(lows),
        "average_close": sum(prices) / len(prices),
        "volatility": calculate_volatility(prices),
        "trend": determine_trend(prices),
    }


def calculate_volatility(prices: List[float], periods: int = 14) -> float:
    """
    Calculate price volatility using the standard deviation of recent prices.

    Args:
        prices: A list of closing prices.
        periods: The number of recent periods (candles) to consider for volatility calculation.

    Returns:
        float: The calculated volatility value. Returns 0.0 if there are insufficient prices.
    """
    # Adjust periods if the number of available prices is less than the requested periods.
    if len(prices) < periods:
        periods = len(prices)

    # If there are not enough periods to calculate, return 0 to avoid errors.
    if periods < 1:
        return 0.0

    # Get the most recent prices for calculation.
    recent_prices = prices[-periods:]
    mean = sum(recent_prices) / len(recent_prices)

    # Calculate variance and then standard deviation (volatility).
    variance = sum((price - mean) ** 2 for price in recent_prices) / len(recent_prices)
    return variance**0.5


def determine_trend(prices: List[float], periods: int = 10) -> str:
    """
    Determine the price trend direction (bullish, bearish, or sideways) based on recent price averages.

    Args:
        prices: A list of closing prices.
        periods: The number of periods (candles) to analyze for trend determination.

    Returns:
        str: The trend direction: 'bullish' (upward), 'bearish' (downward), or 'sideways' (flat).
    """
    # Adjust periods if the number of available prices is less than the requested periods.
    if len(prices) < periods:
        periods = len(prices)

    # A minimum of 2 periods is needed to compare averages.
    if periods < 2:
        return "sideways"

    # Split recent prices into two halves to compare their averages.
    recent_prices = prices[-periods:]
    first_half = recent_prices[: periods // 2]
    second_half = recent_prices[periods // 2 :]

    # Handle cases where one of the halves might be empty (e.g., if periods is 1).
    if not first_half or not second_half:
        return "sideways"

    first_avg = sum(first_half) / len(first_half)
    second_avg = sum(second_half) / len(second_half)

    # Calculate the percentage change between the two averages.
    # Handle division by zero if first_avg is 0.
    change_percent = (
        ((second_avg - first_avg) / first_avg) * 100 if first_avg != 0 else 0
    )

    # Determine trend based on a small threshold for change.
    if change_percent > 0.1:
        return "bullish"
    elif change_percent < -0.1:
        return "bearish"
    else:
        return "sideways"


def calculate_support_resistance(
    candles: List[Candle], periods: int = 20
) -> Dict[str, float]:
    """
    Calculate simple support and resistance levels from historical candle data.
    Support is typically the lowest low, and resistance is the highest high within the period.

    Args:
        candles: A list of Candle objects.
        periods: The number of recent candles to consider for the calculation.

    Returns:
        Dict[str, float]: A dictionary containing 'support', 'resistance', and 'range' (resistance - support).
                          Returns default values if no candles are provided.
    """
    if not candles:
        # Return default values if no candles are provided.
        return {"support": 0.0, "resistance": 0.0, "range": 0.0}

    # Adjust periods if the number of available candles is less than the requested periods.
    if len(candles) < periods:
        periods = len(candles)

    # Get the most recent candles for analysis.
    recent_candles = candles[-periods:]
    highs = [candle.high for candle in recent_candles]
    lows = [candle.low for candle in recent_candles]

    # Calculate resistance as the maximum high and support as the minimum low.
    resistance = max(highs)
    support = min(lows)

    return {"support": support, "resistance": resistance, "range": resistance - support}


def format_timeframe(seconds: int) -> str:
    """
    Format a timeframe given in seconds into a human-readable string (e.g., '1m', '5m', '1h').

    Args:
        seconds: The duration in seconds.

    Returns:
        str: A formatted string representing the timeframe.
    """
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        # Convert to minutes if less than an hour.
        return f"{seconds // 60}m"
    elif seconds < 86400:
        # Convert to hours if less than a day.
        return f"{seconds // 3600}h"
    else:
        # Convert to days for longer timeframes.
        return f"{seconds // 86400}d"


def validate_asset_symbol(symbol: str, available_assets: Dict[str, int]) -> bool:
    """
    Validate if an asset symbol is present in the list of available assets.

    Args:
        symbol: The asset symbol string to validate (e.g., "EURUSD_otc").
        available_assets: A dictionary where keys are asset symbols and values are their IDs.

    Returns:
        bool: True if the symbol exists in the available assets, False otherwise.
    """
    return symbol in available_assets


def calculate_order_expiration(
    duration_seconds: int, current_time: Optional[datetime] = None
) -> datetime:
    """
    Calculate the exact expiration datetime for an order given its duration.

    Args:
        duration_seconds: The duration of the order in seconds.
        current_time: The starting datetime from which to calculate expiration.
                      If None, the current UTC time is used.

    Returns:
        datetime: The calculated expiration datetime.
    """
    if current_time is None:
        current_time = datetime.now()  # Use current time if not provided

    return current_time + timedelta(seconds=duration_seconds)


def retry_async(max_attempts: int = 3, delay: float = 1.0, backoff_factor: float = 2.0):
    """
    A decorator for retrying asynchronous functions in case of exceptions.
    It implements an exponential backoff strategy for delays between retries.

    Args:
        max_attempts: The maximum number of times to attempt executing the function.
        delay: The initial delay in seconds before the first retry.
        backoff_factor: The multiplier for the delay between subsequent retries.
    """

    def decorator(func):
        async def wrapper(*args, **kwargs):
            current_delay = delay

            for attempt in range(max_attempts):
                try:
                    # Attempt to execute the decorated async function.
                    return await func(*args, **kwargs)
                except Exception as e:
                    # If it's the last attempt, re-raise the exception after logging.
                    if attempt == max_attempts - 1:
                        logger.error(
                            f"Function {func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise

                    # Log the failure and wait before retrying.
                    logger.warning(
                        f"Attempt {attempt + 1} failed for {func.__name__}: {e}"
                    )
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff_factor  # Increase delay for next attempt

        return wrapper

    return decorator


def performance_monitor(func):
    """
    A decorator to monitor the execution time of asynchronous functions.
    It logs the duration of function execution or the time taken before an error occurs.
    """

    async def wrapper(*args, **kwargs):
        start_time = time.time()  # Record start time
        try:
            result = await func(*args, **kwargs)  # Execute the function
            execution_time = time.time() - start_time  # Calculate execution time
            logger.debug(
                f"{func.__name__} executed in {execution_time:.3f}s"
            )  # Log successful execution
            return result
        except Exception as e:
            execution_time = time.time() - start_time  # Calculate time until error
            logger.error(
                f"{func.__name__} failed after {execution_time:.3f}s: {e}"
            )  # Log error with duration
            raise  # Re-raise the exception

    return wrapper


class RateLimiter:
    """
    A simple token bucket-like rate limiter for controlling the frequency of API calls.
    It allows a maximum number of calls within a specified time window.
    """

    def __init__(self, max_calls: int = 100, time_window: int = 60):
        """
        Initialize the RateLimiter.

        Args:
            max_calls: The maximum number of calls allowed within the `time_window`.
            time_window: The duration in seconds over which `max_calls` are allowed.
        """
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = []  # Stores the timestamps of previous calls

    async def acquire(self) -> bool:
        """
        Attempt to acquire permission to make a call. If the rate limit is exceeded,
        it will wait until a call can be made.

        Returns:
            bool: Always True, as it waits until permission is granted.
        """
        now = time.time()

        # Remove any call timestamps that are outside the current time window.
        self.calls = [
            call_time for call_time in self.calls if now - call_time < self.time_window
        ]

        # Check if adding a new call would exceed the maximum allowed calls.
        if len(self.calls) < self.max_calls:
            self.calls.append(now)  # Add the current call's timestamp
            return True

        # If the limit is exceeded, calculate how long to wait until the oldest call
        # falls out of the time window, freeing up a slot.
        wait_time = self.time_window - (now - self.calls[0])
        if wait_time > 0:
            logger.warning(
                f"Rate limit exceeded, waiting {wait_time:.1f}s"
            )  # Log the wait time
            await asyncio.sleep(wait_time)  # Wait for the required duration
            # Recursively call acquire to re-check the limit after waiting.
            return await self.acquire()

        # This line should theoretically not be reached if wait_time correctly handles all cases,
        # but included for completeness.
        return True


class OrderManager:
    """
    A class to manage the lifecycle of multiple orders, tracking their active
    and completed states, and allowing for callbacks on order completion.
    """

    def __init__(self):
        """
        Initializes the OrderManager with empty dictionaries for active and completed orders,
        and for order-specific callbacks.
        """
        self.active_orders: Dict[str, OrderResult] = {}  # Orders currently open
        self.completed_orders: Dict[str, OrderResult] = {}  # Orders that have closed
        self.order_callbacks: Dict[str, List] = {}  # Callbacks for specific orders

    def add_order(self, order: OrderResult) -> None:
        """
        Adds a new order to the active orders tracking.

        Args:
            order: The OrderResult object representing the newly placed order.
        """
        self.active_orders[order.order_id] = order

    def complete_order(self, order_id: str, result: OrderResult) -> None:
        """
        Marks an order as completed, moving it from active to completed orders
        and executing any registered callbacks for that order.

        Args:
            order_id: The unique identifier of the order to complete.
            result: The final OrderResult object with the outcome of the order.
        """
        # Remove the order from active tracking if it exists there.
        if order_id in self.active_orders:
            del self.active_orders[order_id]

        # Add the order to the completed orders.
        self.completed_orders[order_id] = result

        # Execute any callbacks registered for this specific order.
        if order_id in self.order_callbacks:
            for callback in self.order_callbacks[order_id]:
                try:
                    callback(result)
                except Exception as e:
                    logger.error(f"Error in order callback for {order_id}: {e}")
            # Remove callbacks once executed to prevent re-execution.
            del self.order_callbacks[order_id]

    def add_order_callback(self, order_id: str, callback) -> None:
        """
        Registers a callback function to be executed when a specific order is completed.

        Args:
            order_id: The unique identifier of the order for which to register the callback.
            callback: The function to call when the order completes. It should accept one argument (OrderResult).
        """
        # Initialize a list for callbacks if none exist for this order ID.
        if order_id not in self.order_callbacks:
            self.order_callbacks[order_id] = []
        self.order_callbacks[order_id].append(callback)

    def get_order_status(self, order_id: str) -> Optional[OrderResult]:
        """
        Retrieves the current status of an order, whether it's active or completed.

        Args:
            order_id: The unique identifier of the order to check.

        Returns:
            Optional[OrderResult]: The OrderResult object if found, otherwise None.
        """
        if order_id in self.active_orders:
            return self.active_orders[order_id]
        elif order_id in self.completed_orders:
            return self.completed_orders[order_id]
        return None

    def get_active_count(self) -> int:
        """
        Returns the number of orders currently active.

        Returns:
            int: The count of active orders.
        """
        return len(self.active_orders)

    def get_completed_count(self) -> int:
        """
        Returns the number of orders that have been completed.

        Returns:
            int: The count of completed orders.
        """
        return len(self.completed_orders)


def candles_to_dataframe(candles: List[Candle]) -> pd.DataFrame:
    """
    Converts a list of Candle objects into a pandas DataFrame, suitable for data analysis.
    The DataFrame will have a 'timestamp' index and columns for 'open', 'high', 'low', 'close', 'volume', and 'asset'.

    Args:
        candles: A list of Candle objects to convert.

    Returns:
        pd.DataFrame: A pandas DataFrame containing the candle data,
                      indexed by timestamp and sorted chronologically.
                      Returns an empty DataFrame if the input list is empty.
    """
    data = []
    # Iterate through each candle and extract relevant attributes.
    for candle in candles:
        data.append(
            {
                "timestamp": candle.timestamp,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "asset": candle.asset,
            }
        )

    df = pd.DataFrame(data)
    if not df.empty:
        # Set 'timestamp' as the DataFrame index for time-series operations.
        df.set_index("timestamp", inplace=True)
        # Sort the DataFrame by index (timestamp) to ensure chronological order.
        df.sort_index(inplace=True)

    return df
