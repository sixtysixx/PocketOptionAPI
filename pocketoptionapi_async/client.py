"""
Professional Async PocketOption API Client
"""

import asyncio
import json
import time
import uuid
from typing import Optional, List, Dict, Any, Union, Callable
from datetime import datetime, timedelta
from collections import defaultdict
import pandas as pd
from loguru import logger

# Import monitoring components
from .monitoring import error_monitor, health_checker, ErrorCategory, ErrorSeverity

# Now AsyncWebSocketClient is our Socket.IO client wrapper
from .websocket_client import AsyncWebSocketClient
from .models import (
    Balance,
    Candle,
    Order,
    OrderResult,
    OrderStatus,
    OrderDirection,
    ServerTime,
    ConnectionInfo,  # Import ConnectionInfo
)
from .constants import ASSETS, REGIONS, TIMEFRAMES, API_LIMITS
from .exceptions import (
    PocketOptionError,
    ConnectionError,
    AuthenticationError,
    InvalidParameterError,
    OrderError,
    WebSocketError,  # Import WebSocketError
)
from .utils import format_session_id  # Keep for formatting auth data


class AsyncPocketOptionClient:
    """
    Professional async PocketOption API client with modern Python practices
    """

    def __init__(
        self,
        ssid: str,
        is_demo: bool = True,
        region: Optional[str] = None,
        uid: int = 0,
        platform: int = 1,
        is_fast_history: bool = True,
        # persistent_connection and auto_reconnect are now handled by AsyncWebSocketClient internals
        # but we keep them for interface consistency and to pass to it.
        persistent_connection: bool = True,  # Default to True, as it's the robust way
        auto_reconnect: bool = True,  # Default to True
        enable_logging: bool = True,
    ):
        """
        Initialize async PocketOption client with enhanced monitoring

        Args:
            ssid: Complete SSID string or raw session ID for authentication
            is_demo: Whether to use demo account
            region: Preferred region for connection
            uid: User ID (if providing raw session)
            platform: Platform identifier (1=web, 3=mobile)
            is_fast_history: Enable fast history loading
            persistent_connection: Enable persistent connection with keep-alive (now handled by AsyncWebSocketClient)
            auto_reconnect: Enable automatic reconnection on disconnection (now handled by AsyncWebSocketClient)
            enable_logging: Enable detailed logging (default: True)
        """
        self.raw_ssid = ssid
        self.is_demo = is_demo
        self.preferred_region = region
        self.uid = uid
        self.platform = platform
        self.is_fast_history = is_fast_history
        self.persistent_connection = persistent_connection  # Retained for config
        self.auto_reconnect = auto_reconnect  # Retained for config
        self.enable_logging = enable_logging

        # Configure logging based on preference
        if not enable_logging:
            logger.remove()
            logger.add(lambda msg: None, level="CRITICAL")  # Disable most logging

        # Parse SSID if it's a complete auth message or treat as raw session ID
        self._auth_data: Dict[str, Any] = {}
        self._parse_ssid_into_auth_data(ssid, is_demo, uid, platform, is_fast_history)

        # Core components
        self._websocket = AsyncWebSocketClient()  # Now a Socket.IO client wrapper
        self._balance: Optional[Balance] = None
        self._orders: Dict[str, OrderResult] = {}
        self._active_orders: Dict[str, OrderResult] = {}
        self._order_results: Dict[str, OrderResult] = {}
        self._candles_cache: Dict[str, List[Candle]] = {}
        self._server_time: Optional[ServerTime] = None
        # Initialize _event_callbacks here to resolve Pylance attribute access
        self._event_callbacks: Dict[str, List[Callable]] = defaultdict(list)
        self._balance_updated_event = asyncio.Event()
        # Initialize _candle_requests here to resolve Pylance attribute access
        self._candle_requests: Dict[str, asyncio.Future] = {}

        # Setup event handlers for websocket messages (from AsyncWebSocketClient)
        self._setup_websocket_event_handlers()

        # Enhanced monitoring and error handling
        self._error_monitor = error_monitor
        self._health_checker = health_checker

        # Performance tracking
        self._operation_metrics: Dict[str, List[float]] = defaultdict(list)
        self._last_health_check = time.time()

        # Keep-alive and reconnection logic is now mostly handled within AsyncWebSocketClient.
        # These are just flags/placeholders for old API compatibility logging.
        self._is_persistent = (
            False  # Becomes True if connection is successfully established
        )
        # by the new AsyncWebSocketClient using its internal persistence.

        # Connection statistics (now sourced directly from _websocket)
        self._connection_stats = {
            "total_connections": 0,
            "successful_connections": 0,
            "total_reconnects": 0,
            "last_ping_time": None,
            "messages_sent": 0,
            "messages_received": 0,
            "connection_start_time": None,
        }

        logger.info(
            f"Initialized PocketOption client (demo={is_demo}, uid={uid}, persistent={self.persistent_connection}) with enhanced monitoring"
            if enable_logging
            else ""
        )

    def _parse_ssid_into_auth_data(
        self, ssid: str, is_demo: bool, uid: int, platform: int, is_fast_history: bool
    ):
        """
        Parses the provided SSID or constructs auth data from parameters.
        This prepares the dictionary to be passed to socketio.AsyncClient's `auth` parameter.
        """
        # If the SSID starts with '42["auth",', try to parse it directly
        if ssid.startswith('42["auth",'):
            try:
                # Extract the JSON part and load it
                json_str = ssid[2:]
                parsed_data = json.loads(json_str)
                if (
                    isinstance(parsed_data, list)
                    and len(parsed_data) > 1
                    and isinstance(parsed_data[1], dict)
                ):
                    self._auth_data = parsed_data[1]
                    # Override with constructor parameters if they differ, or if it's a raw session ID
                    self._auth_data["isDemo"] = 1 if is_demo else 0
                    self._auth_data["uid"] = uid
                    self._auth_data["platform"] = platform
                    self._auth_data["isFastHistory"] = is_fast_history
                    # Keep existing 'session' if present, otherwise set it
                    if "session" not in self._auth_data:
                        self._auth_data["session"] = (
                            ssid  # Fallback: use raw SSID as session
                        )
                    if self.enable_logging:
                        logger.info("SSID parsed from complete auth message.")
                    return
            except (json.JSONDecodeError, IndexError, TypeError, ValueError) as e:
                if self.enable_logging:
                    logger.warning(
                        f"Failed to parse complete SSID string: {e}. Treating as raw session ID."
                    )

        # If parsing failed or it's a raw session ID
        self._auth_data = {
            "session": ssid,
            "isDemo": 1 if is_demo else 0,
            "uid": uid,
            "platform": platform,
            "isFastHistory": is_fast_history,
        }
        if self.enable_logging:
            logger.info(
                "SSID treated as raw session ID or constructed from parameters."
            )

    # --- Private Event Handlers for internal WebSocket client events ---
    # Moved _on_websocket_... and _on_payout_update, _on_auth_error here to define them before usage
    async def _on_websocket_connected(self, data: Dict[str, Any]) -> None:
        """Handle 'connected' event from AsyncWebSocketClient (Socket.IO client)."""
        logger.info(f"Underlying Socket.IO client connected to {data.get('url')}")
        # The main client still needs to await authentication before being truly "connected" for API use.
        # This is just an internal notification from the lower layer.
        self._connection_stats["total_connections"] = (
            self._websocket.sio.reconnect_attempts + 1
        )  # type: ignore[attr-defined]
        self._connection_stats["connection_start_time"] = datetime.now()

    async def _on_websocket_disconnected(self, data: Dict[str, Any]) -> None:
        """Handle 'disconnected' event from AsyncWebSocketClient (Socket.IO client)."""
        logger.warning("Underlying Socket.IO client disconnected.")
        await self._error_monitor.record_error(
            error_type="websocket_disconnected",
            severity=ErrorSeverity.MEDIUM,
            category=ErrorCategory.CONNECTION,
            message="Socket.IO client reported disconnection.",
        )
        self._balance = None  # Clear state on disconnect
        self._balance_updated_event.clear()
        await self._emit_event("disconnected", data)  # Use await for _emit_event

    async def _on_websocket_reconnected(self, data: Dict[str, Any]) -> None:
        """Handle 'reconnected' event from AsyncWebSocketClient (Socket.IO client)."""
        logger.info(
            f"Underlying Socket.IO client reconnected to {data.get('url')} (attempt {data.get('attempt')})"
        )
        self._connection_stats["total_reconnects"] += 1
        self._connection_stats["connection_start_time"] = (
            datetime.now()
        )  # Update on reconnect
        # After reconnect, we need to re-authenticate and re-subscribe data.
        try:
            await self._wait_for_authentication()  # Re-authenticate
            await (
                self._initialize_data()
            )  # Re-initialize data (e.g., re-request balance)
            await self._emit_event("reconnected", data)  # Use await for _emit_event
            logger.info(
                "Client re-authenticated and data re-initialized after reconnection."
            )
        except Exception as e:
            logger.error(
                f"Failed to re-authenticate or re-initialize after reconnect: {e}"
            )
            await self._error_monitor.record_error(
                error_type="reauthentication_failed",
                severity=ErrorSeverity.HIGH,
                category=ErrorCategory.AUTHENTICATION,
                message=f"Failed to re-authenticate or re-initialize data after reconnect: {e}",
            )
            # If re-auth fails, effectively treat as a disconnect, let monitoring handle further
            await self._websocket.disconnect()

    async def _on_payout_update(self, data: Dict[str, Any]) -> None:
        """Handle payout update event."""
        if self.enable_logging:
            logger.info(f"Payout update received: {data}")
        await self._emit_event("payout_update", data)  # Use await for _emit_event

    async def _on_auth_error(self, data: Dict[str, Any]) -> None:
        """Handle authentication errors from the WebSocket client."""
        logger.error(
            f"Authentication error reported by WebSocket client: {data.get('message')}"
        )
        await self._emit_event("auth_error", data)  # Use await for _emit_event
        # This will be caught by _wait_for_authentication and potentially trigger disconnect/reconnection logic.

    def _setup_websocket_event_handlers(self):
        """
        Setup event handlers for the underlying AsyncWebSocketClient (Socket.IO wrapper).
        These handlers will dispatch to the client's public event callbacks.
        """
        self._websocket.add_event_handler("connected", self._on_websocket_connected)
        self._websocket.add_event_handler(
            "disconnected", self._on_websocket_disconnected
        )
        self._websocket.add_event_handler("reconnected", self._on_websocket_reconnected)
        self._websocket.add_event_handler(
            "authenticated", self._on_authenticated
        )  # Direct from websocket
        self._websocket.add_event_handler("balance_data", self._on_balance_data)
        self._websocket.add_event_handler("balance_updated", self._on_balance_updated)
        self._websocket.add_event_handler("order_opened", self._on_order_opened)
        self._websocket.add_event_handler("order_closed", self._on_order_closed)
        self._websocket.add_event_handler("stream_update", self._on_stream_update)
        self._websocket.add_event_handler("candles_received", self._on_candles_received)
        self._websocket.add_event_handler("json_data", self._on_json_data)
        self._websocket.add_event_handler("payout_update", self._on_payout_update)
        self._websocket.add_event_handler(
            "auth_error", self._on_auth_error
        )  # Forward auth errors

    async def connect(
        self, regions: Optional[List[str]] = None, persistent: Optional[bool] = None
    ) -> bool:
        """
        Connect to PocketOption with multiple region support using Socket.IO.

        Args:
            regions: List of regions to try (uses defaults if None)
            persistent: Override persistent connection setting (now influences AsyncWebSocketClient's internal behavior)

        Returns:
            bool: True if connected successfully
        """
        logger.info("Connecting to PocketOption (Socket.IO client)...")
        if persistent is not None:
            self.persistent_connection = bool(persistent)  # Update internal flag

        # Use appropriate regions based on demo mode
        if not regions:
            if self.is_demo:
                # For demo mode, only use demo regions
                regions_list = [
                    name
                    for name, _ in REGIONS.get_all_regions().items()
                    if "DEMO" in name.upper()
                ]
                logger.info(f"Demo mode: Using demo regions: {regions_list}")
            else:
                # For live mode, use all regions except demo
                regions_list = [
                    name
                    for name, url in REGIONS.get_all_regions().items()
                    if "DEMO" not in name.upper()
                ]
                logger.info(f"Live mode: Using non-demo regions: {regions_list}")
        else:
            regions_list = regions

        # Map region names to URLs for the websocket client
        # Filter out None values from the list to match List[str] type hint
        urls_to_try: List[str] = [
            url
            for url in [REGIONS.get_region(name) for name in regions_list]
            if url is not None
        ]  #
        if not urls_to_try:
            logger.error("No valid WebSocket URLs found for the specified regions.")
            raise ConnectionError("No valid WebSocket URLs to connect to.")

        self._connection_stats["total_connections"] += 1
        self._connection_stats["connection_start_time"] = time.time()

        try:
            # The AsyncWebSocketClient (Socket.IO wrapper) handles persistence and reconnections
            success = await self._websocket.connect(urls_to_try, self._auth_data)

            if success:
                logger.info(
                    "Successfully initiated Socket.IO connection. Waiting for authentication..."
                )
                # Wait for authentication event to ensure session is active
                try:
                    await self._wait_for_authentication()
                    await self._initialize_data()
                    self._is_persistent = (
                        self._websocket.is_connected
                    )  # Indicate persistence is active via client
                    self._connection_stats["successful_connections"] += 1
                    logger.info("Client fully connected and authenticated.")
                    return True
                except AuthenticationError as e:
                    logger.error(f"Authentication failed after connection: {e}")
                    await self._websocket.disconnect()  # Disconnect if auth fails
                    return False
                except asyncio.TimeoutError:
                    logger.error("Timeout waiting for authentication after connection.")
                    await self._websocket.disconnect()
                    return False
            else:
                logger.error("Initial Socket.IO connection failed.")
                return False

        except Exception as e:
            logger.error(f"Error during connection process: {e}")
            await self._error_monitor.record_error(
                error_type="client_connection_failed",
                severity=ErrorSeverity.CRITICAL,
                category=ErrorCategory.CONNECTION,
                message=f"Client connection process failed: {e}",
            )
            return False

    async def disconnect(self) -> None:
        """Disconnect from PocketOption and cleanup all resources"""
        logger.info("Disconnecting from PocketOption...")

        # Disconnect the underlying Socket.IO client
        await self._websocket.disconnect()

        # Reset state
        self._is_persistent = False
        self._balance = None
        self._orders.clear()
        self._balance_updated_event.clear()

        logger.info("Disconnected successfully")

    async def get_balance(self) -> Balance:
        """
        Get current account balance.
        This will emit a Socket.IO event to request balance and wait for the response.
        """
        if not self.is_connected:
            raise ConnectionError("Not connected to PocketOption")

        self._balance_updated_event.clear()  # Clear event before requesting

        # Send the balance request as a Socket.IO event
        # This is a key change: instead of '42["getBalance"]', we use emit
        await self._websocket.send_message("getBalance")

        try:
            await asyncio.wait_for(
                self._balance_updated_event.wait(),
                timeout=API_LIMITS["default_timeout"],
            )
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for balance update.")
            if not self._balance:
                raise PocketOptionError("Balance data not available after timeout")

        if not self._balance:
            raise PocketOptionError("Balance data not available")

        return self._balance

    async def place_order(
        self, asset: str, amount: float, direction: OrderDirection, duration: int
    ) -> OrderResult:
        """
        Place a binary options order using Socket.IO events.
        """
        if not self.is_connected:
            raise ConnectionError("Not connected to PocketOption")

        self._validate_order_parameters(asset, amount, direction, duration)

        try:
            request_id = str(uuid.uuid4())

            # The Socket.IO event for opening an order and its payload
            order_payload = {
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

            # Wait for result (this will either get the real server response or create a fallback)
            # Pylance might still flag this, but it's defined in the class.
            # Adding type hint to self (AsyncPocketOptionClient) can sometimes help.
            result = await self._wait_for_order_result(
                order_id=request_id,
                order=Order(  #
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
            raise OrderError(f"Failed to place order: {e}")

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
                candle_future = asyncio.Future()
                request_id = f"{asset}_{timeframe_seconds}"

                # Store the future for this request.
                # _candle_requests should be an attribute, initialized in __init__
                # It is already initialized now, so this check is for robustness.
                if not hasattr(self, "_candle_requests"):
                    self._candle_requests = {}
                self._candle_requests[request_id] = candle_future

                # Prepare the message for `changeSymbol` Socket.IO event
                # This event is observed to subscribe to real-time candles and also fetch initial history.
                message_payload = {
                    "asset": asset,
                    "period": timeframe_seconds,
                    # count and end_time are not typically sent directly with changeSymbol,
                    # the server streams data or sends initial batch based on subscription.
                    # If specific historical range is needed, a different API endpoint might be required
                    # or a specific 'loadHistoryPeriod' event with params not directly observed here.
                    # For now, we assume `changeSymbol` is sufficient to get latest candles.
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
                    raise PocketOptionError(
                        f"Failed to get candles after {max_retries} attempts due to timeout"
                    )
                # Attempt reconnection before retrying
                if self.auto_reconnect:
                    reconnected = await self._attempt_reconnection()
                    if reconnected:
                        logger.info(f"Reconnected, retrying candle request for {asset}")
                        continue
                    else:
                        raise ConnectionError(
                            "Reconnection failed, cannot get candles."
                        )
                else:
                    raise ConnectionError(
                        "Not connected and auto_reconnect is disabled."
                    )
            except Exception as e:
                logger.error(f"Failed to get candles for {asset}: {e}")
                if request_id in self._candle_requests:
                    del self._candle_requests[request_id]  # Clean up
                raise PocketOptionError(f"Failed to get candles: {e}")

        raise PocketOptionError(
            f"Failed to get candles after {max_retries} attempts (unexpected state)"
        )

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
        data = []
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

    async def get_active_orders(self) -> List[OrderResult]:
        """
        Get all active orders

        Returns:
            List[OrderResult]: Active orders
        """
        return list(self._active_orders.values())

    # Change to public methods as Pylance complains about attribute access
    def add_event_callback(self, event: str, callback: Callable) -> None:
        """
        Add event callback

        Args:
            event: Event name (e.g., 'order_closed', 'balance_updated')
            callback: Callback function
        """
        if event not in self._event_callbacks:
            self._event_callbacks[event] = []
        self._event_callbacks[event].append(callback)

    # Change to public methods as Pylance complains about attribute access
    def remove_event_callback(self, event: str, callback: Callable) -> None:
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

    @property
    def is_connected(self) -> bool:
        """Check if client is connected (delegates to Socket.IO client)"""
        return self._websocket.is_connected

    @property
    def connection_info(self) -> Optional[ConnectionInfo]:
        """Get connection information (delegates to Socket.IO client)"""
        return self._websocket.connection_info

    async def send_message(self, event_name: str, data: Any = None) -> bool:
        """Send message through active connection (delegates to Socket.IO client)"""
        try:
            return await self._websocket.send_message(event_name, data)
        except WebSocketError as e:
            logger.error(f"Failed to send message via WebSocket client: {e}")
            return False

    def get_connection_stats(self) -> Dict[str, Any]:
        """
        Get comprehensive connection statistics from the underlying WebSocket client.
        """
        # This needs to adapt to what AsyncWebSocketClient provides
        stats = {}
        if self._websocket.sio.eio:  # Check if eio client exists
            stats["connected_status"] = self._websocket.sio.eio.state  # Engine.IO state
            stats["current_url"] = self._websocket.sio.url
            stats["reconnect_attempts_sio"] = self._websocket.sio.reconnect_attempts

        stats["is_connected"] = self._websocket.is_connected

        # Add basic stats tracked by this client
        stats.update(self._connection_stats)

        return stats

    # Private methods (adapted for Socket.IO)

    def _parse_complete_ssid(self, ssid: str) -> None:
        """
        Old method, replaced by _parse_ssid_into_auth_data.
        This method will no longer be used directly for parsing as `_auth_data`
        is prepared earlier.
        """
        pass  # No longer needed here

    async def _wait_for_authentication(self, timeout: float = 10.0) -> None:
        """Wait for authentication to complete, using an internal event."""
        auth_received_event = asyncio.Event()

        def on_auth_success(data):
            auth_received_event.set()

        # Temporarily add a handler to the client's internal event system
        # Call public method for event callbacks
        self.add_event_callback("authenticated", on_auth_success)  #

        try:
            await asyncio.wait_for(auth_received_event.wait(), timeout=timeout)
            logger.success("Authentication completed successfully.")
        except asyncio.TimeoutError:
            logger.error(
                "Authentication timeout: Did not receive 'authenticated' event."
            )
            raise AuthenticationError("Authentication timeout")
        finally:
            # Call public method for event callbacks
            self.remove_event_callback("authenticated", on_auth_success)  #

    async def _initialize_data(self) -> None:
        """Initialize client data after connection and authentication."""
        # Request initial balance and wait for it
        self._balance_updated_event.clear()
        # Request balance directly via Socket.IO event
        await self._websocket.send_message("getBalance")
        try:
            await asyncio.wait_for(
                self._balance_updated_event.wait(),
                timeout=API_LIMITS["default_timeout"],
            )
            logger.info("Initial balance received during initialization.")
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for initial balance during initialization.")
        except Exception as e:
            logger.error(f"Error during initial balance fetch: {e}")

        # Setup time synchronization
        await self._setup_time_sync()

    async def _request_balance_update(self) -> None:
        """Request balance update from server by emitting Socket.IO event."""
        # This method is now implicitly called by get_balance()
        await self._websocket.send_message("getBalance")

    async def _setup_time_sync(self) -> None:
        """Setup server time synchronization. This might be a direct API call or just local time tracking."""
        # For PocketOption, getting server time is often implicitly done via a message,
        # or it's not a direct 'request' but rather derived from message timestamps.
        # For now, we'll keep the simple local time assignment.
        local_time = datetime.now().timestamp()
        self._server_time = ServerTime(
            server_timestamp=local_time, local_timestamp=local_time, offset=0.0
        )
        logger.info("Server time synchronized (using local time for now).")

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

    async def _send_order(self, order: Order) -> None:
        """
        Send order to server by emitting a Socket.IO event.
        This function is now called internally by `place_order`.
        """
        # Format asset name with # prefix if not already present
        asset_name = order.asset

        # Create the message payload for 'openOrder' event
        order_payload = {
            "asset": asset_name,
            "amount": order.amount,
            "action": order.direction.value,
            "isDemo": 1 if self.is_demo else 0,
            "requestId": order.request_id,
            "optionType": 100,  # This seems to be a common constant
            "time": order.duration,
        }

        # Emit the 'openOrder' event with the payload
        await self._websocket.send_message("openOrder", order_payload)

        if self.enable_logging:
            logger.debug(f"Sent order: {order_payload}")

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
                    logger.info(f"📋 Order {order_id} found in completed results")
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
                    f"📋 Order {order_id} found in completed results (final check)"
                )
            return self._order_results[order_id]

        # If timeout, create a fallback result with the original order data
        if self.enable_logging:
            logger.warning(
                f"⏰ Order {order_id} timed out waiting for server response, creating fallback result"
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
            logger.info(f"📝 Created fallback order result for {order_id}")
        return fallback_result

    async def check_win(
        self, order_id: str, max_wait_time: float = 300.0
    ) -> Optional[Dict[str, Any]]:
        """
        Check win functionality - waits for trade completion message

        Args:
            order_id: Order ID to check
            max_wait_time: Maximum time to wait for result (default 5 minutes)

        Returns:
            Dictionary with trade result or None if timeout/error
        """
        start_time = time.time()

        if self.enable_logging:
            logger.info(
                f"🔍 Starting check_win for order {order_id}, max wait: {max_wait_time}s"
            )

        while time.time() - start_time < max_wait_time:
            # Check if order is in completed results
            if order_id in self._order_results:
                result = self._order_results[order_id]
                if self.enable_logging:
                    logger.success(
                        f" Order {order_id} completed - Status: {result.status.value}, Profit: ${result.profit:.2f}"
                    )

                return {
                    "result": "win"
                    if result.status == OrderStatus.WIN
                    else "loss"
                    if result.status == OrderStatus.LOSE
                    else "draw",
                    "profit": result.profit if result.profit is not None else 0,
                    "order_id": order_id,
                    "completed": True,
                    "status": result.status.value,
                }

            # Check if order is still active (not expired yet)
            if order_id in self._active_orders:
                active_order = self._active_orders[order_id]
                time_remaining = (
                    active_order.expires_at - datetime.now()
                ).total_seconds()

                if time_remaining <= 0:
                    if self.enable_logging:
                        logger.info(
                            f"⏰ Order {order_id} expired but no result yet, continuing to wait..."
                        )
                else:
                    if (
                        self.enable_logging and int(time.time() - start_time) % 10 == 0
                    ):  # Log every 10 seconds
                        logger.debug(
                            f"⌛ Order {order_id} still active, expires in {time_remaining:.0f}s"
                        )

            await asyncio.sleep(1.0)  # Check every second

        # Timeout reached
        if self.enable_logging:
            logger.warning(
                f"⏰ check_win timeout for order {order_id} after {max_wait_time}s"
            )

        return {
            "result": "timeout",
            "order_id": order_id,
            "completed": False,
            "timeout": True,
        }

    async def _request_candles(
        self, asset: str, timeframe: int, count: int, end_time: datetime
    ):
        """Request candle data from server using the correct changeSymbol format"""

        # Create message data in the format expected by PocketOption for real-time candles
        data = {
            "asset": str(asset),
            "period": timeframe,  # timeframe in seconds
        }

        # Create the full message using changeSymbol
        # message_data = ["changeSymbol", data] # This was for raw message sending
        # message = f"42{json.dumps(message_data)}" # This was for raw message sending

        if self.enable_logging:
            logger.debug(f"Requesting candles with changeSymbol: {data}")

        # Create a future to wait for the response
        candle_future = asyncio.Future()
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
        candles = []

        try:
            if isinstance(candles_data, list):
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
                            float(candle_data_item[5])
                            if len(candle_data_item) > 5
                            else 0.0
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
        if not isinstance(data, dict):
            logger.warning(f"Received non-dict JSON data: {data}")
            return

        # Check if this is candles data response from 'changeSymbol' or 'loadHistoryPeriod' that comes as general JSON
        # This part handles when the full candle history (not just stream updates) comes via a 'json_data' event,
        # likely due to the initial `changeSymbol` request or `loadHistoryPeriod`.
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
                        data["candles"], asset_from_data, period_from_data
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
            # Or if it's an Tupdate to an existing active order
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

        # Check if this is order result data with 'deals' (often seen for multiple order outcomes)
        elif "deals" in data and isinstance(data["deals"], list):
            for deal in data["deals"]:
                if isinstance(deal, dict) and "id" in deal:
                    order_id = str(deal["id"])

                    # Update or create the OrderResult
                    active_order = self._active_orders.get(order_id)

                    profit = float(deal.get("profit", 0))
                    status = OrderStatus.LOSE  # Default
                    if profit > 0:
                        status = OrderStatus.WIN
                    elif profit == 0:
                        status = OrderStatus.CLOSED  # Or DRAW if such enum exists

                    if active_order:
                        result = OrderResult(
                            order_id=active_order.order_id,
                            asset=active_order.asset,
                            amount=active_order.amount,
                            direction=active_order.direction,
                            duration=active_order.duration,
                            status=status,
                            placed_at=active_order.placed_at,
                            expires_at=active_order.expires_at,
                            profit=profit,
                            payout=deal.get("payout"),
                        )
                    else:  # If not previously tracked, try to infer details
                        result = OrderResult(
                            order_id=order_id,
                            asset=deal.get("asset", "UNKNOWN"),
                            amount=float(deal.get("amount", 0)),
                            # Direction might need to be inferred from a 'type' or 'action' in deal
                            direction=OrderDirection.CALL
                            if deal.get("type") == "up"
                            else OrderDirection.PUT,  # Example inference
                            duration=int(deal.get("time", 60)),
                            status=status,
                            placed_at=datetime.fromtimestamp(
                                deal.get("created_at", datetime.now().timestamp())
                            ),  # Example
                            expires_at=datetime.fromtimestamp(
                                deal.get("closed_at", datetime.now().timestamp())
                            ),  # Example
                            profit=profit,
                            payout=deal.get("payout"),
                        )

                    # Move from active to completed
                    self._order_results[order_id] = result
                    if order_id in self._active_orders:
                        del self._active_orders[order_id]

                    if self.enable_logging:
                        logger.success(
                            f"Order {order_id} completed via JSON 'deals': {status.value} - Profit: ${profit:.2f}"
                        )
                    # Use self._emit_event
                    await self._emit_event("order_closed", result)  #
        else:
            # Emit as general json_data if not a specific order/candles structure
            # Use self._emit_event
            await self._emit_event("json_data", data)  #

    async def _emit_event(
        self, event: str, data: Any
    ) -> None:  # Changed data type to Any
        """Emit event to registered callbacks"""
        if event in self._event_callbacks:
            for callback in self._event_callbacks[event]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(data)
                    else:
                        callback(data)
                except Exception as e:
                    if self.enable_logging:
                        logger.error(f"Error in event callback for {event}: {e}")

    # Event handlers
    async def _on_authenticated(self, data: Dict[str, Any]) -> None:
        """Handle authentication success"""
        if self.enable_logging:
            logger.success(" Successfully authenticated with PocketOption")
        # _connection_stats["successful_connections"] += 1 # This is updated in connect method already
        # Use self._emit_event
        await self._emit_event("authenticated", data)  #

    async def _on_balance_updated(self, data: Dict[str, Any]) -> None:
        """Handle balance update"""
        try:
            balance = Balance(
                balance=float(data.get("balance", 0)),
                currency=data.get("currency", "USD"),
                is_demo=self.is_demo,
            )
            self._balance = balance
            if self.enable_logging:
                logger.info(f"Balance updated: ${balance.balance:.2f}")
            # Use self._emit_event
            await self._emit_event("balance_updated", balance)  #
            self._balance_updated_event.set()  # Signal that balance is updated
        except Exception as e:
            if self.enable_logging:
                logger.error(f"Failed to parse balance data: {e}")

    async def _on_balance_data(self, data: Dict[str, Any]) -> None:
        """Handle balance data message"""
        # This is similar to balance_updated but for different message format
        await self._on_balance_updated(data)  # This will now also set the event

    async def _on_order_opened(self, data: Dict[str, Any]) -> None:
        """Handle order opened event"""
        if self.enable_logging:
            logger.info(f"Order opened: {data}")
        # Use self._emit_event
        await self._emit_event("order_opened", data)  #

    async def _on_order_closed(self, data: Dict[str, Any]) -> None:
        """Handle order closed event"""
        if self.enable_logging:
            logger.info(f"📊 Order closed: {data}")
        # Use self._emit_event
        await self._emit_event("order_closed", data)  #

    async def _on_stream_update(self, data: Dict[str, Any]) -> None:
        """Handle stream update event - includes real-time candle data"""
        if self.enable_logging:
            logger.debug(f"📡 Stream update: {data}")

        # Check if this is candle data from changeSymbol subscription
        if (
            "asset" in data
            and "period" in data
            and ("candles" in data or "data" in data)
        ):
            await self._handle_candles_stream(data)

        # Use self._emit_event
        await self._emit_event("stream_update", data)  #

    async def _on_candles_received(self, data: Dict[str, Any]) -> None:
        """Handle candles data received"""
        if self.enable_logging:
            logger.info(f"🕯️ Candles received with data: {type(data)}")
        # Check if we have pending candle requests
        # _candle_requests is initialized in __init__
        if self._candle_requests:  # Use `self._candle_requests` directly
            try:
                for request_id, future in list(self._candle_requests.items()):
                    if not future.done():
                        # The data here might be a dictionary with 'candles' key or just the list of candles directly
                        raw_candles_data = data.get("candles")
                        if raw_candles_data is None and isinstance(data, list):
                            raw_candles_data = (
                                data  # Handle case where data is just the list
                            )

                        if raw_candles_data:
                            parts = request_id.split("_")
                            if len(parts) >= 2:
                                asset = "_".join(parts[:-1])
                                timeframe = int(parts[-1])
                                candles = self._parse_candles_data(
                                    raw_candles_data, asset, timeframe
                                )
                                if self.enable_logging:
                                    logger.info(
                                        f"🕯️ Parsed {len(candles)} candles from response for {request_id}"
                                    )
                                future.set_result(candles)
                                if self.enable_logging:
                                    logger.debug(
                                        f"Resolved candle request: {request_id}"
                                    )
                                # Do not del future here, `_request_candles` finally block will handle it
                                # break # Break if one future is resolved to avoid processing same data for multiple futures
                            else:
                                logger.warning(
                                    f"Could not parse request_id: {request_id}"
                                )
                        else:
                            logger.warning(
                                f"No raw candles data found in _on_candles_received payload for {request_id}: {data}"
                            )

            except Exception as e:
                if self.enable_logging:
                    logger.error(f"Error processing candles data: {e}")
                for request_id, future in list(self._candle_requests.items()):
                    if not future.done():
                        future.set_result([])  # Resolve with empty list on error
                        break
        # Use self._emit_event
        await self._emit_event("candles_received", data)  #

    async def _on_disconnected(self, data: Dict[str, Any]) -> None:
        """Handle disconnection event"""
        if self.enable_logging:
            logger.warning("Disconnected from PocketOption")
        # Use self._emit_event
        await self._emit_event("disconnected", data)  #

    async def _handle_candles_stream(self, data: Dict[str, Any]) -> None:
        """Handle candle data from stream updates (changeSymbol responses)"""
        try:
            asset = data.get("asset")
            period = data.get("period")
            if not asset or not period:
                return
            request_id = f"{asset}_{period}"
            if self.enable_logging:
                logger.debug(f"Processing candle stream for {asset} ({period}s)")

            # _candle_requests is initialized in __init__
            if (
                request_id in self._candle_requests
            ):  # Use self._candle_requests directly
                future = self._candle_requests[request_id]
                if not future.done():
                    candles_list = data.get("data") or data.get(
                        "candles"
                    )  # Stream might use 'data' or 'candles'
                    if candles_list:
                        candles = self._parse_candles_data(candles_list, asset, period)
                        if candles:
                            future.set_result(candles)
                            if self.enable_logging:
                                logger.info(
                                    f"🕯️ Resolved candle request for {asset} with {len(candles)} candles from stream"
                                )
                # No need to delete future here, as `_request_candles` finally block will handle it after resolution.
        except Exception as e:
            if self.enable_logging:
                logger.error(f"Error handling candles stream: {e}")

    def _parse_stream_candles(
        self, stream_data: Dict[str, Any], asset: str, timeframe: int
    ):
        """Parse candles from stream update data (changeSymbol response)"""
        candles = []
        try:
            candle_data = stream_data.get("data") or stream_data.get("candles") or []
            if isinstance(candle_data, list):
                for item in candle_data:
                    if isinstance(item, dict):
                        candle = Candle(
                            timestamp=datetime.fromtimestamp(item.get("time", 0)),
                            open=float(item.get("open", 0)),
                            high=float(item.get("high", 0)),
                            low=float(item.get("low", 0)),
                            close=float(item.get("close", 0)),
                            volume=float(item.get("volume", 0)),
                            asset=asset,
                            timeframe=timeframe,
                        )
                        candles.append(candle)
                    elif isinstance(item, (list, tuple)) and len(item) >= 6:
                        # Adjusted indices based on common PocketOption stream format for candles:
                        # [timestamp, open, close, high, low, volume]
                        candle = Candle(
                            timestamp=datetime.fromtimestamp(item[0]),
                            open=float(item[1]),
                            high=float(item[3]),  # High is at index 3
                            low=float(item[4]),  # Low is at index 4
                            close=float(item[2]),  # Close is at index 2
                            volume=float(item[5]) if len(item) > 5 else 0.0,
                            asset=asset,
                            timeframe=timeframe,
                        )
                        candles.append(candle)
            candles.sort(key=lambda x: x.timestamp)
        except Exception as e:
            if self.enable_logging:
                logger.error(f"Error parsing stream candles: {e}")
        return candles

    async def _on_keep_alive_connected(
        self, data: Dict[str, Any]
    ) -> None:  # Added data parameter
        """
        Handle event when keep-alive connection is established.
        This method is now triggered by the internal websocket client's 'connected' event.
        """
        logger.info(
            f"Keep-alive connection established: {data.get('url')}"
        )  # Use data for logging

        # Initialize data after connection
        await self._initialize_data()

        # Emit event
        for callback in self._event_callbacks.get("connected", []):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(data)  # Pass data to the higher-level callback
                else:
                    callback(data)
            except Exception as e:
                logger.error(f"Error in connected callback: {e}")

    async def _on_keep_alive_reconnected(
        self, data: Dict[str, Any]
    ) -> None:  # Added data parameter
        """
        Handle event when keep-alive connection is re-established.
        This method is now triggered by the internal websocket client's 'reconnected' event.
        """
        logger.info(
            f"Keep-alive connection re-established: {data.get('url')}"
        )  # Use data for logging

        # Re-initialize data
        await self._initialize_data()

        # Emit event
        for callback in self._event_callbacks.get("reconnected", []):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(data)  # Pass data to the higher-level callback
                else:
                    callback(data)
            except Exception as e:
                logger.error(f"Error in reconnected callback: {e}")

    async def _on_keep_alive_message(self, message):
        """
        Handle messages received via keep-alive connection.
        This method is now largely superseded by `AsyncWebSocketClient`'s
        internal message parsing and event dispatching (`_on_sio_json`, etc.).
        This method might still be called for raw messages if the underlying
        Socket.IO client forwards them as 'message' events.
        """
        # This logic is mostly moved into AsyncWebSocketClient's _on_sio_json etc.
        # This method in AsyncPocketOptionClient should only handle events
        # explicitly routed from AsyncWebSocketClient's generic `message_received` handler
        # if any. The original implementation parsed raw '42' messages here which is
        # now handled by `python-socketio` and then passed via `_on_json_data`.

        # Emit raw message event (if this client needs to see it)
        await self._emit_event(
            "message", {"message": message}
        )  # Use await for _emit_event

    async def _attempt_reconnection(self, max_attempts: int = 3) -> bool:
        """
        Attempt to reconnect to PocketOption

        Args:
            max_attempts: Maximum number of reconnection attempts

        Returns:
            bool: True if reconnection was successful
        """
        logger.info(f"Attempting reconnection (max {max_attempts} attempts)...")

        for attempt in range(max_attempts):
            try:
                logger.info(f"Reconnection attempt {attempt + 1}/{max_attempts}")

                # Disconnect first to clean up
                # The underlying Socket.IO client has its own reconnect logic,
                # calling disconnect here will tell it to stop any active connections.
                await self._websocket.disconnect()

                # Wait a bit before reconnecting
                await asyncio.sleep(2 + attempt)  # Progressive delay

                # Attempt to reconnect using the main connection logic
                urls_to_try = []
                if self.preferred_region:
                    url = REGIONS.get_region(self.preferred_region)
                    if url:
                        urls_to_try.append(url)
                if not urls_to_try:
                    if self.is_demo:
                        urls_to_try = REGIONS.get_demo_regions()
                    else:
                        urls_to_try = REGIONS.get_all(randomize=True)
                # Filter None values
                urls_to_try = [url for url in urls_to_try if url is not None]  #

                success = await self._websocket.connect(urls_to_try, self._auth_data)

                if success:
                    logger.info(f" Reconnection successful on attempt {attempt + 1}")

                    # Trigger reconnected event (this will trigger _on_websocket_reconnected)
                    # The _on_websocket_reconnected will handle re-auth and re-init
                    return True
                else:
                    logger.warning(f"Reconnection attempt {attempt + 1} failed")

            except Exception as e:
                logger.error(
                    f"Reconnection attempt {attempt + 1} failed with error: {e}"
                )

        logger.error(f"All {max_attempts} reconnection attempts failed")
        return False
