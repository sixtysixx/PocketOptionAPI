import asyncio
from typing import Optional, Callable, Dict, Any, List, Deque
from datetime import datetime, timedelta
from collections import deque
import time
import socketio
from socketio.exceptions import ConnectionError
from loguru import logger
from .models import ConnectionInfo, ConnectionStatus, ServerTime
from .constants import DEFAULT_HEADERS
from .exceptions import WebSocketError


class AsyncWebSocketClient:
    """
    Professional async WebSocket client for PocketOption using python-socketio.
    This client manages Socket.IO connections, including authentication,
    automatic reconnection, message queuing, health monitoring, and routing of messages to event handlers.
    Features robust error handling, connection pooling, rate limiting, and comprehensive monitoring.
    """

    def __init__(
        self,
        max_message_queue_size: int = 1000,
        rate_limit_per_second: int = 50,
        heartbeat_interval: float = 30.0,
        connection_timeout: float = 10.0,
        max_connection_pool_size: int = 5,
    ):
        """
        Initializes the AsyncWebSocketClient with enhanced features.

        Args:
            max_message_queue_size: Maximum number of messages to queue during disconnection
            rate_limit_per_second: Maximum messages per second to prevent flooding
            heartbeat_interval: Interval in seconds for heartbeat checks
            connection_timeout: Timeout for connection attempts in seconds
            max_connection_pool_size: Maximum number of concurrent connections in pool
        """
        # Enhanced Socket.IO client configuration
        self.sio: socketio.AsyncClient = socketio.AsyncClient(
            logger=False,  # Disable built-in socketio logging, as Loguru is used globally
        )
        self.sio.on("connect", self._on_sio_connect)

        # Initialize event handlers
        self._event_handlers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}

    async def disconnect(self):
        """
        Gracefully disconnects from the Socket.IO server.
        Resets internal running flag and connection information.
        """
        logger.info("Disconnecting from Socket.IO client.")  # Log disconnection attempt
        self._running = False  # Set running flag to False
        if self.sio.connected:  # Check if the client is currently connected
            await self.sio.disconnect()  # Initiate Socket.IO disconnection
        self.connection_info = None  # Clear connection information upon disconnection

    # --- Public Event Handler Management ---
    def add_event_handler(self, event: str, handler: Callable) -> None:
        """
        Registers an event handler for a specific custom event type.
        Args:
            event: The name of the custom event (e.g., 'connected', 'json_data').
            handler: The callable function to be invoked when the event occurs.
        """
        if event not in self._event_handlers:
            self._event_handlers[event] = []
        self._event_handlers[event].append(handler)

    async def _on_sio_connect(self):
        engineio_logger = (False,)  # Disable built-in engineio logging
        reconnection = (True,)  # Enable built-in automatic reconnection
        reconnection_attempts = (float("inf"),)  # Infinite reconnection attempts
        reconnection_delay = (
            1,
        )  # Set initial delay (in seconds) before the first reconnection attempt
        reconnection_delay_max = (
            60,
        )  # Set maximum delay (in seconds) between reconnection attempts
        randomization_factor = (
            0.5,
        )  # Add jitter to reconnection delay to prevent thundering herd
        request_timeout = (
            connection_timeout,
        )  # Set timeout (in seconds) for initial connection handshake
        engineio_options = ({"ping_timeout": 30.0, "ping_interval": 25.0},)

        # Core connection management
        self.connection_info: Optional[ConnectionInfo] = None
        self.server_time: Optional[ServerTime] = None
        self._running = False
        self._current_url: Optional[str] = None

        # Enhanced event handling system

        # Connection monitoring and health tracking
        self._last_ping_time: Optional[datetime] = None
        self._last_pong_time: Optional[datetime] = None
        self._connection_start_time: Optional[datetime] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._connection_health_check_task: Optional[asyncio.Task] = None

        # Message queuing for offline periods
        self._message_queue: Deque[Dict[str, Any]] = deque(
            maxlen=max_message_queue_size
        )
        self._queue_processor_task: Optional[asyncio.Task] = None

        # Rate limiting
        self._rate_limiter = RateLimiter(rate_limit_per_second)
        self._outgoing_message_times: Deque[float] = deque()

        # Connection pooling and management
        self._connection_pool_size = max_connection_pool_size
        self._active_connections: List[str] = []
        self._connection_semaphore = asyncio.Semaphore(max_connection_pool_size)

        # Enhanced reconnection tracking
        self._reconnect_attempts_counter = 0
        self._consecutive_failures = 0
        self._last_successful_connection: Optional[datetime] = None

        # Performance metrics
        self._messages_sent = 0
        self._messages_received = 0
        self._connection_duration = 0.0
        self._average_latency = 0.0
        self._latency_measurements: Deque[float] = deque(maxlen=100)

        # Internal synchronization
        self._connection_lock = asyncio.Lock()
        self._message_lock = asyncio.Lock()
        self._cleanup_event = asyncio.Event()

        # Internal event handlers for standard Socket.IO events
        self.sio.on("connect", self._on_sio_connect)
        self.sio.on("disconnect", self._on_sio_disconnect)
        self.sio.on("reconnect", self._on_sio_reconnect)
        self.sio.on("connect_error", self._on_sio_connect_error)

        # Enhanced ping/pong handling
        self.sio.on("pong", self._on_sio_pong)

        # Catch-all handlers for other Socket.IO messages
        self.sio.on("message", self._on_sio_message)
        self.sio.on("json", self._on_sio_json)

        logger.info(
            f"AsyncWebSocketClient initialized with enhanced features: "
            f"rate_limit={rate_limit_per_second}/sec, "
            f"heartbeat_interval={heartbeat_interval}s, "
            f"max_queue_size={max_message_queue_size}"
        )

    async def acquire(self) -> bool:
        """Acquire a token, waiting if necessary."""
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_update
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_update = now

            if self.tokens >= 1:
                self.tokens -= 1
                return True

            wait_time = (1 - self.tokens) / self.rate
            await asyncio.sleep(wait_time)
            self.tokens -= 1
            return True

    async def start(self):
        """Start health monitoring."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Connection health monitoring started")

    async def stop(self):
        """Stop health monitoring."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Connection health monitoring stopped")

    async def _monitor_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                await asyncio.sleep(self.check_interval)

                if not self.client.is_connected:
                    continue

                # Check if we haven't received a pong recently
                if (
                    self.client._last_pong_time
                    and datetime.now() - self.client._last_pong_time
                    > timedelta(seconds=60)
                ):
                    logger.warning(
                        "No pong received in 60 seconds, connection may be unhealthy"
                    )
                    await self.client._handle_unhealthy_connection()

                # Check connection duration and performance
                if self.client._connection_start_time:
                    duration = (
                        datetime.now() - self.client._connection_start_time
                    ).total_seconds()
                    if duration > 3600:  # 1 hour
                        logger.info(
                            "Connection has been active for over 1 hour, performance metrics: "
                            f"sent={self.client._messages_sent}, received={self.client._messages_received}, "
                            f"avg_latency={self.client._average_latency:.2f}ms"
                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in health monitoring: {e}")
                await asyncio.sleep(self.check_interval)

    async def close(self):
        """Enhanced cleanup with proper resource management."""
        logger.info("AsyncWebSocketClient closing - initiating graceful shutdown...")

        # Signal cleanup to all background tasks
        self._cleanup_event.set()
        self._running = False

        # Stop health monitoring
        if hasattr(self, "_health_monitor"):
            await self._health_monitor.stop()

        # Stop queue processor
        if self._queue_processor_task:
            self._queue_processor_task.cancel()
            try:
                await self._queue_processor_task
            except asyncio.CancelledError:
                pass

        # Stop heartbeat task
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # Disconnect from server
        if self.sio.connected:
            try:
                await self.sio.disconnect()
                logger.info("Socket.IO connection closed")
            except Exception as e:
                logger.error(f"Error during disconnect: {e}")

        # Clear message queue
        async with self._message_lock:
            self._message_queue.clear()

        # Log final statistics
        self._log_connection_statistics()

        logger.info("AsyncWebSocketClient closed successfully")

    async def connect(self, urls: List[str], auth_data: Dict[str, Any]) -> bool:
        """
        Connect to PocketOption Socket.IO server with fallback URLs.
        Uses python-socketio's built-in reconnection logic.
        Args:
            urls: A list of WebSocket URLs to try connecting to.
            auth_data: A dictionary containing authentication parameters (e.g., session, isDemo, uid, platform).
        Returns:
            bool: True if connected successfully to any URL, False otherwise.
        """
        self._running = True  # Set running flag to True
        self._reconnect_attempts_counter = (
            0  # Reset reconnection attempt counter for a new connection process
        )

        # Create a mutable copy and shuffle URLs to distribute connections/retries
        import random

        current_urls = list(urls)  # Create a copy of the URLs list
        random.shuffle(current_urls)  # Randomize the order of URLs

        # Enhanced connection retry logic with exponential backoff
        max_attempts_per_url = 3
        base_delay = 2.0

        for url in current_urls:  # Iterate through each URL in the shuffled list
            for attempt in range(max_attempts_per_url):
                try:
                    # python-socketio handles the '/socket.io/?EIO=4&transport=websocket' part,
                    # so we connect to the base URL.
                    base_url = url.split("/socket.io")[0]  # Extract the base URL

                    logger.info(
                        f"Attempting to connect to Socket.IO at {base_url} (attempt {attempt + 1}/{max_attempts_per_url})"
                    )  # Log the current connection attempt

                    # Add connection timeout and retry logic
                    connection_timeout = 10.0 + (
                        attempt * 5
                    )  # Increase timeout with attempts

                    # Connect to the Socket.IO server.
                    # The 'auth' parameter sends authentication data during the handshake.
                    await self.sio.connect(
                        base_url,
                        transports=[
                            "websocket"
                        ],  # Explicitly specify WebSocket transport
                        headers=DEFAULT_HEADERS,  # Use predefined default headers (e.g., User-Agent)
                        auth=auth_data,  # Pass authentication data for handshake
                        wait_timeout=connection_timeout,  # Add connection timeout
                    )

                    if (
                        self.sio.connected
                    ):  # Check if the Socket.IO client is successfully connected
                        self._current_url = (
                            base_url  # Store the base URL of the successful connection
                        )
                        region = self._extract_region_from_url(
                            url
                        )  # Extract region from the original URL
                        self.connection_info = ConnectionInfo(  # Update detailed connection information
                            url=url,
                            region=region,
                            status=ConnectionStatus.CONNECTED,
                            connected_at=datetime.now(),  # Record connection timestamp
                            reconnect_attempts=self._reconnect_attempts_counter,  # Store attempts
                        )
                        logger.success(
                            f"Successfully connected to {region} via Socket.IO (attempt {attempt + 1})"
                        )  # Log success
                        await self._emit_event(
                            "connected",
                            {"url": url, "region": region, "attempt": attempt + 1},
                        )  # Emit custom 'connected' event
                        return True  # Return True as connection is established

                except (
                    ConnectionError
                ) as e:  # Catch Socket.IO specific connection errors
                    logger.warning(
                        f"Socket.IO connection failed to {url} (attempt {attempt + 1}): {e}"
                    )  # Log the specific failure reason
                    if (
                        self.sio.connected
                    ):  # Ensure disconnection if a partial connection occurred
                        await self.sio.disconnect()

                except asyncio.TimeoutError:  # Catch asyncio timeouts during connection
                    logger.warning(
                        f"Connection attempt to {url} timed out (attempt {attempt + 1})."
                    )  # Log timeout
                    if (
                        self.sio.connected
                    ):  # Ensure disconnection if a partial connection occurred
                        await self.sio.disconnect()

                except Exception as e:  # Catch any other unexpected errors
                    logger.error(
                        f"Unexpected error during connection to {url} (attempt {attempt + 1}): {e}"
                    )  # Log the unexpected error
                    if (
                        self.sio.connected
                    ):  # Ensure disconnection if a partial connection occurred
                        await self.sio.disconnect()

                # Exponential backoff delay before next attempt
                if attempt < max_attempts_per_url - 1:
                    delay = base_delay * (2**attempt) + random.uniform(0.5, 2.0)
                    logger.info(
                        f"Waiting {delay:.1f}s before next connection attempt..."
                    )
                    await asyncio.sleep(delay)

                self._reconnect_attempts_counter += (
                    1  # Increment counter for each URL attempt
                )

        logger.error(
            f"Failed to connect to any Socket.IO endpoint after {max_attempts_per_url} attempts per URL."
        )  # Log overall failure
        return False  # Return False if no connection could be established after all attempts

    async def send_message(self, event_name: str, data: Any = None) -> bool:
        """
        Sends a message (event) over the Socket.IO connection.
        Args:
            event_name: The name of the Socket.IO event to emit.
            data: The data payload to send with the event (can be dict, list, str, int, etc.). Optional.
        Returns:
            bool: True if the message was successfully sent, False otherwise.
        Raises:
            WebSocketError: If the client is not connected or an error occurs during emission.
        """
        if not self.sio.connected:  # Check if the Socket.IO client is connected
            logger.warning(
                "Cannot send message: Socket.IO client is not connected."
            )  # Log warning
            raise WebSocketError(
                "Socket.IO client is not connected"
            )  # Raise an error if not connected

        try:
            # Emit event. python-socketio handles the framing (e.g., '42["event_name", data]')
            if data is not None:  # If data payload is provided
                await self.sio.emit(event_name, data)  # Emit the event with data
            else:  # If no data payload
                await self.sio.emit(event_name)  # Emit the event without data

            logger.debug(
                f"Emitted Socket.IO event: '{event_name}' with data: {data}"
            )  # Log the emitted event
            return True  # Return True on successful emission
        except Exception as e:  # Catch any exceptions during emission
            logger.error(
                f"Failed to emit Socket.IO event '{event_name}': {e}"
            )  # Log the error
            raise WebSocketError(
                f"Failed to send Socket.IO event: {e}"
            )  # Re-raise as WebSocketError

    # --- Internal Socket.IO Event Handlers ---
    async def _on_sio_connect(self):
        """
        Handler for Socket.IO 'connect' event.
        This event signifies that the underlying Engine.IO handshake is complete.
        Updates internal connection information.
        """
        logger.success("Socket.IO client connected!")  # Log successful connection
        if (
            not self.connection_info
        ):  # If connection_info hasn't been set yet (first connection)
            if self._current_url:  # If a current URL is available
                region = self._extract_region_from_url(
                    self._current_url
                )  # Extract region from URL
                self.connection_info = ConnectionInfo(  # Create new ConnectionInfo
                    url=self._current_url,
                    region=region,
                    status=ConnectionStatus.CONNECTED,
                    connected_at=datetime.now(),  # Record current time as connected_at
                    reconnect_attempts=self._reconnect_attempts_counter,  # Store reconnection attempts
                )
            else:  # Fallback if URL is not available
                logger.warning(
                    "Unable to determine connection URL"
                )  # Warn if URL is unknown
                self.connection_info = (
                    ConnectionInfo(  # Create ConnectionInfo with unknown details
                        url="unknown",
                        region="UNKNOWN",
                        status=ConnectionStatus.CONNECTED,
                        connected_at=datetime.now(),
                        reconnect_attempts=self._reconnect_attempts_counter,
                    )
                )
        else:  # On re-connection, update status
            self.connection_info = ConnectionInfo(  # Update existing ConnectionInfo
                url=self.connection_info.url,  # Keep original URL
                region=self.connection_info.region,
                status=ConnectionStatus.CONNECTED,
                connected_at=datetime.now(),  # Update connected_at on successful reconnect
                last_ping=self.connection_info.last_ping,  # Preserve last ping time
                reconnect_attempts=self.connection_info.reconnect_attempts + 1
                if self._running
                else 0,  # Increment attempts if running, else reset
            )

    async def _on_sio_disconnect(self):
        """
        Handler for Socket.IO 'disconnect' event.
        Updates connection status to DISCONNECTED.
        """
        logger.warning("Socket.IO client disconnected!")  # Log disconnection warning
        if self.connection_info:  # If connection info exists
            self.connection_info = ConnectionInfo(  # Update connection info status
                url=self.connection_info.url,
                region=self.connection_info.region,
                status=ConnectionStatus.DISCONNECTED,  # Set status to DISCONNECTED
                connected_at=self.connection_info.connected_at,
                last_ping=self.connection_info.last_ping,
                reconnect_attempts=self.connection_info.reconnect_attempts,
            )
        await self._emit_event("disconnected", {})  # Emit custom 'disconnected' event

    async def _on_sio_reconnect(self, attempt_count: int):
        """
        Handler for Socket.IO 'reconnect' event.
        Updates connection information after a successful internal reconnection.
        Args:
            attempt_count: The number of attempts taken by socketio to reconnect.
        """
        logger.info(
            f"Socket.IO client reconnected after {attempt_count} attempts!"
        )  # Log reconnection
        if self.connection_info:  # If connection info exists
            self.connection_info = ConnectionInfo(  # Update connection info
                url=self.connection_info.url,
                region=self.connection_info.region,
                status=ConnectionStatus.CONNECTED,  # Set status to CONNECTED
                connected_at=datetime.now(),  # Update connected_at on successful reconnect
                last_ping=self.connection_info.last_ping,  # Preserve last ping
                reconnect_attempts=attempt_count,  # Store actual attempts
            )
        await self._emit_event(  # Emit custom 'reconnected' event
            "reconnected",
            {
                "attempt": attempt_count,
                "url": self.connection_info.url if self.connection_info else None,
            },
        )

    async def _on_sio_connect_error(self, data: Any):
        """
        Handler for Socket.IO 'connect_error' event.
        Logs the error and emits a custom 'connect_error' event.
        Args:
            data: The error data from Socket.IO.
        """
        logger.error(f"Socket.IO connection error: {data}")  # Log the connection error
        await self._emit_event(
            "connect_error", {"message": str(data)}
        )  # Emit custom 'connect_error' event

    async def _on_sio_message(self, data: Any):
        """
        Catch-all handler for generic 'message' events (raw messages).
        Args:
            data: The raw message data received.
        """
        logger.debug(f"Socket.IO 'message' event received: {data}")  # Log raw message
        await self._emit_event(
            "message_received", {"message": data}
        )  # Emit custom 'message_received' event

    async def _on_sio_json(self, data: Any):
        """
        Handler for 'json' events. `python-socketio` typically parses '42' messages
        into this format. The data format here is usually `[event_name, data_payload]`.
        Args:
            data: The parsed JSON data received from Socket.IO.
        """
        logger.debug(
            f"Socket.IO 'json' event received: {data}"
        )  # Log the raw JSON data

        if (
            isinstance(data, list) and len(data) > 0
        ):  # Check if data is a list (common for S.IO events)
            event_type: str = data[0]  # First element is the event type
            event_data: Dict[str, Any] = (
                data[1] if len(data) > 1 else {}
            )  # Second element is the data payload, if present

            logger.info(
                f"Received Socket.IO event: {event_type} with data: {event_data}"
            )  # Log parsed event

            # Explicitly map common PocketOption events to custom events
            if event_type == "successauth":
                logger.success(
                    "Authentication successful - received 'successauth' event"
                )  # Log auth success
                await self._emit_event(
                    "authenticated", event_data
                )  # Emit custom 'authenticated' event
            elif event_type == "successupdateBalance":
                await self._emit_event(
                    "successupdateBalance", event_data
                )  # Emit custom 'successupdateBalance' event
            elif event_type == "successopenOrder":
                await self._emit_event(
                    "successopenOrder", event_data
                )  # Emit custom 'successopenOrder' event
            elif event_type == "successcloseOrder":
                await self._emit_event(
                    "successcloseOrder", event_data
                )  # Emit custom 'successcloseOrder' event
            elif event_type == "updateStream":
                await self._emit_event(
                    "updateStream", event_data
                )  # Emit custom 'updateStream' event
            elif event_type == "loadHistoryPeriod":
                await self._emit_event(
                    "candles_received", event_data
                )  # Emit custom 'candles_received' event
            elif event_type == "payout_update":
                await self._emit_event(
                    "payout_update", event_data
                )  # Emit custom 'payout_update' event
            elif event_type == "updateHistoryNew":
                await self._emit_event(
                    "history_update", event_data
                )  # Emit custom 'history_update' event
            elif event_type == "autherror":
                logger.error(
                    f"Authentication error received: {event_data}"
                )  # Log auth error
                await self._emit_event(
                    "auth_error", event_data
                )  # Emit custom 'auth_error' event
                # Enhanced auth error handling
                logger.error(f"Authentication failed with data: {event_data}")
                if isinstance(event_data, dict):
                    for key, value in event_data.items():
                        logger.error(f"  {key}: {value}")
            else:
                # Fallback for unrecognized Socket.IO events
                logger.debug(
                    f"Unrecognized event type: {event_type}"
                )  # Log unrecognized event
                await self._emit_event(  # Emit generic 'unknown_event'
                    "unknown_event", {"type": event_type, "data": event_data}
                )
        elif isinstance(
            data, dict
        ):  # Sometimes a raw JSON object might be sent without an event_type array
            await self._emit_event("json_data", data)  # Emit as general 'json_data'
        else:
            logger.warning(
                f"Unexpected data format in Socket.IO 'json' event: {data}"
            )  # Warn for unexpected formats

    def remove_event_handler(self, event: str, handler: Callable) -> None:
        """
        Removes a previously registered event handler.
        Args:
            event: The name of the event from which to remove the handler.
            handler: The callable function to remove.
        """
        if event in self._event_handlers:  # If handlers exist for this event
            try:
                self._event_handlers[event].remove(
                    handler
                )  # Attempt to remove the handler
            except ValueError:
                pass  # Handler not found in list, so do nothing

    async def _emit_event(self, event: str, data: Dict[str, Any]) -> None:
        """
        Emits an event to all registered handlers for that event type.
        Supports both synchronous and asynchronous handler functions.
        Args:
            event: The name of the event to emit.
            data: The data payload to pass to the event handlers.
        """
        if event in self._event_handlers:  # Check if there are handlers for this event
            for handler in self._event_handlers[
                event
            ]:  # Iterate through each registered handler
                try:
                    if asyncio.iscoroutinefunction(
                        handler
                    ):  # Check if the handler is an asynchronous function
                        await handler(data)  # Await the asynchronous handler
                    else:
                        handler(data)  # Call the synchronous handler directly
                except Exception as e:
                    logger.error(
                        f"Error in event handler for {event}: {e}"
                    )  # Log any exceptions in handlers

    # --- Utility Methods ---
    def _extract_region_from_url(self, url: str) -> str:
        """
        Extracts a region name heuristic from a given WebSocket URL.
        Args:
            url: The WebSocket URL.
        Returns:
            str: The extracted region name (e.g., "LIVE", "DEMO", "UNKNOWN").
        """
        try:
            parts = url.split("//")[1].split(".")[
                0
            ]  # Get the subdomain part of the URL
            if "api-" in parts:
                return parts.replace(
                    "api-", ""
                ).upper()  # Extract and uppercase the region name
            elif "demo" in parts:
                return "DEMO"
            elif "try-demo" in parts:
                return "DEMO_2"
            else:
                return "UNKNOWN"
        except Exception:
            return "UNKNOWN"

    @property
    def is_connected(self) -> bool:
        """
        Checks if the Socket.IO client is currently connected.
        Returns:
            bool: True if connected, False otherwise.
        """
        return self.sio.connected
