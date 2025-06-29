"""
Enhanced Keep-Alive Connection Manager for PocketOption Async API.
This module provides a robust, persistent WebSocket connection manager
that handles automatic reconnections, periodic pings, and intelligent
message processing, mimicking the behavior of a long-running client.
"""

import asyncio
import json  # Import json for parsing messages
import ssl  # Import ssl for WebSocket secure connections
from typing import Optional, List, Callable, Dict, Any
from datetime import datetime, timedelta
from loguru import logger
from websockets.exceptions import ConnectionClosed
from websockets.legacy.client import connect, WebSocketClientProtocol

from .models import ConnectionInfo, ConnectionStatus
from .constants import (
    REGIONS,
    DEFAULT_HEADERS,
)  # Import DEFAULT_HEADERS for random User-Agent


class ConnectionKeepAlive:
    """
    Advanced connection keep-alive manager based on old API patterns.
    This class manages a persistent WebSocket connection to PocketOption,
    including automatic reconnection, periodic ping-pong, and dispatching
    of various incoming message types to registered event handlers.
    """

    def __init__(self, ssid: str, is_demo: bool = True):
        """
        Initializes the ConnectionKeepAlive manager.

        Args:
            ssid: The complete SSID string for authentication (expected to be in '42["auth",...]' format).
            is_demo: A boolean indicating whether to connect to demo or live servers.
        """
        self.ssid = ssid
        self.is_demo = is_demo

        # Connection state variables
        self.websocket: Optional[WebSocketClientProtocol] = None
        self.connection_info: Optional[ConnectionInfo] = None
        self.is_connected = False
        self.should_reconnect = True  # Flag to control reconnection attempts

        # Background asyncio tasks
        self._ping_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._message_task: Optional[asyncio.Task] = None
        self._health_task: Optional[asyncio.Task] = None

        # Keep-alive and reconnection settings
        self.ping_interval = 20  # seconds: Interval for sending keep-alive pings
        self.reconnect_delay = (
            5  # seconds: Initial delay before attempting reconnection
        )
        self.max_reconnect_attempts = 10  # Maximum number of reconnection tries
        self.current_reconnect_attempts = 0  # Counter for current reconnection attempts

        # Dictionary to store registered event handlers
        self._event_handlers: Dict[str, List[Callable]] = {}

        # Determine available WebSocket URLs based on demo mode
        self.available_urls = (
            REGIONS.get_demo_regions() if is_demo else REGIONS.get_all()
        )
        self.current_url_index = (
            0  # Index to track the current URL being used from available_urls
        )

        # Connection statistics for monitoring
        self.connection_stats = {
            "total_connections": 0,
            "successful_connections": 0,
            "total_reconnects": 0,
            "last_ping_time": None,
            "last_pong_time": None,
            "total_messages_sent": 0,
            "total_messages_received": 0,
        }

        logger.info(
            f"Initialized keep-alive manager with {len(self.available_urls)} available regions"
        )

    async def start_persistent_connection(self) -> bool:
        """
        Starts a persistent WebSocket connection with automatic keep-alive and reconnection.
        This method attempts to establish an initial connection and then kicks off
        all necessary background tasks to maintain it.

        Returns:
            bool: True if the initial connection and background tasks are successfully started, False otherwise.
        """
        logger.info("Starting persistent connection with keep-alive...")

        try:
            # Attempt to establish the initial WebSocket connection.
            if await self._establish_connection():
                # If connection is successful, start all monitoring and communication tasks.
                await self._start_background_tasks()
                logger.success(
                    "Success: Persistent connection established with keep-alive active"
                )
                return True
            else:
                logger.error("Error: Failed to establish initial connection")
                return False

        except Exception as e:
            logger.error(f"Error: Error starting persistent connection: {e}")
            return False

    async def stop_persistent_connection(self):
        """
        Stops the persistent connection and gracefully cancels all running background tasks.
        Sets a flag to prevent further reconnection attempts and closes the WebSocket.
        """
        logger.info("Stopping persistent connection...")

        self.should_reconnect = False  # Signal background tasks to stop

        # Cancel all associated asyncio tasks.
        tasks = [
            self._ping_task,
            self._reconnect_task,
            self._message_task,
            self._health_task,
        ]
        for task in tasks:
            if task and not task.done():
                task.cancel()  # Request task cancellation
                try:
                    await task  # Await task to ensure it cleans up properly
                except asyncio.CancelledError:
                    pass  # Expected exception when cancelling

        # Close the WebSocket connection if it's open.
        if self.websocket:
            await self.websocket.close()
            self.websocket = None

        self.is_connected = False  # Update connection state
        logger.info("Success: Persistent connection stopped")

    async def _establish_connection(self) -> bool:
        """
        Attempts to establish a WebSocket connection to one of the available URLs.
        It iterates through the `available_urls` list, trying each one until a connection is successful.
        Includes SSL context setup and an initial handshake.

        Returns:
            bool: True if a connection is successfully established and handshake completed, False otherwise.
        """
        for attempt in range(len(self.available_urls)):
            url = self.available_urls[self.current_url_index]

            try:
                logger.info(
                    f"Connecting: Attempting connection to {url} (attempt {attempt + 1})"
                )

                # Configure SSL context for secure WebSocket connections.
                # `PROTOCOL_TLS_CLIENT` is used for client-side connections.
                # `check_hostname = False` and `verify_mode = ssl.CERT_NONE` are used for broader
                # compatibility, but can be made stricter in production environments if needed.
                ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

                # Establish the WebSocket connection with a timeout.
                # `extra_headers` are taken from `DEFAULT_HEADERS` which includes a random User-Agent.
                # `ping_interval` and `ping_timeout` are set to `None` because pings are handled manually
                # by the `_ping_loop` for more control over the specific PocketOption ping message.
                self.websocket = await asyncio.wait_for(
                    connect(
                        url,
                        ssl=ssl_context,
                        extra_headers=DEFAULT_HEADERS,  # Use DEFAULT_HEADERS for User-Agent etc.
                        ping_interval=None,  # Manual ping handling
                        ping_timeout=None,  # Manual ping handling
                        close_timeout=10,
                    ),
                    timeout=15.0,  # Timeout for the initial connection attempt
                )

                # Update connection information upon successful connection.
                region = self._extract_region_from_url(url)
                self.connection_info = ConnectionInfo(
                    url=url,
                    region=region,
                    status=ConnectionStatus.CONNECTED,
                    connected_at=datetime.now(),
                    reconnect_attempts=self.current_reconnect_attempts,
                )

                self.is_connected = True  # Mark connection as active
                self.current_reconnect_attempts = (
                    0  # Reset reconnect counter on success
                )
                self.connection_stats["total_connections"] += 1
                self.connection_stats["successful_connections"] += 1

                # Perform the initial handshake sequence required by PocketOption.
                await self._send_handshake()

                logger.success(f"Success: Connected to {region} region successfully")
                await self._emit_event("connected", {"url": url, "region": region})

                return True  # Connection successful

            except Exception as e:
                logger.warning(f"Caution: Failed to connect to {url}: {e}")

                # Move to the next URL in the list for the next attempt.
                self.current_url_index = (self.current_url_index + 1) % len(
                    self.available_urls
                )

                # Ensure the WebSocket is closed if an error occurred during connection.
                if self.websocket:
                    try:
                        await self.websocket.close()
                    except Exception:
                        pass  # Ignore errors during close
                    self.websocket = None

                await asyncio.sleep(1)  # Brief delay before trying the next URL

        return False  # All URLs failed to connect

    async def _send_handshake(self):
        """
        Performs the initial handshake sequence with the PocketOption server.
        This involves sending and receiving specific messages to establish the session.
        The `self.ssid` is expected to be in the `42["auth",...]` format.

        Raises:
            RuntimeError: If the WebSocket is not connected when this method is called.
            Exception: If any part of the handshake process fails or times out.
        """
        try:
            if not self.websocket:
                raise RuntimeError("Handshake called with no websocket connection.")

            # Step 1: Wait for the initial "0" message from the server.
            # This message typically contains a session ID (sid) from the server.
            initial_message = await asyncio.wait_for(
                self.websocket.recv(), timeout=10.0
            )
            logger.debug(f"Received initial handshake message: {initial_message}")

            # Step 2: Send the "40" response. This is a Socket.IO specific acknowledgment.
            await self.websocket.send("40")
            await asyncio.sleep(0.1)  # Small delay to allow server to process

            # Step 3: Wait for the connection establishment message ("40" with "sid").
            # This confirms the Socket.IO connection is ready.
            conn_message = await asyncio.wait_for(self.websocket.recv(), timeout=10.0)
            logger.debug(f"Received connection confirmation: {conn_message}")

            # Step 4: Send the SSID for authentication.
            # `self.ssid` is already expected to be formatted as '42["auth", {...}]'.
            await self.websocket.send(self.ssid)
            logger.debug("Authentication SSID sent. Handshake completed.")

            # Update message sent count
            self.connection_stats["total_messages_sent"] += 2  # For "40" and SSID

        except Exception as e:
            logger.error(f"Handshake failed: {e}")
            raise  # Re-raise the exception to indicate handshake failure

    async def _start_background_tasks(self):
        """
        Starts all necessary background asyncio tasks to maintain the persistent connection.
        This includes tasks for sending pings, receiving messages, monitoring health,
        and managing reconnections.
        """
        logger.info("Persistent: Starting background keep-alive tasks...")

        # Create and start the periodic ping task.
        self._ping_task = asyncio.create_task(self._ping_loop())

        # Create and start the message receiving task.
        self._message_task = asyncio.create_task(self._message_loop())

        # Create and start the connection health monitoring task.
        self._health_task = asyncio.create_task(self._health_monitor_loop())

        # Create and start the reconnection monitoring task.
        self._reconnect_task = asyncio.create_task(self._reconnection_monitor())

        logger.success("Success: All background tasks started")

    async def _ping_loop(self):
        """
        Continuously sends a specific ping message ('42["ps"]') to the server
        at regular intervals (`ping_interval`) to keep the WebSocket connection alive.
        """
        logger.info("Ping: Starting ping loop...")

        while self.should_reconnect:  # Loop as long as reconnection is desired
            try:
                if self.is_connected and self.websocket:
                    # Send the PocketOption specific ping message.
                    await self.websocket.send('42["ps"]')
                    self.connection_stats["last_ping_time"] = datetime.now()
                    self.connection_stats["total_messages_sent"] += 1

                    logger.debug("Ping: Ping sent")

                await asyncio.sleep(
                    self.ping_interval
                )  # Wait for the next ping interval

            except ConnectionClosed:
                logger.warning(
                    "Connecting: Connection closed during ping loop, stopping ping."
                )
                self.is_connected = False  # Mark as disconnected
                break  # Exit loop
            except Exception as e:
                logger.error(f"Error: Ping failed unexpectedly: {e}, stopping ping.")
                self.is_connected = False  # Mark as disconnected
                break  # Exit loop

    async def _message_loop(self):
        """
        Continuously receives and processes messages from the WebSocket.
        This loop runs as a background task, listening for incoming data
        and dispatching it to the `_process_message` method.
        """
        logger.info("Message: Starting message loop...")

        while self.should_reconnect:  # Loop as long as reconnection is desired
            try:
                if self.is_connected and self.websocket:
                    try:
                        # Receive message with a timeout to prevent indefinite blocking.
                        message = await asyncio.wait_for(
                            self.websocket.recv(), timeout=30.0
                        )

                        self.connection_stats["total_messages_received"] += 1
                        await self._process_message(
                            message
                        )  # Process the received message

                    except asyncio.TimeoutError:
                        logger.debug(
                            "Message: Message receive timeout (normal, no message within 30s)."
                        )
                        continue  # Continue listening
                else:
                    await asyncio.sleep(1)  # Wait if not connected before re-checking

            except ConnectionClosed:
                logger.warning(
                    "Connecting: Connection closed during message receive loop, stopping."
                )
                self.is_connected = False  # Mark as disconnected
                break  # Exit loop
            except Exception as e:
                logger.error(
                    f"Error: Message loop encountered an error: {e}, stopping."
                )
                self.is_connected = False  # Mark as disconnected
                break  # Exit loop

    async def _health_monitor_loop(self):
        """
        Monitors the health of the WebSocket connection by checking for recent pongs
        and the WebSocket's internal state. If issues are detected, it marks the
        connection as disconnected to trigger reconnection logic.
        """
        logger.info("Health: Starting health monitor...")

        while self.should_reconnect:  # Loop as long as reconnection is desired
            try:
                await asyncio.sleep(30)  # Check health every 30 seconds

                if not self.is_connected:
                    logger.warning(
                        "Health: Health check: Connection already marked as lost."
                    )
                    continue  # Skip checks if already disconnected

                # Check if a ping response (pong) has been received recently.
                # If the last ping was sent more than 60 seconds ago and no pong was received,
                # it suggests the connection might be dead.
                if self.connection_stats["last_ping_time"]:
                    time_since_last_ping_sent = (
                        datetime.now() - self.connection_stats["last_ping_time"]
                    )
                    if time_since_last_ping_sent > timedelta(seconds=60):
                        logger.warning(
                            "Health: Health check: No pong response for over 60 seconds, connection may be dead."
                        )
                        self.is_connected = False  # Trigger reconnection

                # Directly check the WebSocket's internal state.
                if self.websocket and self.websocket.closed:
                    logger.warning(
                        "Health: Health check: WebSocket is internally closed."
                    )
                    self.is_connected = False  # Trigger reconnection

            except Exception as e:
                logger.error(f"Error: Health monitor loop encountered an error: {e}")

    async def _reconnection_monitor(self):
        """
        Monitors the connection status and automatically attempts to reconnect
        if a disconnection is detected, up to `max_reconnect_attempts`.
        """
        logger.info("Persistent: Starting reconnection monitor...")

        while self.should_reconnect:  # Loop as long as reconnection is desired
            try:
                await asyncio.sleep(5)  # Check connection status every 5 seconds

                if not self.is_connected and self.should_reconnect:
                    logger.warning(
                        "Persistent: Detected disconnection, attempting reconnect..."
                    )

                    self.current_reconnect_attempts += 1  # Increment attempt counter

                    # Check if maximum reconnection attempts have been reached.
                    if self.current_reconnect_attempts <= self.max_reconnect_attempts:
                        logger.info(
                            f"Persistent: Reconnection attempt {self.current_reconnect_attempts}/{self.max_reconnect_attempts}"
                        )

                        # Clean up any existing broken WebSocket connection before reconnecting.
                        if self.websocket:
                            try:
                                await self.websocket.close()
                            except Exception:
                                pass  # Ignore errors during close
                            self.websocket = None

                        # Attempt to re-establish the connection.
                        success = await self._establish_connection()

                        if success:
                            logger.success("Success: Reconnection successful!")
                            await self._emit_event(
                                "reconnected",
                                {
                                    "attempt": self.current_reconnect_attempts,
                                    "url": self.connection_info.url
                                    if self.connection_info
                                    else None,
                                },
                            )
                        else:
                            logger.error(
                                f"Error: Reconnection attempt {self.current_reconnect_attempts} failed."
                            )
                            await asyncio.sleep(
                                self.reconnect_delay
                            )  # Wait before next attempt
                    else:
                        logger.error(
                            f"Error: Max reconnection attempts ({self.max_reconnect_attempts}) reached. Stopping reconnection."
                        )
                        await self._emit_event(
                            "max_reconnects_reached",
                            {"attempts": self.current_reconnect_attempts},
                        )
                        self.should_reconnect = False  # Stop trying to reconnect
                        break  # Exit loop

            except Exception as e:
                logger.error(
                    f"Error: Reconnection monitor loop encountered an error: {e}"
                )

    async def _process_message(self, message):
        """
        Processes incoming WebSocket messages from the PocketOption server.
        This method decodes messages (bytes or string) and dispatches them
        to appropriate event handlers based on their content and format.

        Args:
            message: The raw message received from the WebSocket (can be bytes or str).
        """
        try:
            # Handle raw bytes messages first. These often contain direct JSON objects
            # for balance updates or specific order data in older API patterns.
            if isinstance(message, bytes):
                decoded_message = message.decode("utf-8")
                try:
                    json_data = json.loads(decoded_message)
                    logger.debug(f"Message: Received JSON bytes: {json_data}")

                    # Specific handling for balance data (e.g., {"balance": ..., "currency": ..., "isDemo": ...})
                    if "balance" in json_data and isinstance(
                        json_data.get("balance"), (int, float)
                    ):
                        balance_data = {
                            "balance": float(json_data["balance"]),
                            "currency": json_data.get("currency", "USD"),
                            "is_demo": bool(json_data.get("isDemo", 1)),
                        }
                        if "uid" in json_data:
                            balance_data["uid"] = json_data["uid"]
                        await self._emit_event("balance_data", balance_data)
                        await self._emit_event(
                            "balance_updated", balance_data
                        )  # Emit both for compatibility
                    # Specific handling for order data from bytes (e.g., {"requestId": "buy", ...})
                    elif "requestId" in json_data and json_data["requestId"] == "buy":
                        await self._emit_event("order_data", json_data)
                    else:
                        # Emit as general JSON data if not specifically recognized
                        await self._emit_event("json_data", json_data)
                except json.JSONDecodeError:
                    logger.debug(
                        f"Message: Received non-JSON bytes: {decoded_message[:100]}..."
                    )
                    await self._emit_event(
                        "raw_bytes_message", {"message": decoded_message}
                    )  # Emit raw bytes
                return  # Processing for bytes message is complete

            # If not bytes, assume message is a string.
            message_str = message

            logger.debug(f"Message: Received string: {message_str[:100]}...")

            # Handle "2" (ping) message from the server, respond with "3" (pong).
            if message_str == "2":
                if self.websocket:
                    await self.websocket.send("3")
                    self.connection_stats["last_pong_time"] = datetime.now()
                    logger.debug("Ping: Pong sent in response to '2'.")
                return

            # Handle messages prefixed with '42'. This is the new primary format for application-level data.
            # Examples: '42["successauth", {...}]', '42["balance_updated", {...}]', '42["stream_update", {...}]'
            if message_str.startswith("42"):
                await self._parse_42_message(message_str)
                return

            # Handle messages prefixed with '451-'. This is an older structured JSON message format.
            # Example: '451-[["updateStream", {...}]]'
            if message_str.startswith("451-["):
                try:
                    # Extract the JSON part after "451-" prefix.
                    json_part = message_str.split("-", 1)[1]
                    # Parse the JSON string, which is expected to be a list of lists (events).
                    events_list = json.loads(json_part)
                    if isinstance(events_list, list):
                        for event_item in events_list:
                            if isinstance(event_item, list) and len(event_item) > 0:
                                event_type = event_item[0]
                                event_data = (
                                    event_item[1] if len(event_item) > 1 else {}
                                )
                                # Dispatch specific events based on event_type.
                                if event_type == "updateStream":
                                    await self._emit_event("stream_update", event_data)
                                elif event_type == "loadHistoryPeriod":
                                    await self._emit_event(
                                        "candles_received", event_data
                                    )
                                else:
                                    # Emit as general JSON data if not specifically recognized.
                                    await self._emit_event(
                                        "json_data",
                                        {"type": event_type, "data": event_data},
                                    )
                            else:
                                logger.warning(
                                    f"Unexpected item format in 451- list: {event_item}"
                                )
                    else:
                        logger.warning(
                            f"451- message content is not a list of events: {events_list}"
                        )
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"Failed to decode JSON from 451- prefixed message '{message_str[:100]}...': {e}"
                    )
                except Exception as e:
                    logger.error(
                        f"Error handling 451- prefixed message '{message_str[:100]}...': {e}"
                    )
                return  # Processing for 451- message is complete

            # Fallback: If no specific handler matches, emit as a generic message_received event.
            await self._emit_event("message_received", {"message": message_str})

        except Exception as e:
            logger.error(
                f"Error: An unexpected error occurred while processing message: {e}"
            )

    async def _parse_42_message(self, message: str):
        """
        Parses and handles messages prefixed with '42'.
        These messages typically contain an event type and associated data in a JSON array format.

        Args:
            message: The string message starting with '42'.
        """
        try:
            json_payload_str = message[2:]  # Remove the "42" prefix
            data = json.loads(json_payload_str)

            # Expected format is a list: `["event_type", {event_data}]`
            if isinstance(data, list) and len(data) > 0:
                event_type = data[0]
                event_data = data[1] if len(data) > 1 else {}

                # Dispatch events based on the event type.
                if event_type == "successauth":
                    logger.success(
                        "Success: Authentication successful via '42' message."
                    )
                    await self._emit_event("authenticated", event_data)
                elif event_type == "balance_data":
                    await self._emit_event("balance_data", event_data)
                elif event_type == "balance_updated":
                    await self._emit_event("balance_updated", event_data)
                elif event_type == "order_opened":
                    await self._emit_event("order_opened", event_data)
                elif event_type == "order_closed":
                    await self._emit_event("order_closed", event_data)
                elif event_type == "stream_update":
                    await self._emit_event("stream_update", event_data)
                elif (
                    event_type == "loadHistoryPeriod"
                ):  # Often seen as `candles_received`
                    await self._emit_event("candles_received", event_data)
                elif event_type == "payout_update":  # Example for a new event type
                    await self._emit_event("payout_update", event_data)
                else:
                    # Emit a general 'json_data' event for other unrecognized '42'-prefixed JSON messages.
                    await self._emit_event(
                        "json_data", {"type": event_type, "data": event_data}
                    )
            # Handle cases where '42' is followed by a direct JSON object (less common but possible).
            elif isinstance(data, dict):
                await self._emit_event("json_data", data)
            else:
                logger.warning(
                    f"Unexpected '42' message content type: {type(data).__name__} for message: {message[:100]}..."
                )

        except json.JSONDecodeError as e:
            logger.warning(
                f"Failed to decode JSON from '42' prefixed message '{message[:100]}...': {e}"
            )
        except Exception as e:
            logger.error(
                f"Error handling '42' prefixed message '{message[:100]}...': {e}"
            )

    async def send_message(self, message: str) -> bool:
        """
        Sends a message over the WebSocket connection.
        Includes a check to ensure the connection is active before sending.

        Args:
            message: The string message to send.

        Returns:
            bool: True if the message was successfully sent, False otherwise.
        """
        try:
            if self.is_connected and self.websocket:
                await self.websocket.send(message)
                self.connection_stats["total_messages_sent"] += 1
                logger.debug(f"Message: Sent: {message[:50]}...")
                return True
            else:
                logger.warning("Caution: Cannot send message: not connected.")
                return False
        except Exception as e:
            logger.error(f"Error: Failed to send message: {e}")
            self.is_connected = False  # Mark as disconnected on send failure
            return False

    def add_event_handler(self, event: str, handler: Callable):
        """
        Registers an event handler for a specific event type.

        Args:
            event: The name of the event (e.g., 'connected', 'authenticated', 'message_received').
            handler: The callable function to be executed when the event occurs.
        """
        if event not in self._event_handlers:
            self._event_handlers[event] = []
        self._event_handlers[event].append(handler)

    async def _emit_event(self, event: str, data: Any):
        """
        Emits an event to all registered handlers for that event type.
        Supports both synchronous and asynchronous handler functions.

        Args:
            event: The name of the event to emit.
            data: The data associated with the event.
        """
        if event in self._event_handlers:
            for handler in self._event_handlers[event]:
                try:
                    # Check if the handler is an asynchronous function and await it if so.
                    if asyncio.iscoroutinefunction(handler):
                        await handler(data)
                    else:
                        handler(data)  # Call synchronous handler directly
                except Exception as e:
                    logger.error(f"Error: Error in event handler for {event}: {e}")

    def _extract_region_from_url(self, url: str) -> str:
        """
        Extracts a region name from a given WebSocket URL.
        Attempts to identify common patterns in PocketOption URLs (e.g., "api-eu", "demo").

        Args:
            url: The WebSocket URL string.

        Returns:
            str: The extracted region name (e.g., "EU", "DEMO"), or "UNKNOWN" if not found.
        """
        try:
            parts = url.split("//")[1].split(".")[0]
            if "api-" in parts:
                return parts.replace("api-", "").upper()
            elif "demo" in parts:
                return "DEMO"
            else:
                return "UNKNOWN"
        except Exception:
            return "UNKNOWN"

    def get_connection_stats(self) -> Dict[str, Any]:
        """
        Retrieves detailed connection statistics and current status.

        Returns:
            Dict[str, Any]: A dictionary containing various connection metrics and information.
        """
        uptime = timedelta()
        if self.connection_info and self.connection_info.connected_at:
            uptime = datetime.now() - self.connection_info.connected_at

        return {
            **self.connection_stats,  # Include existing stats
            "is_connected": self.is_connected,
            "current_url": self.connection_info.url if self.connection_info else None,
            "current_region": self.connection_info.region
            if self.connection_info
            else None,
            "reconnect_attempts": self.current_reconnect_attempts,
            "uptime": str(uptime).split(".")[0],  # Format timedelta for readability
            "available_regions": len(self.available_urls),
        }

    async def connect_with_keep_alive(
        self, regions: Optional[List[str]] = None
    ) -> bool:
        """
        Establishes a persistent connection with keep-alive, optionally using a specific list of regions.
        This method serves as the public entry point for starting the managed connection.

        Args:
            regions: An optional list of region URLs to prioritize for connection. If None,
                     the regions defined during initialization (based on `is_demo`) are used.

        Returns:
            bool: True if the connection is successfully established, False otherwise.
        """
        if regions:
            self.available_urls = regions  # Update the list of URLs to try
            self.current_url_index = (
                0  # Reset index to start from the beginning of the new list
            )
        return await self.start_persistent_connection()

    async def disconnect(self) -> None:
        """
        Disconnects from the WebSocket and cleans up all persistent connection resources.
        This is an alias for `stop_persistent_connection`.
        """
        await self.stop_persistent_connection()

    def get_stats(self) -> Dict[str, Any]:
        """
        Returns connection statistics (alias for `get_connection_stats`).

        Returns:
            Dict[str, Any]: A dictionary containing various connection metrics and information.
        """
        return self.get_connection_stats()


async def demo_keep_alive():
    """
    Demonstrates the usage of the `ConnectionKeepAlive` manager.
    It initializes a manager, registers event handlers, starts a persistent connection,
    runs for a period, and then gracefully shuts down.
    """
    logger.info("Testing: Testing Enhanced Keep-Alive Connection Manager")

    # Example complete SSID in the new '42["auth",...]' format.
    # Replace with your actual SSID for live testing.
    ssid = r'42["auth",{"session":"n1p5ah5u8t9438rbunpgrq0hlq","isDemo":1,"uid":0,"platform":1}]'

    # Create an instance of the keep-alive manager for a demo account.
    keep_alive = ConnectionKeepAlive(ssid, is_demo=True)

    # Define asynchronous event handlers.
    async def on_connected(data: Dict[str, Any]):
        """Handler for 'connected' event."""
        logger.success(
            f"Successfully: Connected to: {data.get('url')} (Region: {data.get('region')})"
        )

    async def on_reconnected(data: Dict[str, Any]):
        """Handler for 'reconnected' event."""
        logger.success(
            f"Persistent: Reconnected after {data.get('attempt')} attempts to {data.get('url')}"
        )

    async def on_message_received(data: Dict[str, Any]):
        """Handler for 'message_received' event (general messages)."""
        message_content = data.get("message", "N/A")
        logger.info(
            f"Message: Received: {message_content[:100]}..."
        )  # Log first 100 chars

    async def on_authenticated(data: Dict[str, Any]):
        """Handler for 'authenticated' event."""
        logger.success(
            "Authentication: Client successfully authenticated with PocketOption."
        )

    async def on_balance_updated(data: Dict[str, Any]):
        """Handler for 'balance_updated' event."""
        logger.info(
            f"Balance: Updated - Balance: {data.get('balance'):.2f} {data.get('currency')}"
        )

    async def on_json_data(data: Dict[str, Any]):
        """Handler for general JSON data messages."""
        logger.debug(f"JSON Data: Received general JSON data: {data}")

    # Register the event handlers with the keep-alive manager.
    keep_alive.add_event_handler("connected", on_connected)
    keep_alive.add_event_handler("reconnected", on_reconnected)
    keep_alive.add_event_handler("message_received", on_message_received)
    keep_alive.add_event_handler("authenticated", on_authenticated)
    keep_alive.add_event_handler("balance_updated", on_balance_updated)
    keep_alive.add_event_handler(
        "balance_data", on_balance_updated
    )  # Also handle balance_data as balance_updated
    keep_alive.add_event_handler("json_data", on_json_data)

    try:
        # Start the persistent connection. This will also start background tasks.
        success = await keep_alive.start_persistent_connection()

        if success:
            logger.info(
                "Starting: Keep-alive connection started, will maintain connection automatically..."
            )

            # Let the connection run for a while to demonstrate its persistence.
            for i in range(60):  # Run for 1 minute (60 seconds)
                await asyncio.sleep(1)  # Wait for 1 second

                # Print connection statistics every 10 seconds.
                if i % 10 == 0:
                    stats = keep_alive.get_connection_stats()
                    logger.info(
                        f"Statistics: Connected={stats['is_connected']}, "
                        f"Messages sent={stats['total_messages_sent']}, "
                        f"Messages received={stats['total_messages_received']}, "
                        f"Uptime={stats['uptime']}"
                    )

                # Send a test message every 30 seconds to demonstrate sending capabilities.
                if i % 30 == 0 and i > 0:
                    await keep_alive.send_message('42["test_message",{"data":"hello"}]')

        else:
            logger.error("Error: Failed to start keep-alive connection.")

    except Exception as e:
        logger.critical(f"Critical Error in demo_keep_alive: {e}")
    finally:
        # Ensure a clean shutdown of the persistent connection and all resources.
        logger.info("Testing: Initiating clean shutdown...")
        await keep_alive.stop_persistent_connection()
        logger.info("Testing: Demo finished.")


if __name__ == "__main__":
    # Run the asynchronous demo function.
    asyncio.run(demo_keep_alive())
