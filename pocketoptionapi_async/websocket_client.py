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
        self.sio: socketio.AsyncClient = socketio.AsyncClient(
            logger=False,  # Disable built-in socketio logging if Loguru handles it
            engineio_logger=False,  # Disable built-in engineio logging
            reconnection=True,  # Enable built-in reconnection
            reconnection_attempts=10,  # Max attempts for internal reconnection
            reconnection_delay=1,  # Initial delay
            reconnection_delay_max=30,  # Max delay
            randomization_factor=0.5,  # Jitter factor for reconnection delay
            request_timeout=10,  # Timeout for initial connection attempts
        )
        self.connection_info: Optional[ConnectionInfo] = None
        self.server_time: Optional[ServerTime] = None
        self._running = False
        self._event_handlers: Dict[str, List[Callable]] = {}
        self._reconnect_attempts_counter = 0  # To track custom reconnects if needed

        # Internal event handlers for socket.io events
        self.sio.on("connect", self._on_sio_connect)
        self.sio.on("disconnect", self._on_sio_disconnect)
        self.sio.on("reconnect", self._on_sio_reconnect)
        self.sio.on("connect_error", self._on_sio_connect_error)

        # Catch-all for other Socket.IO messages that don't have explicit handlers
        self.sio.on("message", self._on_sio_message)
        self.sio.on("json", self._on_sio_json)  # For '42' messages parsed by socket.io

        logger.info("AsyncWebSocketClient initialized using python-socketio.")

    async def connect(self, urls: List[str], auth_data: Dict[str, Any]) -> bool:
        """
        Connect to PocketOption Socket.IO server with fallback URLs.
        Uses python-socketio's built-in reconnection logic.

        Args:
            urls: A list of WebSocket URLs to try connecting to.
            auth_data: A dictionary containing authentication parameters (session, isDemo, uid, platform).

        Returns:
            bool: True if connected successfully, False otherwise.
        """
        self._running = True
        self._reconnect_attempts_counter = 0  # Reset on new connection attempt

        # If a region is preferred in connection_info, prioritize that URL
        # The main client handles region selection, this class takes a pre-selected list.
        # We rotate through the provided URLs.
        current_urls = list(urls)  # Create a mutable copy

        # Shuffle urls to distribute connections
        import random

        random.shuffle(current_urls)

        for url in current_urls:
            try:
                # python-socketio handles the '/socket.io/?EIO=4&transport=websocket' part
                # So we connect to the base URL
                base_url = url.split("/socket.io")[0]

                logger.info(f"Attempting to connect to Socket.IO at {base_url}")

                # Use default headers (including random User-Agent)
                # and pass auth_data directly to the Socket.IO client.
                # The 'auth' parameter in connect() sends an EIO3 'ping' message
                # or an EIO4 'connect' packet with auth data.
                await self.sio.connect(
                    base_url,
                    transports=["websocket"],
                    headers=DEFAULT_HEADERS,
                    auth=auth_data,  # python-socketio will handle sending this
                    # The following are internal socketio/engineio settings, can be overridden if needed
                    # ping_interval=CONNECTION_SETTINGS["ping_interval"], # Managed by self.sio.__init__
                    # ping_timeout=CONNECTION_SETTINGS["ping_timeout"],
                    # close_timeout=CONNECTION_SETTINGS["close_timeout"],
                    # reconnection_attempts etc. also managed by self.sio.__init__
                )

                if self.sio.connected:
                    region = self._extract_region_from_url(
                        url
                    )  # Extract from original URL
                    self.connection_info = ConnectionInfo(
                        url=url,
                        region=region,
                        status=ConnectionStatus.CONNECTED,
                        connected_at=datetime.now(),
                        reconnect_attempts=self._reconnect_attempts_counter,
                    )
                    logger.success(f"Successfully connected to {region} via Socket.IO")
                    await self._emit_event("connected", {"url": url, "region": region})
                    return True
            except ConnectionError as e:
                logger.warning(f"Socket.IO connection failed to {url}: {e}")
                # This error often indicates a failed handshake or refusal
                if self.sio.connected:
                    await self.sio.disconnect()  # Ensure cleanup
                    await self.sio.disconnect()  # Ensure cleanup
            except asyncio.TimeoutError:
                logger.warning(f"Connection attempt to {url} timed out.")
                if self.sio.connected:
                    await self.sio.disconnect()
            except Exception as e:
                logger.error(f"Unexpected error during connection to {url}: {e}")
                if self.sio.connected:
                    await self.sio.disconnect()

            self._reconnect_attempts_counter += 1  # Increment for each URL attempt

        logger.error("Failed to connect to any Socket.IO endpoint after all attempts.")
        return False

    async def disconnect(self):
        """
        Gracefully disconnects from the Socket.IO server.
        """
        logger.info("Disconnecting from Socket.IO client.")
        self._running = False
        if self.sio.connected:
            await self.sio.disconnect()
        self.connection_info = None  # Clear connection info

    async def send_message(self, event_name: str, data: Any = None) -> bool:
        """
        Sends a message (event) over the Socket.IO connection.

        Args:
            event_name: The name of the Socket.IO event to emit.
            data: The data payload to send with the event (can be dict, list, str, int, etc.).

        Returns:
            bool: True if the message was successfully sent, False otherwise.
        """
        if not self.sio.connected:
            logger.warning("Cannot send message: Socket.IO client is not connected.")
            raise WebSocketError("Socket.IO client is not connected")

        try:
            # Emit event. python-socketio handles the framing (e.g., '42["event_name", data]')
            if data is not None:
                await self.sio.emit(event_name, data)
            else:
                await self.sio.emit(event_name)  # Send event without data if None

            logger.debug(f"Emitted Socket.IO event: '{event_name}' with data: {data}")
            return True
        except Exception as e:
            logger.error(f"Failed to emit Socket.IO event '{event_name}': {e}")
            raise WebSocketError(f"Failed to send Socket.IO event: {e}")

    # --- Internal Socket.IO Event Handlers ---
    async def _on_sio_connect(self):
        """Handler for Socket.IO 'connect' event."""
        logger.success("Socket.IO client connected!")
        # This initial 'connect' event from socketio means the Engine.IO handshake is done
        # The higher-level client needs to know when the *authentication* is complete.
        # So we update connection info here and _emit_event("connected") will be done
        # by the main client after authentication.

        # Note: self.sio.url holds the *last successfully connected* URL after handshake
        if (
            not self.connection_info
        ):  # If connection_info hasn't been set yet (first connect)
            region = self._extract_region_from_url(self.sio.eio.url)
            self.connection_info = ConnectionInfo(
                url=self.sio.eio.current_url,
                region=region,
                status=ConnectionStatus.CONNECTED,
                connected_at=datetime.now(),
                reconnect_attempts=self._reconnect_attempts_counter,
            )
        else:  # On re-connection, update status
            self.connection_info = ConnectionInfo(
                url=self.connection_info.url,  # Keep original URL
                region=self.connection_info.region,
                status=ConnectionStatus.CONNECTED,
                connected_at=datetime.now(),
                last_ping=self.connection_info.last_ping,
                reconnect_attempts=self.connection_info.reconnect_attempts + 1
                if self._running
                else 0,
            )

    async def _on_sio_disconnect(self):
        """Handler for Socket.IO 'disconnect' event."""
        logger.warning("Socket.IO client disconnected!")
        if self.connection_info:
            self.connection_info = ConnectionInfo(
                url=self.connection_info.url,
                region=self.connection_info.region,
                status=ConnectionStatus.DISCONNECTED,
                connected_at=self.connection_info.connected_at,
                last_ping=self.connection_info.last_ping,
                reconnect_attempts=self.connection_info.reconnect_attempts,
            )
        await self._emit_event("disconnected", {})

    async def _on_sio_reconnect(self, attempt_count):
        """Handler for Socket.IO 'reconnect' event."""
        logger.info(f"Socket.IO client reconnected after {attempt_count} attempts!")
        # Update connection info, the main client should re-authenticate
        if self.connection_info:
            self.connection_info = ConnectionInfo(
                url=self.connection_info.url,
                region=self.connection_info.region,
                status=ConnectionStatus.CONNECTED,
                connected_at=datetime.now(),  # Update connected_at on successful reconnect
                last_ping=self.connection_info.last_ping,
                reconnect_attempts=attempt_count,
            )
        await self._emit_event(
            "reconnected",
            {
                "attempt": attempt_count,
                "url": self.connection_info.url if self.connection_info else None,
            },
        )

    async def _on_sio_connect_error(self, data):
        """Handler for Socket.IO 'connect_error' event."""
        logger.error(f"Socket.IO connection error: {data}")
        await self._emit_event("connect_error", {"message": str(data)})
        # This will be handled by the main client for potential fallback regions

    async def _on_sio_message(self, data):
        """Catch-all handler for 'message' events (raw messages)."""
        logger.debug(f"Socket.IO 'message' event received: {data}")
        # This could be text messages, or messages not fitting the 'event, data' pattern
        await self._emit_event("message_received", {"message": data})

    async def _on_sio_json(self, data):
        """
        Handler for 'json' events. python-socketio typically parses '42' messages
        into this. The data format here is usually `[event_name, data_payload]`.
        """
        logger.debug(f"Socket.IO 'json' event received: {data}")

        if isinstance(data, list) and len(data) > 0:
            event_type = data[0]
            event_data = data[1] if len(data) > 1 else {}

            # Explicitly map common PocketOption events
            if event_type == "successauth":
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
            elif event_type == "loadHistoryPeriod":
                await self._emit_event("candles_received", event_data)
            elif event_type == "payout_update":
                # Special handling for payout_update which might come as part of `42`
                # if the original message was parsed differently.
                await self._emit_event("payout_update", event_data)
            elif event_type == "updateHistoryNew":
                await self._emit_event("history_update", event_data)
            else:
                # Fallback for unrecognized Socket.IO events
                await self._emit_event(
                    "unknown_event", {"type": event_type, "data": event_data}
                )
        elif isinstance(data, dict):
            # Sometimes a raw JSON object might be sent without an event_type array.
            await self._emit_event("json_data", data)
        else:
            logger.warning(f"Unexpected data format in Socket.IO 'json' event: {data}")

    # --- Public Event Handler Management ---
    def add_event_handler(self, event: str, handler: Callable) -> None:
        """
        Registers an event handler for a specific event type.
        """
        if event not in self._event_handlers:
            self._event_handlers[event] = []
        self._event_handlers[event].append(handler)

    def remove_event_handler(self, event: str, handler: Callable) -> None:
        """
        Removes a previously registered event handler.
        """
        if event in self._event_handlers:
            try:
                self._event_handlers[event].remove(handler)
            except ValueError:
                pass

    async def _emit_event(self, event: str, data: Dict[str, Any]) -> None:
        """
        Emits an event to all registered handlers for that event type.
        Supports both synchronous and asynchronous handler functions.
        """
        if event in self._event_handlers:
            for handler in self._event_handlers[event]:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(data)
                    else:
                        handler(data)
                except Exception as e:
                    logger.error(f"Error in event handler for {event}: {e}")

    # --- Utility Methods ---
    def _extract_region_from_url(self, url: str) -> str:
        """
        Extracts a region name from a given WebSocket URL.
        """
        try:
            parts = url.split("//")[1].split(".")[0]
            if "api-" in parts:
                return parts.replace("api-", "").upper()
            elif "demo" in parts:
                return "DEMO"
            elif "chat-po" in parts:  # Added for the specific HAR file example
                return "CHAT_PO"
            elif "events-po" in parts:  # Added for the specific HAR file example
                return "EVENTS_PO"
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
        """
        return self.sio.connected
