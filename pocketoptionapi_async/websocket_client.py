import asyncio
from typing import Optional, Callable, Dict, Any, List
from datetime import datetime
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
    automatic reconnection, and routing of messages to event handlers.
    """

    def __init__(self):
        """
        Initializes the AsyncWebSocketClient, setting up the Socket.IO client
        with reconnection logic and internal event handlers.
        """
        self.sio: socketio.AsyncClient = socketio.AsyncClient(
            logger=True,  # Enable built-in socketio logging for debugging
            engineio_logger=True,  # Enable built-in engineio logging for debugging
            reconnection=True,  # Enable built-in automatic reconnection
            reconnection_attempts=10,  # Set maximum reconnection attempts for internal logic
            reconnection_delay=1,  # Set initial delay (in seconds) before the first reconnection attempt
            reconnection_delay_max=30,  # Set maximum delay (in seconds) between reconnection attempts
            randomization_factor=0.5,  # Add jitter to reconnection delay to prevent thundering herd
            request_timeout=10,  # Set timeout (in seconds) for initial connection handshake
        )
        self.debug_mode = False  # Add debug mode flag - change if having issues
        self.connection_info: Optional[ConnectionInfo] = (
            None  # Stores detailed connection status and info
        )
        self.server_time: Optional[ServerTime] = (
            None  # Stores server time for synchronization
        )
        self._running = (
            False  # Flag to indicate if the client is intended to be running
        )
        self._event_handlers: Dict[
            str, List[Callable[[Dict[str, Any]], None]]
        ] = {}  # Dictionary to store custom event handlers
        self._reconnect_attempts_counter = (
            0  # Counter for custom reconnection logic (if needed externally)
        )
        self._current_url: Optional[str] = ()
        self._auth_data: Optional[Dict[str, Any]] = (
            None  # Stores the URL of the current successful connection
        )
        self._raw_ssid: Optional[str] = (
            None  # Stores the raw SSID for sending as message
        )
        self._auth_event = asyncio.Event()  # Event to signal authentication completion
        self._is_authenticated = False  # Flag to track authentication status

        # Internal event handlers for standard Socket.IO events
        self.sio.on(
            "connect", self._on_sio_connect
        )  # Handler for successful connection
        self.sio.on("disconnect", self._on_sio_disconnect)  # Handler for disconnection
        self.sio.on(
            "reconnect", self._on_sio_reconnect
        )  # Handler for successful reconnection
        self.sio.on(
            "connect_error", self._on_sio_connect_error
        )  # Handler for connection errors

        # Add logging for all events
        self.sio.on("*", self._on_any_event)  # Handler for all events

        # Catch-all handlers for other Socket.IO messages that don't have explicit handlers
        self.sio.on(
            "message", self._on_sio_message
        )  # Handler for generic 'message' events (raw data)
        self.sio.on(
            "json", self._on_sio_json
        )  # Handler for 'json' events (parsed '42' messages)
        self.sio.on(
            "successauth", self._on_successauth
        )  # Handler for 'successauth' events

        logger.info(
            "AsyncWebSocketClient initialized using python-socketio."
        )  # Log initialization

    async def close(self):
        """Disconnect from the server and clean up resources."""
        if self.sio.connected:
            await self.sio.disconnect()
        logger.info("AsyncWebSocketClient closed.")

    async def connect(
        self, urls: List[str], auth_data: Dict[str, Any], raw_ssid: Optional[str] = None
    ) -> bool:
        """
        Connect to PocketOption Socket.IO server with fallback URLs.
        Uses python-socketio's built-in reconnection logic.
        Args:
            urls: A list of WebSocket URLs to try connecting to.
            auth_data: A dictionary containing authentication parameters (e.g., session, isDemo, uid, platform).
            raw_ssid: The raw SSID string to send as a message after connection.
        Returns:
            bool: True if connected successfully to any URL, False otherwise.
        """
        self._running = True  # Set running flag to True
        self._reconnect_attempts_counter = (
            0  # Reset reconnection attempt counter for a new connection process
        )
        self._raw_ssid = raw_ssid  # Store raw SSID for sending after connection

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
                    logger.info(
                        f"Attempting to connect to {base_url} with auth data: {auth_data}"
                    )
                    await self.sio.connect(
                        base_url,
                        transports=[
                            "websocket"
                        ],  # Explicitly specify WebSocket transport
                        headers=DEFAULT_HEADERS,  # Use predefined default headers (e.g., User-Agent)
                        auth=auth_data,  # Pass authentication data for handshake
                        wait_timeout=connection_timeout,  # Add connection timeout
                    )
                    logger.info(f"Connection attempt to {base_url} completed")

                    # Log connection details in debug mode
                    if self.debug_mode:
                        logger.debug(f"Connected to Socket.IO at {base_url}")
                        logger.debug(f"Authentication data: {auth_data}")

                    logger.info(f"Socket.IO connected status: {self.sio.connected}")
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

    async def disconnect(self):
        """
        Gracefully disconnects from the Socket.IO server.
        Resets internal running flag and connection information.
        """
        logger.info("Disconnecting from Socket.IO client.")  # Log disconnection attempt
        self._running = False  # Set running flag to False
        if self.sio.connected:  # Check if the client is currently connected
            await self.sio.disconnect()  # Disconnect from the server
        logger.success(
            "Disconnected from Socket.IO client."
        )  # Log successful disconnection

    async def send_message(self, event: str, data: Any = None) -> bool:
        """
        Sends a message through the Socket.IO connection.
        Args:
            event: The event name to emit.
            data: The data to send with the event.
        Returns:
            bool: True if the message was sent successfully, False otherwise.
        """
        if not self.sio.connected:
            logger.warning("Cannot send message: Socket.IO client is not connected.")
            return False

        try:
            await self.sio.emit(event, data)
            logger.debug(f"Emitted Socket.IO event: '{event}' with data: {data}")
            return True
        except Exception as e:
            logger.error(f"Failed to send message '{event}': {e}")
            return False

    # --- Internal Socket.IO Event Handlers ---
    async def _on_sio_connect(self):
        """
        Handler for successful Socket.IO connection.
        This is called automatically by the Socket.IO client upon successful connection.
        """
        logger.success("Socket.IO client connected!")  # Log connection success

        # Send raw SSID as message after connection if provided
        if self._raw_ssid:
            logger.info(f"Sending authentication message: {self._raw_ssid[:50]}...")
            try:
                # Parse the raw SSID to extract the event and data
                # The raw SSID should be in the format: 42["event_name", {...data...}]
                import json
                import re

                # Extract event name and data from the raw SSID
                match = re.match(r'42\["([^"]+)",(.*)\]', self._raw_ssid)
                if match:
                    event_name = match.group(1)
                    event_data_str = match.group(2)
                    # Parse the JSON data
                    event_data = json.loads(event_data_str)

                    logger.info(
                        f"Emitting auth event: {event_name} with data: {event_data}"
                    )
                    await self.sio.emit(event_name, event_data)
                    logger.success("Authentication message sent successfully")
                else:
                    logger.error(f"Failed to parse raw SSID: {self._raw_ssid}")
                    # Fallback to sending as raw message
                    await self.sio.send(self._raw_ssid)
                    logger.success("Authentication message sent as raw packet")

                # Reset authentication status and event for new connection
                self._is_authenticated = False
                self._auth_event.clear()
            except Exception as e:
                logger.error(f"Failed to send authentication message: {e}")

        # Try to extract connection URL for logging
        try:
            # This is a simplified approach; the actual URL might be in sio's internal state
            if self._current_url:
                logger.info(f"Connected to: {self._current_url}")
            else:
                logger.warning("Unable to determine connection URL")
        except Exception:
            logger.warning("Unable to determine connection URL")

    async def _on_sio_disconnect(self):
        """
        Handler for Socket.IO disconnection.
        This is called automatically by the Socket.IO client upon disconnection.
        """
        logger.warning("Socket.IO client disconnected!")  # Log disconnection
        await self._emit_event("disconnected", {})  # Emit custom 'disconnected' event

    async def _on_sio_reconnect(self):
        """
        Handler for successful Socket.IO reconnection.
        This is called automatically by the Socket.IO client upon successful reconnection.
        """
        logger.success("Socket.IO client reconnected!")  # Log reconnection success
        await self._emit_event("reconnected", {})  # Emit custom 'reconnected' event

    async def _on_successauth(self, data: Any):
        """
        Handler for 'successauth' events.
        Args:
            data: The data received with the event.
        """
        logger.success(
            f"Authentication successful - received 'successauth' event with data: {data}"
        )  # Log auth success
        # Set authentication status and event
        self._is_authenticated = True
        self._auth_event.set()
        await self._emit_event(
            "authenticated", data if isinstance(data, dict) else {}
        )  # Emit custom 'authenticated' event
        await self._emit_event(
            "successauth", data if isinstance(data, dict) else {}
        )  # Also emit 'successauth' for backward compatibility

    async def _on_any_event(self, event, *args):
        """
        Handler for all events.
        Args:
            event: The event name.
            *args: The event arguments.
        """
        logger.info(f"Received event: {event} with args: {args}")

        # Special logging for authentication-related events
        if "auth" in event.lower():
            logger.info(f"AUTH RELATED EVENT: {event} - {args}")

    async def _on_sio_connect_error(self, error: Exception):
        """
        Handler for Socket.IO connection errors.
        This is called automatically by the Socket.IO client when a connection error occurs.
        Args:
            error: The connection error that occurred.
        """
        logger.error(f"Socket.IO connection error: {error}")  # Log the connection error
        await self._emit_event(
            "connection_error", {"error": str(error)}
        )  # Emit custom 'connection_error' event

        # Enhanced error handling for common issues
        error_str = str(error).lower()
        if "invalid session" in error_str or "session not found" in error_str:
            logger.error(
                "Session error detected: The session might be expired or already in use."
            )
            logger.error("To resolve this issue:")
            logger.error(
                "1. Close the browser tab that's using the PocketOption website"
            )
            logger.error("2. Or generate a new SSID by logging into PocketOption again")
            logger.error(
                "3. Or wait for the browser session to timeout (usually 30 minutes)"
            )

    async def _on_sio_message(self, data: Any):
        """
        Catch-all handler for generic 'message' events (raw messages).
        Args:
            data: The raw message data received.
        """
        logger.debug(f"Socket.IO 'message' event received: {data}")  # Log raw message

        # Log all received messages for debugging
        logger.info(f"DEBUG: Raw message data received: {data}")

        # Log detailed debug information in debug mode
        if self.debug_mode:
            logger.debug(f"DEBUG: Received raw message: {data}")

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

        # Log all received data for debugging
        logger.info(f"DEBUG: Raw JSON data received: {data}")

        # Log detailed debug information in debug mode
        if self.debug_mode:
            logger.debug(f"DEBUG: Received JSON event: {data}")

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

            # Log all events for debugging
            logger.debug(f"DEBUG: Event type '{event_type}' data: {event_data}")

            # Explicitly map common PocketOption events to custom events
            if event_type == "successauth":
                logger.success(
                    "Authentication successful - received 'successauth' event"
                )  # Log auth success
                # Set authentication status and event
                self._is_authenticated = True
                self._auth_event.set()
                await self._emit_event(
                    "authenticated", event_data
                )  # Emit custom 'authenticated' event
                await self._emit_event(
                    "successauth", event_data
                )  # Also emit 'successauth' for backward compatibility
            elif event_type == "auth":
                logger.info(
                    f"Received 'auth' event with data: {event_data}"
                )  # Log auth event
                # Handle auth event as well
                if (
                    isinstance(event_data, dict)
                    and event_data.get("status") == "success"
                ):
                    logger.success(
                        "Authentication successful - received 'auth' event with success status"
                    )
                    self._is_authenticated = True
                    self._auth_event.set()
                    await self._emit_event("authenticated", event_data)
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
                        # Check if this is a session conflict error
                        if (
                            "session" in str(key).lower()
                            and "not found" in str(value).lower()
                        ):
                            logger.error("Session conflict detected")
            elif event_type == "successupdateBalance":
                logger.info(f"Received balance update: {event_data}")
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
                        # Check if this is a session conflict error
                        if (
                            "session" in str(key).lower()
                            and "not found" in str(value).lower()
                        ):
                            logger.error("Session conflict detected")
            elif event_type == "updateAssets":
                logger.info(f"Asset update received: {type(event_data)}")
                await self._emit_event("updateAssets", event_data)
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
            logger.info(f"Received raw JSON data: {data}")
            await self._emit_event("json_data", data)  # Emit as general 'json_data'
        else:
            logger.warning(
                f"Unexpected data format in Socket.IO 'json' event: {data}"
            )  # Warn for unexpected formats

    # --- Public Event Handler Management ---
    def add_event_handler(self, event: str, handler: Callable) -> None:
        """
        Registers an event handler for a specific custom event type.
        Args:
            event: The name of the custom event (e.g., 'connected', 'json_data').
            handler: The callable function to be invoked when the event occurs.
        """
        if event not in self._event_handlers:  # If no handlers for this event yet
            self._event_handlers[event] = []  # Create an empty list
        self._event_handlers[event].append(handler)  # Add the handler to the list

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

    async def wait_for_authentication(self, timeout: float = 20.0) -> bool:
        """
        Wait for authentication to complete.

        Args:
            timeout: How long to wait for authentication to complete.

        Returns:
            bool: True if authenticated, False otherwise.
        """
        try:
            await asyncio.wait_for(self._auth_event.wait(), timeout=timeout)
            return self._is_authenticated
        except asyncio.TimeoutError:
            logger.warning(f"Authentication timeout after {timeout} seconds")
            return False
