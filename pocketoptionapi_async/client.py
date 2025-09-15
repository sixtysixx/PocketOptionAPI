import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union, Callable
from loguru import logger
import pandas as pd

from .websocket_client import AsyncWebSocketClient
from .models import (
    Balance,
    Candle,
    Order,
    OrderDirection,
    OrderResult,
    OrderStatus,
    ConnectionStatus,
    ConnectionInfo,
    ServerTime,
)
from .constants import REGIONS, TIMEFRAMES, API_LIMITS
from .exceptions import (
    AuthenticationError,
    ConnectionError,
    OrderError,
    TimeoutError,
    InvalidParameterError,
)
from .utils import (
    format_session_id,
    calculate_payout_percentage,
    retry_async,
    performance_monitor,
    RateLimiter,
    OrderManager,
)
from .monitoring import error_monitor, ErrorCategory, ErrorSeverity


class AsyncPocketOptionClient:
    """
    Professional async client for the PocketOption trading platform.
    This client provides a high-level interface for interacting with the PocketOption API,
    including connection management, authentication, trading operations, and data retrieval.
    It uses an underlying `AsyncWebSocketClient` for WebSocket communication.
    """

    def __init__(
        self,
        ssid: str,
        is_demo: bool = True,
        enable_logging: bool = True,
        auto_reconnect: bool = True,
        persistent_connection: bool = True,
    ):
        """
        Initializes the AsyncPocketOptionClient.

        Args:
            ssid: The complete SSID string for authentication (expected to be in '42["auth",...]' format).
            is_demo: A boolean indicating whether to connect to demo or live servers.
            enable_logging: Whether to enable detailed logging.
            auto_reconnect: Whether to automatically reconnect on disconnection.
            persistent_connection: Whether to maintain a persistent connection.
        """
        # Store raw SSID for sending as message
        self.raw_ssid = ssid
        self.is_demo = is_demo
        self.enable_logging = enable_logging
        self.auto_reconnect = auto_reconnect
        self.persistent_connection = persistent_connection

        # Parse SSID to extract authentication data
        self._auth_data = self._parse_ssid_into_auth_data(ssid)

        # Initialize underlying WebSocket client
        self._websocket = AsyncWebSocketClient()

        # Connection state
        self.server_time: Optional[ServerTime] = None

        # Data storage
        self._balance: Optional[Balance] = None
        self._candles: Dict[str, List[Candle]] = {}
        self._active_orders: Dict[str, OrderResult] = {}
        self._order_results: Dict[str, OrderResult] = {}
        self._balance_updated_event = asyncio.Event()
        self._candle_requests: Dict[str, asyncio.Future] = {}
        self._candles_cache: Dict[str, List[Candle]] = {}

        # Rate limiting
        self._rate_limiter = RateLimiter(
            max_calls=int(API_LIMITS["rate_limit"]),
            time_window=60,  # 1 minute
        )

        # Order management
        self._order_manager = OrderManager()

        # Event callbacks
        self._event_callbacks: Dict[str, List[Callable]] = {}

        # Setup WebSocket event handlers
        self._setup_websocket_event_handlers()

        logger.info("AsyncPocketOptionClient initialized.")

    def _parse_ssid_into_auth_data(self, ssid_input: str) -> Dict[str, Any]:
        """
        Parses the complete SSID string to extract authentication data for the connection handshake.
        The SSID is expected to be in the format: '42["auth",{"session":"...","isDemo":1,...}]'

        Args:
            ssid_input: The complete SSID string.

        Returns:
            Dict[str, Any]: A dictionary containing the parsed authentication data.
        """
        logger.info(
            f"Processing SSID input (length: {len(ssid_input)}, starts with: {ssid_input[:30]}...)"
        )

        # Handle already parsed auth data (for backward compatibility or direct dict input)
        if isinstance(ssid_input, dict):
            logger.debug("SSID input is already a dictionary.")
            return ssid_input

        # Handle complete SSID string format
        if ssid_input.startswith('42["auth",') and ssid_input.endswith("]"):
            try:
                # Extract the JSON part after '42["auth",' and before the final ']'
                json_part = ssid_input[
                    10:-1
                ]  # Remove '42["auth",' prefix and ']' suffix
                auth_dict = json.loads(json_part)

                logger.info(f"Parsed auth data keys: {list(auth_dict.keys())}")
                logger.debug(f"Full auth data: {auth_dict}")

                # Validate required keys
                required_keys = ["session", "isDemo", "uid", "platform"]
                for key in required_keys:
                    if key not in auth_dict:
                        logger.warning(f"Missing required auth key: {key}")

                # Extract session for logging
                if "session" in auth_dict:
                    session_preview = auth_dict["session"][:25] + "..."
                    logger.info(f"Found session in auth data: {session_preview}")

                return auth_dict

            except json.JSONDecodeError as e:
                logger.error(f"Failed to decode JSON from SSID: {e}")
                raise AuthenticationError(
                    f"Invalid SSID format: Unable to parse JSON. Error: {e}"
                )
            except Exception as e:
                logger.error(f"Unexpected error parsing SSID: {e}")
                raise AuthenticationError(f"Failed to parse SSID: {e}")

        # Handle raw session ID format (backward compatibility)
        elif re.match(r"^[a-zA-Z0-9\-_]+$", ssid_input):
            logger.info("SSID appears to be a raw session ID.")
            return format_session_id(
                ssid_input,
                is_demo=self.is_demo,
                uid=0,  # Default values for raw session ID
                platform=1,
            )

        # Handle PHP serialized format (if needed in the future)
        elif ssid_input.startswith("a:"):
            logger.info("SSID appears to be in PHP serialized format.")
            # This would require a PHP unserialize implementation
            # For now, we'll treat it as an error
            raise AuthenticationError(
                "PHP serialized SSID format not supported. Please use '42[\"auth\",{...}]' format."
            )

        else:
            logger.error(f"Unrecognized SSID format: {ssid_input[:50]}...")
            raise AuthenticationError(
                "Unrecognized SSID format. Expected '42[\"auth\",{...}]' or raw session ID."
            )

    def _setup_websocket_event_handlers(self):
        """Setup event handlers for WebSocket client events."""
        self._websocket.add_event_handler("connected", self._on_websocket_connected)
        self._websocket.add_event_handler(
            "disconnected", self._on_websocket_disconnected
        )
        self._websocket.add_event_handler("reconnected", self._on_websocket_reconnected)
        self._websocket.add_event_handler("authenticated", self._on_authenticated)
        self._websocket.add_event_handler("successauth", self._on_successauth)
        self._websocket.add_event_handler("balance_updated", self._on_balance_updated)
        self._websocket.add_event_handler("candles_received", self._on_candles_received)
        self._websocket.add_event_handler("order_opened", self._on_order_opened)
        self._websocket.add_event_handler("order_closed", self._on_order_closed)
        self._websocket.add_event_handler("stream_update", self._on_stream_update)
        self._websocket.add_event_handler("message_received", self._on_message_received)
        self._websocket.add_event_handler("json_data", self._on_json_data)
        self._websocket.add_event_handler("auth_error", self._on_auth_error)
        self._websocket.add_event_handler("connection_error", self._on_connection_error)
        self._websocket.add_event_handler("updateAssets", self._on_update_assets)

    async def connect(
        self, regions: Optional[List[str]] = None, timeout: float = 20.0
    ) -> bool:
        """
        Establishes a connection to the PocketOption API with authentication.

        Args:
            regions: A list of region names to try connecting to. If None, uses default regions.
            timeout: Timeout for the connection process.

        Returns:
            bool: True if connected and authenticated successfully, False otherwise.
        """
        logger.info("Connecting to PocketOption (Socket.IO client)...")
        start_time = time.time()

        try:
            # Determine which regions to try
            if regions is None:
                if self.is_demo:
                    regions = ["DEMO", "DEMO_2"]
                    logger.info(f"Demo mode: Using demo regions: {regions}")
                else:
                    regions = list(REGIONS.get_all_regions().keys())
                    logger.info(f"Live mode: Using all available regions: {regions}")

            # Get WebSocket URLs for the specified regions
            urls_to_try = []
            for region in regions:
                url = REGIONS.get_region(region)
                if url:
                    urls_to_try.append(url)
                else:
                    logger.warning(f"Unknown region: {region}")

            if not urls_to_try:
                logger.error("No valid WebSocket URLs found for the specified regions.")
                return False

            # Attempt to connect using the WebSocket client
            # Pass both auth_data for handshake and raw_ssid for sending as message
            success = await self._websocket.connect(
                urls_to_try, self._auth_data, self.raw_ssid
            )

            if success:
                self.is_connected = True
                self.connection_info = self._websocket.connection_info
                logger.info(
                    "Successfully initiated Socket.IO connection. Waiting for authentication..."
                )

                # Wait for authentication to complete using the websocket client's method
                auth_success = await self._websocket.wait_for_authentication(
                    timeout=timeout
                )
                if auth_success:
                    logger.success(
                        "Client successfully authenticated with PocketOption."
                    )
                    await self._emit_event("connected", {"info": self.connection_info})
                    return True
                else:
                    logger.error("Authentication failed after connection.")
                    await self.disconnect()
                    return False
            else:
                logger.error("Failed to establish Socket.IO connection.")
                return False

        except Exception as e:
            logger.error(f"Error during connection process: {e}", exc_info=True)
            await error_monitor.record_error(
                error_type="connection",
                severity=ErrorSeverity.CRITICAL,
                category=ErrorCategory.CONNECTION,
                message=f"Client connection process failed: {e}",
                context={"regions": regions, "timeout": timeout},
            )
            return False
        finally:
            elapsed = time.time() - start_time
            logger.debug(f"Connection attempt took {elapsed:.2f} seconds.")

    async def _wait_for_authentication(self, timeout: float = 20.0) -> bool:
        """
        Waits for authentication to complete using the websocket client's authentication wait.

        Args:
            timeout: How long to wait for authentication to complete.

        Returns:
            bool: True if authenticated, False otherwise.
        """
        logger.info(f"Starting authentication wait (timeout={timeout}s)")

        # Log the session ID being used
        if self._auth_data and "session" in self._auth_data:
            session_preview = self._auth_data["session"][:25] + "..."
            logger.debug(f"Using session ID: {session_preview}")

        # Use the websocket client's authentication wait mechanism
        return await self._websocket.wait_for_authentication(timeout=timeout)

    async def disconnect(self) -> None:
        """Disconnect from PocketOption and cleanup all resources"""
        logger.info("Disconnecting...")

        # Disconnect the underlying Socket.IO client
        await self._websocket.disconnect()

        # Reset state
        self.is_connected = False
        self.connection_info = None
        self._balance = None
        self._active_orders.clear()

        logger.info("Disconnected successfully")

    async def get_balance(self) -> Optional[Balance]:
        """
        Get current account balance.
        This will emit a Socket.IO event to request balance and wait for the response.
        """
        if not self.is_connected:
            # Attempt to connect if not already connected
            if not await self.connect():
                raise ConnectionError("Failed to connect to PocketOption")

        # Create an event to wait for balance update
        balance_updated_event = asyncio.Event()

        # Store the current balance to check if it gets updated
        old_balance = self._balance

        # Define a callback to set the event when balance is updated
        def on_balance_updated(data: Dict[str, Any]) -> None:
            balance_updated_event.set()

        # Add the callback
        self.add_event_callback("balance_updated", on_balance_updated)

        try:
            # Send getBalance message
            await self._websocket.send_message("getBalance")

            # Wait for balance update with timeout
            try:
                await asyncio.wait_for(
                    balance_updated_event.wait(),
                    timeout=API_LIMITS["default_timeout"],
                )
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for balance update.")
                if not self._balance:
                    raise ConnectionError("Balance data not available after timeout")

            if not self._balance:
                raise ConnectionError("Balance data not available")

            return self._balance

        finally:
            # Remove the callback
            self.remove_event_callback("balance_updated", on_balance_updated)

    async def get_active_orders(self) -> List[OrderResult]:
        """
        Retrieves the list of currently active orders.

        Returns:
            List[OrderResult]: A list of active orders.
        """
        try:
            # In a real implementation, this would fetch from the API
            # For now, we'll return orders managed by our OrderManager
            active_orders = list(self._active_orders.values())
            logger.debug(f"Retrieved {len(active_orders)} active orders.")
            return active_orders
        except Exception as e:
            logger.error(f"Error getting active orders: {e}")
            await error_monitor.record_error(
                error_type="orders_fetch",
                severity=ErrorSeverity.MEDIUM,
                category=ErrorCategory.TRADING,
                message=f"Failed to fetch active orders: {e}",
                context={},
            )
            return []

    async def place_order(
        self, asset: str, amount: float, direction: OrderDirection, duration: int
    ) -> OrderResult:
        """
        Place a binary options order using Socket.IO events.
        """
        if not self.is_connected:
            # Attempt to connect if not already connected
            if not await self.connect():
                raise ConnectionError("Failed to connect to PocketOption")

        self._validate_order_parameters(asset, amount, direction, duration)

        try:
            request_id = str(uuid.uuid4())

            # The Socket.IO event for opening an order and its payload
            order_payload: Dict[str, Any] = {
                "asset": asset,
                "amount": amount,
                "action": direction.value,  # 'call' or 'put'
                "isDemo": 1 if self.is_demo else 0,
                "requestId": request_id,
                "optionType": 100,  # Assuming this is a constant from observation
                "time": duration,  # Duration in seconds
            }

            # Use the websocket client to emit the event
            await self._websocket.send_message("openOrder", order_payload)

            logger.info(
                f"Sent order for {asset} ({direction.value}) with request ID: {request_id}"
            )

            result = await self._wait_for_order_result(
                order_id=request_id,
                order=Order(
                    asset=asset,
                    amount=amount,
                    direction=direction,
                    duration=duration,
                    request_id=request_id,
                ),
            )

            logger.info(f"Order placed: {result.order_id} - {result.status}")
            return result

        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            raise OrderError(f"Failed to place order: {e}") from e

    # --- WebSocket Event Handlers ---
    async def _on_websocket_connected(self, data: Dict[str, Any]):
        """Handle WebSocket connected event."""
        logger.info(
            f"Underlying Socket.IO client connected to {data.get('url', 'unknown')}"
        )
        self.is_connected = True
        await self._emit_event("connected", data)

    async def _on_websocket_disconnected(self, data: Dict[str, Any]):
        """Handle WebSocket disconnected event."""
        logger.warning("Underlying Socket.IO client disconnected.")
        self.is_connected = False
        await self._emit_event("disconnected", data)

    async def _on_websocket_reconnected(self, data: Dict[str, Any]):
        """Handle WebSocket reconnected event."""
        logger.info("Underlying Socket.IO client reconnected.")
        self.is_connected = True
        await self._emit_event("reconnected", data)

    async def _on_authenticated(self, data: Dict[str, Any]):
        """Handle authenticated event."""
        logger.success("Authentication successful.")
        await self._emit_event("authenticated", data)

    async def _on_successauth(self, data: Dict[str, Any]):
        """Handle successauth event."""
        logger.success("Authentication confirmed via successauth event.")
        await self._emit_event("successauth", data)

    async def _on_balance_updated(self, data: Dict[str, Any]):
        """Handle balance updated event."""
        try:
            balance = Balance(
                balance=float(data.get("balance", 0)),
                currency=data.get("currency", "USD"),
                is_demo=bool(data.get("isDemo", self.is_demo)),
            )
            self._balance = balance
            logger.info(f"Balance updated: ${balance.balance:.2f} {balance.currency}")
            await self._emit_event("balance_updated", balance.dict())
        except Exception as e:
            logger.error(f"Error processing balance update: {e}")

    async def _on_candles_received(self, data: Dict[str, Any]):
        """Handle candles received event."""
        try:
            # Process candle data
            logger.debug(f"Candles received: {len(data.get('candles', []))} candles")
            await self._emit_event("candles_received", data)
        except Exception as e:
            logger.error(f"Error processing candles: {e}")

    async def _on_order_opened(self, data: Dict[str, Any]):
        """Handle order opened event."""
        try:
            # Process order opened data
            logger.info(f"Order opened: {data}")
            await self._emit_event("order_opened", data)
        except Exception as e:
            logger.error(f"Error processing order opened: {e}")

    async def _on_order_closed(self, data: Dict[str, Any]):
        """Handle order closed event."""
        try:
            # Process order closed data
            logger.info(f"Order closed: {data}")
            await self._emit_event("order_closed", data)
        except Exception as e:
            logger.error(f"Error processing order closed: {e}")

    async def _on_stream_update(self, data: Dict[str, Any]):
        """Handle stream update event."""
        try:
            # Process stream update data
            logger.debug(f"Stream update received: {data}")
            await self._emit_event("stream_update", data)
        except Exception as e:
            logger.error(f"Error processing stream update: {e}")

    async def _on_message_received(self, data: Dict[str, Any]):
        """Handle raw message received event."""
        try:
            logger.debug(f"Raw message received: {data}")
            await self._emit_event("message_received", data)
        except Exception as e:
            logger.error(f"Error processing message: {e}")

    async def _on_json_data(self, data: Dict[str, Any]):
        """Handle JSON data event."""
        try:
            logger.debug(f"JSON data received: {data}")
            await self._emit_event("json_data", data)
        except Exception as e:
            logger.error(f"Error processing JSON data: {e}")

    async def _on_auth_error(self, data: Dict[str, Any]):
        """Handle authentication error event."""
        logger.error(f"Authentication error: {data}")
        await self._emit_event("auth_error", data)

    async def _on_connection_error(self, data: Dict[str, Any]):
        """Handle connection error event."""
        logger.error(f"Connection error: {data}")
        await self._emit_event("connection_error", data)

    async def _on_update_assets(self, data: Dict[str, Any]):
        """Handle updateAssets event."""
        try:
            logger.info(f"Asset update received: {type(data)}")
            if isinstance(data, bytes):
                # Binary data needs to be parsed
                logger.info(f"Binary asset data received, length: {len(data)}")
                # Forward raw binary data to event handlers
                await self._emit_event("assets_updated", {"binary_data": data})
            else:
                logger.info(f"Asset data received: {data}")
                await self._emit_event("assets_updated", data)
        except Exception as e:
            logger.error(f"Error processing asset update: {e}")

    # --- Event Management ---
    def add_event_callback(self, event: str, callback: Callable):
        """
        Add a callback function for a specific event.

        Args:
            event: The event name.
            callback: The callback function.
        """
        if event not in self._event_callbacks:
            self._event_callbacks[event] = []
        self._event_callbacks[event].append(callback)

    # Change to public methods as Pylance complains about attribute access
    def remove_event_callback(self, event: str, callback: Callable[..., Any]) -> None:
        """
        Remove event callback

        Args:
            event: Event name
            callback: Callback function to remove
        """
        if event in self._event_callbacks:
            try:
                self._event_callbacks[event].remove(callback)
            except ValueError:
                pass

    async def _emit_event(self, event: str, data: Dict[str, Any]):
        """
        Emit an event to all registered callbacks.

        Args:
            event: The event name.
            data: The event data.
        """
        if event in self._event_callbacks:
            for callback in self._event_callbacks[event]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(data)
                    else:
                        callback(data)
                except Exception as e:
                    logger.error(f"Error in event callback for {event}: {e}")

    async def _wait_for_order_result(
        self, order_id: str, order: Order, timeout: float = 30.0
    ) -> OrderResult:
        """Wait for order execution result"""
        start_time = time.time()

        # Wait for order to appear in tracking system
        while time.time() - start_time < timeout:
            # Check if order was added to active orders (by _on_order_opened or _on_json_data)
            if order_id in self._active_orders:
                if self.enable_logging:
                    logger.success(f" Order {order_id} found in active tracking")
                return self._active_orders[order_id]

            # Check if order went directly to results (failed or completed)
            if order_id in self._order_results:
                if self.enable_logging:
                    logger.info(f" Order {order_id} found in completed results")
                return self._order_results[order_id]

            await asyncio.sleep(0.2)  # Check every 200ms

        # Check one more time before creating fallback
        if order_id in self._active_orders:
            if self.enable_logging:
                logger.success(
                    f" Order {order_id} found in active tracking (final check)"
                )
            return self._active_orders[order_id]

        if order_id in self._order_results:
            if self.enable_logging:
                logger.info(
                    f" Order {order_id} found in completed results (final check)"
                )
            return self._order_results[order_id]

        # If timeout, create a fallback result with the original order data
        if self.enable_logging:
            logger.warning(
                f" Order {order_id} timed out waiting for server response, creating fallback result"
            )
        fallback_result = OrderResult(
            order_id=order_id,
            asset=order.asset,
            amount=order.amount,
            direction=order.direction,
            duration=order.duration,
            status=OrderStatus.ACTIVE,  # Assume it's active since it was placed
            placed_at=datetime.now(),
            expires_at=datetime.now() + timedelta(seconds=order.duration),
            error_message="Timeout waiting for server confirmation",
        )  # Store it in active orders in case server responds later
        self._active_orders[order_id] = fallback_result
        if self.enable_logging:
            logger.info(f"Created fallback order result for {order_id}")
        return fallback_result

    def _validate_order_parameters(
        self, asset: str, amount: float, direction: OrderDirection, duration: int
    ) -> None:
        """Validate order parameters"""
        if asset not in ASSETS:
            raise InvalidParameterError(f"Invalid asset: {asset}")

        if (
            amount < API_LIMITS["min_order_amount"]
            or amount > API_LIMITS["max_order_amount"]
        ):
            raise InvalidParameterError(
                f"Amount must be between {API_LIMITS['min_order_amount']} and {API_LIMITS['max_order_amount']}"
            )

        if (
            duration < API_LIMITS["min_duration"]
            or duration > API_LIMITS["max_duration"]
        ):
            raise InvalidParameterError(
                f"Duration must be between {API_LIMITS['min_duration']} and {API_LIMITS['max_duration']} seconds"
            )

    async def get_candles(
        self,
        asset: str,
        timeframe: Union[str, int],
        count: int = 100,
        end_time: Optional[datetime] = None,
    ) -> List[Candle]:
        """
        Get historical candle data using Socket.IO `changeSymbol` event.
        """
        if not self.is_connected:
            if self.auto_reconnect:
                logger.info(
                    f"Connection lost, attempting reconnection for {asset} candles..."
                )
                reconnected = await self._attempt_reconnection()
                if not reconnected:
                    raise ConnectionError(
                        "Not connected to PocketOption and reconnection failed"
                    )
            else:
                raise ConnectionError("Not connected to PocketOption")

        if isinstance(timeframe, str):
            timeframe_seconds = TIMEFRAMES.get(timeframe, 60)
        else:
            timeframe_seconds = timeframe

        if asset not in ASSETS:
            raise InvalidParameterError(f"Invalid asset: {asset}")

        if not end_time:
            end_time = datetime.now()

        max_retries = 2
        for attempt in range(max_retries):
            try:
                candle_future = asyncio.Future[Any]()
                request_id = f"{asset}_{timeframe_seconds}"

                if not hasattr(self, "_candle_requests"):
                    self._candle_requests = {}
                self._candle_requests[request_id] = candle_future

                message_payload: Dict[str, Any] = {
                    "asset": asset,
                    "period": timeframe_seconds,
                }

                await self._websocket.send_message("changeSymbol", message_payload)
                logger.debug(
                    f"Requested candles for {asset} ({timeframe_seconds}s) with changeSymbol"
                )

                candles = await asyncio.wait_for(
                    candle_future, timeout=API_LIMITS["default_timeout"]
                )

                # Filter/truncate to `count` if the server returns more
                if len(candles) > count:
                    candles = sorted(candles, key=lambda c: c.timestamp, reverse=True)[
                        :count
                    ]
                    candles.reverse()  # Sort ascending by time for consistency

                self._candles_cache[request_id] = candles
                logger.info(f"Retrieved {len(candles)} candles for {asset}")
                return candles

            except asyncio.TimeoutError:
                logger.warning(f"Candle request timed out for {asset}")
                if request_id in self._candle_requests:
                    del self._candle_requests[request_id]  # Clean up
                if attempt == max_retries - 1:
                    logger.error(
                        f"Failed to get candles after {max_retries} attempts due to timeout"
                    )  # Use max_attempts
                    raise ConnectionError(
                        f"Failed to get candles after {max_retries} attempts due to timeout"
                    )
                # Attempt reconnection before retrying
                if self.auto_reconnect:
                    reconnected = await self._attempt_reconnection()
                    if reconnected:
                        logger.info(f"Reconnected, retrying candle request for {asset}")
                        continue
                    raise ConnectionError("Reconnection failed, cannot get candles.")
                raise ConnectionError("Not connected and auto_reconnect is disabled.")
            except Exception as e:
                logger.error(f"Failed to get candles for {asset}: {e}")
                if request_id in self._candle_requests:
                    del self._candle_requests[request_id]  # Clean up
                raise ConnectionError(f"Failed to get candles: {e}")

        raise ConnectionError(
            f"Failed to get candles after {max_retries} attempts (unexpected state)"
        )

    async def check_order_result(self, order_id: str) -> Optional[OrderResult]:
        """
        Check the result of a specific order

        Args:
            order_id: Order ID to check
            Returns:
            OrderResult: Order result or None if not found
        """
        # First check active orders
        if order_id in self._active_orders:
            return self._active_orders[order_id]

        # Then check completed orders
        if order_id in self._order_results:
            return self._order_results[order_id]

        # Not found
        return None

    async def get_candles_dataframe(
        self,
        asset: str,
        timeframe: Union[str, int],
        count: int = 100,
        end_time: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """
        Get historical candle data as DataFrame

        Args:
            asset: Asset symbol
            timeframe: Timeframe (e.g., "1m", "5m", 60)
            count: Number of candles to retrieve
            end_time: End time for data (defaults to now)

        Returns:
            pd.DataFrame: Historical candle data
        """
        candles = await self.get_candles(asset, timeframe, count, end_time)

        # Convert to DataFrame
        data: List[Dict[str, Any]] = []
        for candle in candles:
            data.append(
                {
                    "timestamp": candle.timestamp,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                }
            )
        df = pd.DataFrame(data)

        if not df.empty:
            df.set_index("timestamp", inplace=True)
            df.sort_index(inplace=True)

            return df

    async def send_message(self, event_name: str, data: Any = None) -> bool:
        """Send message through active connection (delegates to Socket.IO client)"""
        try:
            return await self._websocket.send_message(event_name, data)
        except Exception as e:
            logger.error(f"Failed to send message via WebSocket client: {e}")
            return False

    def get_connection_stats(self) -> Dict[str, Any]:
        """
        Get comprehensive connection statistics from the underlying WebSocket client.
        """
        stats = {}
        if self._websocket.sio.eio is not None:
            stats["connected_status"] = self._websocket.sio.eio.state
            stats["current_url"] = (
                self._websocket.sio.eio.url if self._websocket.sio.eio.url else None
            )
            stats["reconnect_attempts_sio"] = (
                self._websocket.sio.eio.reconnection_attempts
                if self._websocket.sio.eio
                else 0
            )

        stats["is_connected"] = self._websocket.is_connected

        return stats

    @property
    def session_id(self) -> Optional[str]:
        """Get the session ID from authentication data"""
        return self._auth_data.get("session")

    async def _request_candles(
        self, asset: str, timeframe: int, count: int, end_time: datetime
    ):
        """Request candle data from server using the correct changeSymbol format"""

        # Create message data in the format expected by PocketOption for real-time candles
        data: Dict[str, Any] = {
            "asset": str(asset),
            "period": timeframe,  # timeframe in seconds
        }

        if self.enable_logging:
            logger.debug(f"Requesting candles with changeSymbol: {data}")

        # Create a future to wait for the response
        candle_future = asyncio.Future[Any]()
        request_id = f"{asset}_{timeframe}"

        # Store the future for this request
        # if not hasattr(self, "_candle_requests"): # Already initialized in __init__
        #     self._candle_requests = {}
        self._candle_requests[request_id] = candle_future

        # Send the request using appropriate connection
        await self._websocket.send_message(
            "changeSymbol", data
        )  # Use the client's send_message now

        try:
            # Wait for the response (with timeout)
            candles = await asyncio.wait_for(candle_future, timeout=10.0)
            return candles
        except asyncio.TimeoutError:
            if self.enable_logging:
                logger.warning(f"Candle request timed out for {asset}")
            return []
        finally:
            # Clean up the request
            if request_id in self._candle_requests:
                del self._candle_requests[request_id]

    def _parse_candles_data(self, candles_data: List[Any], asset: str, timeframe: int):
        """Parse candles data from server response"""
        candles: List[Candle] = []

        try:
            for candle_data_item in candles_data:
                # Check for dictionary format (from stream updates)
                if isinstance(candle_data_item, dict):
                    candle = Candle(
                        timestamp=datetime.fromtimestamp(
                            candle_data_item.get("time", 0)
                        ),  # Use candle_data_item
                        open=float(
                            candle_data_item.get("open", 0)
                        ),  # Use candle_data_item
                        high=float(
                            candle_data_item.get("high", 0)
                        ),  # Use candle_data_item
                        low=float(
                            candle_data_item.get("low", 0)
                        ),  # Use candle_data_item
                        close=float(
                            candle_data_item.get("close", 0)
                        ),  # Use candle_data_item
                        volume=float(
                            candle_data_item.get("volume", 0)
                        ),  # Use candle_data_item
                        asset=asset,
                        timeframe=timeframe,
                    )
                    candles.append(candle)
                # Check for list/tuple format (from loadHistoryPeriod or old streams)
                elif (
                    isinstance(candle_data_item, (list, tuple))
                    and len(candle_data_item) >= 5
                ):
                    # Server format variations: [timestamp, open, low, high, close] or [timestamp, open, close, high, low, volume]
                    # Assume the latter for robust parsing based on common PO patterns.
                    # Always ensure high >= low.

                    ts = candle_data_item[0]
                    op = float(candle_data_item[1])
                    cl = float(candle_data_item[2])  # Assuming close is at index 2
                    hi = float(candle_data_item[3])  # Assuming high is at index 3
                    lo = float(candle_data_item[4])  # Assuming low is at index 4

                    # Correcting potential low/high swaps, ensuring high >= low
                    actual_high = max(hi, lo)
                    actual_low = min(hi, lo)

                    vol = (
                        float(candle_data_item[5]) if len(candle_data_item) > 5 else 0.0
                    )

                    candle = Candle(
                        timestamp=datetime.fromtimestamp(ts),
                        open=op,
                        high=actual_high,
                        low=actual_low,
                        close=cl,
                        volume=vol,
                        asset=asset,
                        timeframe=timeframe,
                    )
                    candles.append(candle)

        except Exception as e:
            if self.enable_logging:
                logger.error(
                    f"Error parsing candles data: {e}. Raw data: {candles_data[:100]}..."
                )

        return candles

    async def _on_json_data(self, data: Dict[str, Any]) -> None:
        """Handle detailed order data from JSON messages received via Socket.IO `json` event."""
        if not data:
            logger.warning(f"Received non-dict JSON data: {data}")
            return

        if "candles" in data and isinstance(data["candles"], list):
            asset_from_data = data.get("asset")
            period_from_data = data.get("period")
            if asset_from_data and period_from_data:
                request_id = f"{asset_from_data}_{period_from_data}"
                if (
                    request_id in self._candle_requests
                    and not self._candle_requests[request_id].done()
                ):
                    candles = self._parse_candles_data(
                        data["candles"],
                        asset_from_data,
                        period_from_data,
                    )
                    self._candle_requests[request_id].set_result(candles)
                    if self.enable_logging:
                        logger.success(
                            f"Candles data received and resolved future: {len(candles)} candles for {asset_from_data}"
                        )
                    # No need to delete future here; `_request_candles` finally block handles it.
                    return  # Handled this message, exit

        # Check if this is detailed order data with requestId
        if "requestId" in data and "asset" in data and "amount" in data:
            request_id = str(data["requestId"])

            # If this is a new order, add it to tracking
            # Or if it's an update to an existing active order
            if (
                request_id in self._active_orders
                or request_id not in self._order_results
            ):
                direction_val = data.get("action", "").lower()
                order_result = OrderResult(
                    order_id=request_id,
                    asset=data.get("asset", "UNKNOWN"),
                    amount=float(data.get("amount", 0)),
                    direction=OrderDirection.CALL
                    if direction_val == "call"
                    else OrderDirection.PUT,
                    duration=int(data.get("time", 60)),
                    status=OrderStatus.ACTIVE,  # Assume active unless profit/loss indicates otherwise
                    placed_at=datetime.now(),
                    expires_at=datetime.now()
                    + timedelta(seconds=int(data.get("time", 60))),
                    profit=float(data.get("profit", 0)) if "profit" in data else None,
                    payout=float(data.get("payout", 0)) if "payout" in data else None,
                    error_message=data.get("error"),  # Capture potential error messages
                )

                # If there's profit/payout, it's a closed order
                if (
                    "profit" in data
                ):  # Check for profit being explicitly in data and not None
                    if (
                        order_result.profit is not None and order_result.profit > 0
                    ):  # Check for None
                        order_result.status = OrderStatus.WIN
                    elif (
                        order_result.profit is not None and order_result.profit < 0
                    ):  # Check for None
                        order_result.status = OrderStatus.LOSE
                    else:
                        order_result.status = OrderStatus.CLOSED  # Draw/No profit

                    self._order_results[request_id] = order_result
                    if request_id in self._active_orders:
                        del self._active_orders[request_id]
                    logger.success(
                        f"Order {request_id} closed from JSON data: {order_result.status.value}, Profit: ${order_result.profit:.2f}"
                    )
                    # Use self._emit_event
                    await self._emit_event("order_closed", order_result)  #
                else:  # It's an active order update or initial confirmation
                    self._active_orders[request_id] = order_result
                    logger.info(
                        f"Order {request_id} updated/confirmed from JSON data: Active"
                    )
                    # Use self._emit_event
                    await self._emit_event(
                        "order_opened", order_result
                    )  # Can re-emit for updates
