import asyncio
import os
import sys
import json
from typing import Dict, Any
from loguru import logger
from dotenv import load_dotenv

# Add the parent directory to the Python path to allow importing pocketoptionapi_async
# This makes the 'pocketoptionapi_async' package discoverable when running from 'demos'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Load environment variables from .env file (expected in the project root)
# The `dotenv_path` argument explicitly points to the .env file in the parent directory.
load_dotenv(
    dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
)

# Configure loguru for clear output
logger.remove()
logger.add(
    os.getenv("LOG_FILE", "test_websocket_functions.log"),
    rotation=os.getenv("LOG_ROTATION", "10 MB"),
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
    enqueue=True,  # Use a queue for logging to avoid blocking
)
logger.add(
    lambda msg: print(msg, end=""),
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
    colorize=True,
    enqueue=True,
    diagnose=False,
)

# Import necessary classes from the pocketoptionapi_async package
try:
    from pocketoptionapi_async.client import AsyncPocketOptionClient
    from pocketoptionapi_async.websocket_client import AsyncWebSocketClient
    from pocketoptionapi_async.connection_keep_alive import ConnectionKeepAlive
    from pocketoptionapi_async.models import OrderDirection, Balance, OrderResult
    from pocketoptionapi_async.constants import REGIONS
    from pocketoptionapi_async.exceptions import (
        PocketOptionError,
        OrderError,
    )
except ImportError as e:
    logger.error(
        f"Failed to import project modules. Ensure your Python path is correct and all files are present. Error: {e}"
    )
    logger.info(
        "Please ensure your project structure is 'project_root/pocketoptionapi_async/' and 'project_root/demos/test_websocket_functions.py'."
    )
    logger.info(
        "The script attempts to add the parent directory to sys.path for imports."
    )
    exit(1)


# --- Dummy SSID for testing ---
# This SSID is for demonstration only. A real connection and authentication
# will require a valid SSID obtained from a PocketOption login session.
# The format is '42["auth",{"session":"YOUR_SESSION_ID","isDemo":1,"uid":0,"platform":1}]'
TEST_SSID = os.getenv(
    "PO_SESSION_ID",
    r'42["auth",{"session":"demo_session_for_testing_dummy_ssid","isDemo":1,"uid":0,"platform":1}]',
)


# --- Event Handlers for Testing ---
# These functions will be called when corresponding events are emitted by the clients.
# They primarily log the event and its data to confirm that the event system works.


async def on_connected(data: Dict[str, Any]):
    """Callback for 'connected' event."""
    logger.success(
        f"Event: Connected! URL: {data.get('url')}, Region: {data.get('region')}"
    )


async def on_reconnected(data: Dict[str, Any]):
    """Callback for 'reconnected' event."""
    logger.info(
        f"Event: Reconnected! Attempt: {data.get('attempt')}, URL: {data.get('url')}"
    )


async def on_disconnected(data: Dict[str, Any]):
    """Callback for 'disconnected' event."""
    logger.warning("Event: Disconnected!")


async def on_authenticated(data: Dict[str, Any]):
    """Callback for 'authenticated' event."""
    logger.success("Event: Authenticated successfully!")


async def on_balance_updated(data: Balance):
    """Callback for 'balance_updated' event."""
    logger.info(
        f"Event: Balance Updated: {data.balance:.2f} {data.currency} (Demo: {data.is_demo})"
    )


async def on_order_opened(data: Dict[str, Any]):
    """Callback for 'order_opened' event."""
    logger.info(f"Event: Order Opened: {data.get('requestId')}")


async def on_order_closed(data: OrderResult):
    """Callback for 'order_closed' event."""
    logger.info(
        f"Event: Order Closed: {data.order_id}, Status: {data.status.value}, Profit: {data.profit}"
    )


async def on_stream_update(data: Dict[str, Any]):
    """Callback for 'stream_update' event."""
    # This event can be very verbose, so log at debug level
    logger.debug(f"Event: Stream Update: {data.get('asset', 'N/A')}")


async def on_candles_received(data: Dict[str, Any]):
    """Callback for 'candles_received' event."""
    # This event can also be verbose, log at debug level
    logger.debug(f"Event: Candles Received: {len(data.get('candles', []))} candles")


async def on_json_data(data: Dict[str, Any]):
    """Callback for 'json_data' event (general JSON messages)."""
    # This is a fallback for unhandled JSON, can be very verbose
    logger.debug(f"Event: General JSON Data: {json.dumps(data)[:100]}...")


async def on_auth_error(data: Dict[str, Any]):
    """Callback for 'auth_error' event."""
    logger.error(f"Event: Authentication Error: {data.get('message')}")


async def on_payout_update(data: Dict[str, Any]):
    """Callback for 'payout_update' event."""
    logger.info(f"Event: Payout Update for {data.get('symbol')}: {data.get('payout')}%")


async def on_message_received(data: Dict[str, Any]):
    """Callback for 'message_received' event (raw messages)."""
    # This is a general catch-all for raw messages, can be very verbose
    logger.debug(f"Event: Raw Message Received: {data.get('message', '')[:100]}...")


async def on_max_reconnects_reached(data: Dict[str, Any]):
    """Callback for 'max_reconnects_reached' event."""
    logger.critical(
        f"Event: Max Reconnect Attempts Reached: {data.get('attempts')} attempts."
    )


# --- Test Functions ---


async def test_async_websocket_client():
    """
    Tests the AsyncWebSocketClient's core functionalities:
    connection, disconnection, message sending, and event handling.
    """
    logger.info("\n--- Testing AsyncWebSocketClient ---")
    client = AsyncWebSocketClient()

    # Register event handlers
    client.add_event_handler("connected", on_connected)
    client.add_event_handler("disconnected", on_disconnected)
    client.add_event_handler("authenticated", on_authenticated)
    client.add_event_handler("balance_updated", on_balance_updated)
    client.add_event_handler("order_opened", on_order_opened)
    client.add_event_handler("order_closed", on_order_closed)
    client.add_event_handler("stream_update", on_stream_update)
    client.add_event_handler("candles_received", on_candles_received)
    client.add_event_handler("json_data", on_json_data)
    client.add_event_handler("auth_error", on_auth_error)
    client.add_event_handler("payout_update", on_payout_update)
    client.add_event_handler("message_received", on_message_received)

    # Test connection
    logger.info("Attempting to connect AsyncWebSocketClient...")
    try:
        # Use a demo region URL for testing
        demo_urls = REGIONS.get_demo_regions()
        if not demo_urls:
            logger.error(
                "No demo regions found in constants.py. Cannot test connection."
            )
            return

        # Try connecting to the first demo URL
        auth_data = {"session": TEST_SSID, "isDemo": 1, "uid": 0, "platform": 1}
        connected = await client.connect([demo_urls[0]], auth_data)
        logger.info(f"AsyncWebSocketClient connected status: {connected}")
        logger.info(f"Is client connected (property): {client.is_connected}")

        if connected:
            # Test sending a message (e.g., request balance)
            logger.info("Testing send_message: Requesting balance...")
            await client.send_message('42["getBalance"]')
            await asyncio.sleep(2)  # Give time for response

            # Test sending a message to trigger stream update (e.g., change symbol)
            # This might trigger 'stream_update' and 'candles_received' events
            logger.info("Testing send_message: Requesting EURUSD_otc stream...")
            await client.send_message(
                '42["changeSymbol",{"asset":"EURUSD_otc","period":60}]'
            )
            await asyncio.sleep(5)  # Give time for stream data

            # Test remove_event_handler
            logger.info("Testing remove_event_handler...")
            client.remove_event_handler("connected", on_connected)
            # This won't be visibly testable unless we reconnect, but ensures the method works.

        else:
            logger.warning(
                "AsyncWebSocketClient failed to connect. Skipping message tests."
            )

    except Exception as e:
        logger.error(f"Error during AsyncWebSocketClient connection/testing: {e}")

    finally:
        # Test disconnection
        logger.info("Attempting to disconnect AsyncWebSocketClient...")
        await client.disconnect()
        logger.info(
            f"AsyncWebSocketClient connected status after disconnect: {client.is_connected}"
        )
        logger.info("AsyncWebSocketClient test finished.")
        await asyncio.sleep(1)  # Small delay


async def test_connection_keep_alive():
    """
    Tests the ConnectionKeepAlive manager's functionalities:
    persistent connection, auto-reconnection, message sending, and stats.
    """
    logger.info("\n--- Testing ConnectionKeepAlive ---")
    keep_alive: ConnectionKeepAlive = ConnectionKeepAlive(TEST_SSID, is_demo=True)

    # Register event handlers
    keep_alive.add_event_handler("connected", on_connected)
    keep_alive.add_event_handler("reconnected", on_reconnected)
    keep_alive.add_event_handler("disconnected", on_disconnected)
    keep_alive.add_event_handler("authenticated", on_authenticated)
    keep_alive.add_event_handler("balance_updated", on_balance_updated)
    keep_alive.add_event_handler("json_data", on_json_data)
    keep_alive.add_event_handler("message_received", on_message_received)
    keep_alive.add_event_handler("max_reconnects_reached", on_max_reconnects_reached)

    # Test starting persistent connection
    logger.info("Attempting to start persistent connection with ConnectionKeepAlive...")
    try:
        connected = await keep_alive.establish_connection()
        logger.info(f"ConnectionKeepAlive connected status: {connected}")
        logger.info(f"Is keep_alive connected (property): {keep_alive.is_connected}")

        if connected:
            # Test sending a message
            logger.info(
                "Testing send_message via ConnectionKeepAlive: Requesting balance..."
            )
            await keep_alive.send_message('42["getBalance"]')
            await asyncio.sleep(2)  # Give time for response

            # Test getting connection stats
            stats = keep_alive.get_connection_stats()
            logger.info(
                f"ConnectionKeepAlive Stats: {json.dumps(stats, indent=2, default=str)}"
            )

            # Simulate a brief run time for persistent connection
            logger.info("Allowing persistent connection to run for 10 seconds...")
            await asyncio.sleep(10)

        else:
            logger.warning(
                "ConnectionKeepAlive failed to connect. Skipping message/stats tests."
            )

    except Exception as e:
        logger.error(f"Error during ConnectionKeepAlive connection/testing: {e}")

    finally:
        # Test stopping persistent connection
        logger.info("Attempting to stop persistent connection...")
        if keep_alive.websocket is not None and keep_alive.is_connected:
            if keep_alive.websocket:
                    await keep_alive.websocket.disconnect()
        logger.info(
            f"ConnectionKeepAlive connected status after stop: {keep_alive.is_connected}"
        )
        logger.info("ConnectionKeepAlive test finished.")
        await asyncio.sleep(1)  # Small delay


async def test_async_pocket_option_client_with_websocket_mode():
    """
    Tests the high-level AsyncPocketOptionClient in its default (non-persistent) WebSocket mode.
    """
    logger.info("\n--- Testing AsyncPocketOptionClient (WebSocket Mode) ---")
    client = AsyncPocketOptionClient(
        TEST_SSID, is_demo=True, persistent_connection=False, auto_reconnect=True
    )

    # Register high-level event callbacks
    client.add_event_callback("connected", on_connected)
    client.add_event_callback("disconnected", on_disconnected)
    client.add_event_callback("authenticated", on_authenticated)
    client.add_event_callback("balance_updated", on_balance_updated)
    client.add_event_callback("order_opened", on_order_opened)
    client.add_event_callback("order_closed", on_order_closed)
    client.add_event_callback("stream_update", on_stream_update)
    client.add_event_callback("candles_received", on_candles_received)
    client.add_event_callback("message_received", on_message_received)
    client.add_event_callback("reconnected", on_reconnected)  # For auto_reconnect

    try:
        logger.info("Attempting to connect AsyncPocketOptionClient (WebSocket Mode)...")
        connected = await client.connect()
        logger.info(
            f"AsyncPocketOptionClient (WebSocket Mode) connected status: {connected}"
        )
        logger.info(f"Is client connected (property): {client.is_connected}")

        if connected:
            # Test get_balance
            logger.info("Testing get_balance...")
            balance = await client.get_balance()
            logger.info(f"Current Balance: {balance.balance:.2f} {balance.currency}")
            await asyncio.sleep(1)

            # Test place_order (will likely fail without real market conditions)
            logger.info(
                "Testing place_order (may fail without real market conditions)..."
            )
            try:
                order_result = await client.place_order(
                    asset="EURUSD_otc",
                    amount=1.0,
                    direction=OrderDirection.CALL,
                    duration=60,
                )
                logger.info(
                    f"Order Placement Attempt: {order_result.order_id}, Status: {order_result.status.value}"
                )
                # Wait for order result
                await asyncio.sleep(5)
                check_res = await client.check_win(
                    order_result.order_id, max_wait_time=10
                )
                logger.info(f"Order Check Result: {check_res}")

            except OrderError as oe:
                logger.warning(f"Order placement failed as expected in test: {oe}")
            except Exception as e:
                logger.error(f"Unexpected error during order placement: {e}")
            await asyncio.sleep(1)

            # Test get_candles
            logger.info("Testing get_candles...")
            try:
                candles = await client.get_candles(
                    asset="EURUSD_otc", timeframe="1m", count=5
                )
                logger.info(f"Received {len(candles)} candles for EURUSD_otc (1m)")
                if candles:
                    logger.debug(f"First candle: {candles[0]}")
            except PocketOptionError as pe:
                logger.warning(f"Failed to get candles: {pe}")
            await asyncio.sleep(1)

            # Test get_candles_dataframe
            logger.info("Testing get_candles_dataframe...")
            try:
                df_candles = await client.get_candles_dataframe(
                    asset="EURUSD_otc", timeframe="1m", count=5
                )
                logger.info(f"Received {len(df_candles)} candles as DataFrame.")
                if not df_candles.empty:
                    logger.debug(f"DataFrame head:\n{df_candles.head()}")
            except PocketOptionError as pe:
                logger.warning(f"Failed to get candles DataFrame: {pe}")
            await asyncio.sleep(1)

            # Test get_active_orders
            active_orders = await client.get_active_orders()
            logger.info(f"Active Orders: {len(active_orders)}")
            if active_orders:
                logger.debug(f"First active order: {active_orders[0].order_id}")
            await asyncio.sleep(1)

            # Test get_connection_stats
            stats = client.get_connection_stats()
            logger.info(
                f"AsyncPocketOptionClient (WebSocket Mode) Stats: {json.dumps(stats, indent=2, default=str)}"
            )
            await asyncio.sleep(1)

        else:
            logger.warning(
                "AsyncPocketOptionClient (WebSocket Mode) failed to connect. Skipping further tests."
            )

    except Exception as e:
        logger.error(
            f"Error during AsyncPocketOptionClient (WebSocket Mode) testing: {e}"
        )

    finally:
        logger.info(
            "Attempting to disconnect AsyncPocketOptionClient (WebSocket Mode)..."
        )
        await client.disconnect()
        logger.info("AsyncPocketOptionClient (WebSocket Mode) test finished.")
        await asyncio.sleep(1)


async def test_async_pocket_option_client_with_persistent_mode():
    """
    Tests the high-level AsyncPocketOptionClient in its persistent connection mode.
    """
    logger.info("\n--- Testing AsyncPocketOptionClient (Persistent Mode) ---")
    client = AsyncPocketOptionClient(
        TEST_SSID, is_demo=True, persistent_connection=True, auto_reconnect=True
    )

    # Register high-level event callbacks
    client.add_event_callback("connected", on_connected)
    client.add_event_callback("disconnected", on_disconnected)
    client.add_event_callback("authenticated", on_authenticated)
    client.add_event_callback("balance_updated", on_balance_updated)
    client.add_event_callback("order_opened", on_order_opened)
    client.add_event_callback("order_closed", on_order_closed)
    client.add_event_callback("stream_update", on_stream_update)
    client.add_event_callback("candles_received", on_candles_received)
    client.add_event_callback("message_received", on_message_received)
    client.add_event_callback("reconnected", on_reconnected)

    try:
        logger.info(
            "Attempting to connect AsyncPocketOptionClient (Persistent Mode)..."
        )
        connected = await client.connect()
        logger.info(
            f"AsyncPocketOptionClient (Persistent Mode) connected status: {connected}"
        )
        logger.info(f"Is client connected (property): {client.is_connected}")

        if connected:
            # Test get_balance
            logger.info("Testing get_balance...")
            balance = await client.get_balance()
            logger.info(f"Current Balance: {balance.balance:.2f} {balance.currency}")
            await asyncio.sleep(1)

            # Test place_order (will likely fail without real market conditions)
            logger.info(
                "Testing place_order (may fail without real market conditions)..."
            )
            try:
                order_result = await client.place_order(
                    asset="EURUSD_otc",
                    amount=1.0,
                    direction=OrderDirection.CALL,
                    duration=60,
                )
                logger.info(
                    f"Order Placement Attempt: {order_result.order_id}, Status: {order_result.status.value}"
                )
                await asyncio.sleep(5)
                check_res = await client.check_win(
                    order_result.order_id, max_wait_time=10
                )
                logger.info(f"Order Check Result: {check_res}")
            except OrderError as oe:
                logger.warning(f"Order placement failed as expected in test: {oe}")
            except Exception as e:
                logger.error(f"Unexpected error during order placement: {e}")
            await asyncio.sleep(1)

            # Test get_candles
            logger.info("Testing get_candles...")
            try:
                candles = await client.get_candles(
                    asset="EURUSD_otc", timeframe="1m", count=5
                )
                logger.info(f"Received {len(candles)} candles for EURUSD_otc (1m)")
            except PocketOptionError as pe:
                logger.warning(f"Failed to get candles: {pe}")
            await asyncio.sleep(1)

            # Test get_connection_stats
            stats = client.get_connection_stats()
            logger.info(
                f"AsyncPocketOptionClient (Persistent Mode) Stats: {json.dumps(stats, indent=2, default=str)}"
            )
            await asyncio.sleep(1)

            # Allow persistent connection to run for a bit
            logger.info("Allowing persistent client to run for 10 seconds...")
            await asyncio.sleep(10)

        else:
            logger.warning(
                "AsyncPocketOptionClient (Persistent Mode) failed to connect. Skipping further tests."
            )

    except Exception as e:
        logger.error(
            f"Error during AsyncPocketOptionClient (Persistent Mode) testing: {e}"
        )

    finally:
        logger.info(
            "Attempting to disconnect AsyncPocketOptionClient (Persistent Mode)..."
        )
        await client.disconnect()
        logger.info("AsyncPocketOptionClient (Persistent Mode) test finished.")
        await asyncio.sleep(1)


async def main():
    """Main function to run all tests."""
    logger.info("Starting comprehensive WebSocket functionality test...")

    # Run tests for AsyncWebSocketClient
    await test_async_websocket_client()

    # Run tests for ConnectionKeepAlive
    await test_connection_keep_alive()

    # Run tests for AsyncPocketOptionClient in WebSocket mode
    await test_async_pocket_option_client_with_websocket_mode()

    # Run tests for AsyncPocketOptionClient in Persistent mode
    await test_async_pocket_option_client_with_persistent_mode()

    logger.info("\nAll WebSocket functionality tests completed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Test interrupted by user.")
    except Exception as e:
        logger.critical(f"An unhandled error occurred during tests: {e}")
