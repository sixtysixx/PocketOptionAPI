"""
Async WebSocket client for PocketOption API
"""

import asyncio
import json
import ssl
from typing import Optional, Callable, Dict, Any, List
from datetime import datetime
import websockets
from websockets.exceptions import ConnectionClosed
from websockets.legacy.client import WebSocketClientProtocol
from loguru import logger

from .models import ConnectionInfo, ConnectionStatus, ServerTime
from .constants import CONNECTION_SETTINGS, DEFAULT_HEADERS
from .exceptions import WebSocketError, ConnectionError


class AsyncWebSocketClient:
    """
    Professional async WebSocket client for PocketOption.
    This client handles WebSocket connections, message sending/receiving,
    and dispatches events based on incoming messages. It includes robust
    error handling and connection management.
    """

    def __init__(self):
        """
        Initializes the AsyncWebSocketClient.
        Sets up internal state variables, queues for messages,
        and event handlers.
        """
        self.websocket: Optional[WebSocketClientProtocol] = None
        self.connection_info: Optional[ConnectionInfo] = None
        self.server_time: Optional[ServerTime] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._event_handlers: Dict[str, List[Callable]] = {}
        self._running = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = CONNECTION_SETTINGS["max_reconnect_attempts"]

    async def connect(self, urls: List[str], ssid: str) -> bool:
        """
        Connect to PocketOption WebSocket with fallback URLs.
        Attempts to establish a WebSocket connection to the provided URLs
        in sequence until successful. Performs an initial handshake.

        Args:
            urls: A list of WebSocket URLs to try connecting to.
            ssid: The session ID string required for authentication after connection.

        Returns:
            bool: True if connected successfully and handshake completed, False otherwise.
        """
        for url in urls:
            try:
                logger.info(f"Attempting to connect to {url}")

                # SSL context setup for secure WebSocket connections (WSS)
                # Disables hostname verification and certificate validation for broader compatibility,
                # which might be adjusted for stricter production environments.
                ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

                # Establish WebSocket connection with specified parameters and a timeout.
                ws = await asyncio.wait_for(
                    websockets.connect(
                        url,
                        ssl=ssl_context,
                        extra_headers=DEFAULT_HEADERS,
                        ping_interval=CONNECTION_SETTINGS["ping_interval"],
                        ping_timeout=CONNECTION_SETTINGS["ping_timeout"],
                        close_timeout=CONNECTION_SETTINGS["close_timeout"],
                    ),
                    timeout=10.0,
                )
                self.websocket = ws  # type: ignore

                # Update connection information upon successful connection.
                region = self._extract_region_from_url(url)
                self.connection_info = ConnectionInfo(
                    url=url,
                    region=region,
                    status=ConnectionStatus.CONNECTED,
                    connected_at=datetime.now(),
                    reconnect_attempts=self._reconnect_attempts,
                )

                logger.info(f"Connected to {region} region successfully")
                self._running = True  # Set client to running state

                # Perform the initial handshake sequence required by PocketOption.
                await self._send_handshake(ssid)

                # Start background tasks like pinging and message receiving after handshake.
                await self._start_background_tasks()

                self._reconnect_attempts = 0  # Reset reconnect attempts on success
                return True

            except Exception as e:
                logger.warning(f"Failed to connect to {url}: {e}")
                if self.websocket:
                    await (
                        self.websocket.close()
                    )  # Ensure WebSocket is closed on failure
                    self.websocket = None
                continue  # Try next URL

        # If all URLs fail, raise a ConnectionError.
        raise ConnectionError("Failed to connect to any WebSocket endpoint")

    async def _handle_payout_message(self, message: str) -> None:
        """
        Handles messages related to asset payout information.
        These messages are typically in the format `[[5, [...]]]`.
        The payout percentage is located at index 5 within the inner list.

        Args:
            message: The raw WebSocket message string containing payout data.
        """
        try:
            # The message starts with "[[5," and is a JSON string.
            # We need to parse it as JSON.
            # Example: [[5, ["5", "#AAPL", "Apple", "stock", 2, 50, ...]]]
            # The structure is a list containing a list, where the first element
            # of the inner list is '5' (indicating payout data), and the rest is the data.

            # Find the start and end of the actual JSON array data
            json_start_index = message.find("[", message.find("[") + 1)
            json_end_index = message.rfind("]")

            if json_start_index == -1 or json_end_index == -1:
                logger.warning(
                    f"Could not find valid JSON array in payout message: {message[:100]}..."
                )
                return

            # Extract the inner JSON string that represents the array of arrays
            json_str = message[json_start_index : json_end_index + 1]

            # Parse the extracted JSON string
            data: List[List[Any]] = json.loads(json_str)

            # Iterate through each asset's payout information
            for asset_data in data:
                # Ensure the asset_data is a list and has enough elements
                if isinstance(asset_data, list) and len(asset_data) > 5:
                    try:
                        # Extract relevant information based on known message structure
                        asset_id = asset_data[0]
                        asset_symbol = asset_data[1]
                        asset_name = asset_data[2]
                        asset_type = asset_data[3]
                        payout_percentage = asset_data[5]  # Payout is at index 5

                        payout_info = {
                            "id": asset_id,
                            "symbol": asset_symbol,
                            "name": asset_name,
                            "type": asset_type,
                            "payout": payout_percentage,
                        }
                        logger.debug(f"Parsed payout info: {payout_info}")
                        # Emit an event with the parsed payout data
                        await self._emit_event("payout_update", payout_info)
                    except IndexError:
                        logger.warning(
                            f"Payout message element missing for asset_data: {asset_data}"
                        )
                    except Exception as e:
                        logger.error(
                            f"Error processing individual asset payout data {asset_data}: {e}"
                        )
                else:
                    logger.warning(
                        f"Unexpected format for asset payout data: {asset_data}"
                    )

        except json.JSONDecodeError as e:
            logger.error(
                f"Failed to decode JSON from payout message '{message[:100]}...': {e}"
            )
        except Exception as e:
            logger.error(
                f"Error in _handle_payout_message for message '{message[:100]}...': {e}"
            )

    async def disconnect(self):
        """
        Gracefully disconnects from the WebSocket.
        Cancels background tasks and closes the WebSocket connection.
        """
        logger.info("Disconnecting from WebSocket")

        self._running = False  # Stop message processing loop

        # Cancel the periodic ping task if it's running.
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            try:
                await self._ping_task  # Await cancellation to ensure cleanup
            except asyncio.CancelledError:
                pass

        # Close the WebSocket connection if it's open.
        if self.websocket:
            await self.websocket.close()
            self.websocket = None

        # Update connection status to disconnected.
        if self.connection_info:
            self.connection_info = ConnectionInfo(
                url=self.connection_info.url,
                region=self.connection_info.region,
                status=ConnectionStatus.DISCONNECTED,
                connected_at=self.connection_info.connected_at,
                reconnect_attempts=self.connection_info.reconnect_attempts,
            )

    async def send_message(self, message: str) -> None:
        """
        Sends a message over the WebSocket connection.

        Args:
            message: The string message to send.

        Raises:
            WebSocketError: If the WebSocket is not connected or if sending fails.
        """
        if not self.websocket or self.websocket.closed:
            raise WebSocketError("WebSocket is not connected")

        try:
            await self.websocket.send(message)
            logger.debug(f"Sent message: {message}")
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            raise WebSocketError(f"Failed to send message: {e}")

    async def receive_messages(self) -> None:
        """
        Continuously receives and processes messages from the WebSocket.
        This runs as a background task after a successful connection.
        Handles connection closures and timeouts during message reception.
        """
        try:
            while self._running and self.websocket:
                try:
                    # Wait for an incoming message with a timeout.
                    message = await asyncio.wait_for(
                        self.websocket.recv(),
                        timeout=CONNECTION_SETTINGS["message_timeout"],
                    )
                    await self._process_message(message)  # Process the received message

                except asyncio.TimeoutError:
                    logger.warning("Message receive timeout, continuing to listen.")
                    continue  # Continue loop to try receiving again
                except ConnectionClosed:
                    logger.warning("WebSocket connection closed by peer.")
                    await self._handle_disconnect()  # Handle the disconnection
                    break  # Exit loop as connection is closed
                except Exception as e:
                    logger.error(f"Error during message reception: {e}")
                    await self._handle_disconnect()  # Handle unexpected errors
                    break

        except Exception as e:
            logger.error(f"Critical error in message receiving loop: {e}")
            await self._handle_disconnect()

    def add_event_handler(self, event: str, handler: Callable) -> None:
        """
        Registers an event handler for a specific event type.

        Args:
            event: The name of the event (e.g., 'authenticated', 'balance_updated').
            handler: The callable function to be executed when the event occurs.
        """
        if event not in self._event_handlers:
            self._event_handlers[event] = []
        self._event_handlers[event].append(handler)

    def remove_event_handler(self, event: str, handler: Callable) -> None:
        """
        Removes a previously registered event handler.

        Args:
            event: The name of the event.
            handler: The handler function to remove.
        """
        if event in self._event_handlers:
            try:
                self._event_handlers[event].remove(handler)
            except ValueError:
                pass  # Handler not found, ignore

    async def _send_handshake(self, ssid: str) -> None:
        """
        Performs the initial handshake sequence with the PocketOption server.
        This involves sending and receiving specific messages to establish the session.

        Args:
            ssid: The session ID to be sent for authentication.

        Raises:
            WebSocketError: If the handshake times out or fails unexpectedly.
        """
        try:
            # Step 1: Wait for the initial "0" message from the server.
            logger.debug("Waiting for initial handshake message (type '0')...")
            if not self.websocket:
                raise WebSocketError("WebSocket is not connected during handshake")
            initial_message = await asyncio.wait_for(
                self.websocket.recv(), timeout=10.0
            )
            logger.debug(f"Received initial: {initial_message}")

            # Ensure the message is decoded to a string for processing.
            if isinstance(initial_message, memoryview):
                initial_message = bytes(initial_message).decode("utf-8")
            elif isinstance(initial_message, (bytes, bytearray)):
                initial_message = initial_message.decode("utf-8")

            # Validate the initial message format and proceed.
            if initial_message.startswith("0") and "sid" in initial_message:
                # Step 2: Send the "40" response.
                await self.send_message("40")
                logger.debug("Sent '40' response.")

                # Step 3: Wait for the connection establishment message ("40" with "sid").
                conn_message = await asyncio.wait_for(
                    self.websocket.recv(), timeout=10.0
                )
                logger.debug(f"Received connection: {conn_message}")

                # Ensure the message is decoded to a string.
                if isinstance(conn_message, memoryview):
                    conn_message_str = bytes(conn_message).decode("utf-8")
                elif isinstance(conn_message, (bytes, bytearray)):
                    conn_message_str = conn_message.decode("utf-8")
                else:
                    conn_message_str = conn_message

                # Validate the connection message format.
                if conn_message_str.startswith("40") and "sid" in conn_message_str:
                    # Step 4: Send the SSID for authentication.
                    await self.send_message(ssid)
                    logger.debug("Sent SSID authentication.")
                else:
                    logger.warning(
                        f"Unexpected connection message format during handshake: {conn_message_str[:100]}..."
                    )
            else:
                logger.warning(
                    f"Unexpected initial handshake message format: {initial_message[:100]}..."
                )

            logger.debug("Handshake sequence completed.")

        except asyncio.TimeoutError:
            logger.error("Handshake timeout - server didn't respond as expected.")
            raise WebSocketError("Handshake timeout")
        except Exception as e:
            logger.error(f"Handshake failed: {e}")
            raise

    async def _start_background_tasks(self) -> None:
        """
        Starts necessary background tasks after a successful connection.
        This includes the periodic ping task and the continuous message receiving task.
        """
        # Start the periodic ping task to keep the connection alive.
        self._ping_task = asyncio.create_task(self._ping_loop())

        # Start the message receiving task. This task will continuously listen
        # for incoming messages from the WebSocket.
        asyncio.create_task(self.receive_messages())

    async def _ping_loop(self) -> None:
        """
        Sends periodic ping messages to the server to maintain the WebSocket connection.
        The interval is defined in CONNECTION_SETTINGS.
        """
        while self._running and self.websocket:
            try:
                await asyncio.sleep(CONNECTION_SETTINGS["ping_interval"])

                if self.websocket and not self.websocket.closed:
                    # Send a specific ping message expected by the server.
                    await self.send_message('42["ps"]')

                    # Update the last ping time in connection info.
                    if self.connection_info:
                        self.connection_info = ConnectionInfo(
                            url=self.connection_info.url,
                            region=self.connection_info.region,
                            status=self.connection_info.status,
                            connected_at=self.connection_info.connected_at,
                            last_ping=datetime.now(),
                            reconnect_attempts=self.connection_info.reconnect_attempts,
                        )

            except Exception as e:
                logger.error(f"Ping task failed: {e}")
                break  # Exit loop on error, allowing disconnection to be handled.

    async def _process_message(self, message) -> None:
        """
        Processes incoming WebSocket messages. This method acts as a central dispatcher
        for different types of messages received from the PocketOption server,
        decoding them and emitting corresponding events.

        Args:
            message: The raw message received from the WebSocket. This can be bytes or a string.
        """
        try:
            # All messages are expected to be strings after initial connection,
            # so decode bytes if necessary.
            if isinstance(message, bytes):
                message = message.decode("utf-8")

            logger.debug(f"Received message: {message}")

            # Handle initial handshake messages ("0" and "40")
            # These are specific non-JSON messages related to WebSocket protocol setup.
            if message.startswith("0") and "sid" in message:
                await self.send_message("40")
                return
            elif message.startswith("40") and "sid" in message:
                # This signifies a successful WebSocket connection establishment.
                await self._emit_event("connected", {})
                return
            elif message == "2":
                # This is a ping request from the server. Respond with "3" (pong).
                await self.send_message("3")
                return

            # Handle specific JSON message formats based on their prefixes.

            # Case 1: Payout messages, which start with "[[5," and are direct JSON arrays.
            # Example: [[5, ["5", "#AAPL", "Apple", "stock", 2, 50, ...]]]
            if message.startswith("[[5,"):
                await self._handle_payout_message(message)
                return

            # Case 2: Messages starting with "42" followed by a JSON payload.
            # This covers most application-level messages like authentication results,
            # balance updates, order events, etc., in the new format.
            # Examples: 42["successauth", {...}], 42["balance_updated", {...}]
            if message.startswith("42"):
                # Check for specific non-JSON "42" messages (like "NotAuthorized")
                if "NotAuthorized" in message:
                    logger.error("Authentication failed: Invalid SSID")
                    await self._emit_event("auth_error", {"message": "Invalid SSID"})
                    return
                # If it's a general "42" message, attempt to parse the rest as JSON.
                try:
                    json_payload_str = message[2:]  # Remove the "42" prefix
                    data = json.loads(json_payload_str)

                    # If the parsed data is a list, it typically follows the ["event_type", data] pattern.
                    if isinstance(data, list) and len(data) > 0:
                        await self._handle_json_message(data)
                    # If it's a dictionary, it might be a direct JSON object.
                    elif isinstance(data, dict):
                        await self._emit_event(
                            "json_data", data
                        )  # Emit as general JSON data
                    else:
                        logger.warning(
                            f"Unexpected 42-prefixed message content type: {type(data).__name__} for message: {message[:100]}..."
                        )
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"Failed to decode JSON from 42-prefixed message '{message[:100]}...': {e}"
                    )
                except Exception as e:
                    logger.error(
                        f"Error handling 42-prefixed message '{message[:100]}...': {e}"
                    )
                return  # Message handled or attempted to be handled

            # Case 3: Messages starting with "451-" followed by a JSON payload.
            # This is an older format for structured JSON messages.
            # Example: 451-[["updateStream", {...}]]
            if message.startswith("451-["):
                json_part = message.split("-", 1)[1]
                data = json.loads(json_part)
                # _handle_json_message expects a list like ["event_type", data]
                # The 451- prefix usually contains a single event as a list within a list.
                # Example: data = [["updateStream", {...}]]
                # We need to pass the inner list to _handle_json_message.
                if (
                    isinstance(data, list)
                    and len(data) > 0
                    and isinstance(data[0], list)
                ):
                    await self._handle_json_message(data[0])  # Pass the inner list
                else:
                    logger.warning(
                        f"Unexpected 451- message format: {message[:100]}..."
                    )
                return

            # If none of the above specific patterns match, log as unhandled.
            logger.warning(f"Unhandled WebSocket message: {message[:100]}...")

        except Exception as e:
            logger.error(f"An unexpected error occurred while processing message: {e}")

    async def _handle_json_message(self, data: List[Any]) -> None:
        """
        Handles JSON formatted messages where the first element of the JSON array
        is the event type and the second is the event data.

        Args:
            data: The parsed JSON data, expected to be a list like `[event_type, event_data]`.
        """
        if not data or not isinstance(data, list) or len(data) < 1:
            logger.warning(f"Invalid JSON message format: {data}")
            return

        event_type = data[0]
        event_data = data[1] if len(data) > 1 else {}

        # Dispatch events based on the event type.
        if event_type == "successauth":
            await self._emit_event("authenticated", event_data)
        elif event_type == "successupdateBalance":
            await self._emit_event("balance_updated", event_data)
        elif event_type == "successopenOrder":
            await self._emit_event("order_opened", event_data)
        elif event_type == "successcloseOrder":
            await self._emit_event("order_closed", event_data)
        elif event_type == "updateStream":
            await self._emit_event("stream_update", event_data)
        elif event_type == "loadHistoryPeriod":
            await self._emit_event("candles_received", event_data)
        elif event_type == "updateHistoryNew":
            await self._emit_event("history_update", event_data)
        else:
            # For any other unrecognized event type, emit a generic 'unknown_event'.
            await self._emit_event(
                "unknown_event", {"type": event_type, "data": event_data}
            )

    async def _emit_event(self, event: str, data: Dict[str, Any]) -> None:
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
                    logger.error(f"Error in event handler for {event}: {e}")

    async def _handle_disconnect(self) -> None:
        """
        Handles the WebSocket disconnection event.
        Updates connection status and emits a 'disconnected' event.
        """
        if self.connection_info:
            self.connection_info = ConnectionInfo(
                url=self.connection_info.url,
                region=self.connection_info.region,
                status=ConnectionStatus.DISCONNECTED,
                connected_at=self.connection_info.connected_at,
                last_ping=self.connection_info.last_ping,
                reconnect_attempts=self.connection_info.reconnect_attempts,
            )

        # Emit a disconnected event to allow the main client to handle reconnection logic.
        await self._emit_event("disconnected", {})

        # The reconnection logic is typically handled by the main client (AsyncPocketOptionClient)
        # rather than directly within the WebSocket client itself.

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
            # Example: "wss://api-eu.po.market/..." -> "api-eu"
            parts = url.split("//")[1].split(".")[0]
            if "api-" in parts:
                return parts.replace("api-", "").upper()
            elif "demo" in parts:
                return "DEMO"
            else:
                return "UNKNOWN"
        except Exception:
            return "UNKNOWN"

    @property
    def is_connected(self) -> bool:
        """
        Checks if the WebSocket client is currently connected and ready for communication.

        Returns:
            bool: True if the WebSocket is open and the connection status is CONNECTED, False otherwise.
        """
        return (
            self.websocket is not None
            and not self.websocket.closed
            and self.connection_info is not None
            and self.connection_info.status == ConnectionStatus.CONNECTED
        )
