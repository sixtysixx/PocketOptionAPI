"""
Enhanced Keep-Alive Connection Manager for PocketOption Async API.
This module provides a robust, persistent WebSocket connection manager
that handles automatic reconnections, periodic pings, and intelligent
message processing, mimicking the behavior of a long-running client.
"""

import asyncio
import json
import ssl
from typing import Optional, List, Callable, Dict, Any
from datetime import datetime, timedelta
from loguru import logger
import socketio

from .models import ConnectionInfo, ConnectionStatus
from .constants import (
    REGIONS,
)


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
        self.websocket: Optional[socketio.AsyncSimpleClient] = None
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

    async def _establish_connection(self) -> bool:
        """
        Establishes a WebSocket connection to PocketOption server.
        Handles SSL context setup and initial connection attempt.

        Returns:
            bool: True if connection is successfully established, False otherwise.
        """
        try:
            # Create SSL context for secure WebSocket connection
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            # Initialize SocketIO client
            self.websocket = socketio.AsyncSimpleClient()
            url = self.available_urls[self.current_url_index]

            # Attempt connection
            await self.websocket.connect(url)
            self.is_connected = True

            # Update connection info
            self.connection_info = ConnectionInfo(
                url=url,
                region=self._extract_region_from_url(url),
                connected_at=datetime.now(),
                status=ConnectionStatus.CONNECTED,
            )

            # Send authentication SSID
            await self.websocket.emit("message", self.ssid)

            return True

        except Exception as e:
            logger.error(f"Connection error: {str(e)}")
            self.is_connected = False
            return False

    async def establish_connection(self) -> bool:
        """
        Public method to establish a WebSocket connection to PocketOption server.
        This is a wrapper around the private _establish_connection method.

        Returns:
            bool: True if connection is successfully established, False otherwise.
        """
        return await self._establish_connection()

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
                    await self.websocket.emit("3")
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
                await self.websocket.emit(message)
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


async def demo_keep_alive():
    """
    Demonstrates the usage of the `ConnectionKeepAlive` manager.
    It initializes a manager, registers event handlers, starts a persistent connection,
    runs for a period, and then gracefully shuts down.
    """
    logger.info("Testing: Testing Enhanced Keep-Alive Connection Manager")

    # Example complete SSID in the '42["auth",...]' format.
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
        success = await keep_alive.establish_connection()

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
                # if i % 30 == 0 and i > 0:
                #    await keep_alive.send_message('42["test_message",{"data":"hello"}]')

                # does this script even need to be here because i think socketio library has an automatic keep-alive

        else:
            logger.error("Error: Failed to start keep-alive connection.")

    except Exception as e:
        logger.critical(f"Critical Error in demo_keep_alive: {e}")
    finally:
        # Ensure a clean shutdown of the persistent connection and all resources.
        logger.info("Testing: Initiating clean shutdown...")
        if keep_alive.websocket:
            if keep_alive.websocket:
                await keep_alive.websocket.disconnect()
        logger.info("Testing: Demo finished.")


if __name__ == "__main__":
    # Run the asynchronous demo function.
    asyncio.run(demo_keep_alive())
