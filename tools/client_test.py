"""
Example Async WebSocket client for PocketOption API.
This script demonstrates how to establish a WebSocket connection to PocketOption,
handle the initial handshake, send authentication, and process basic incoming messages.
It utilizes the region URLs and default headers (including a random user agent)
defined in the `constants` module.
"""

import socketio
import anyio
from rich.pretty import pprint as print
from pocketoptionapi_async.constants import REGIONS, DEFAULT_HEADERS

SESSION = r'42["auth",{"session":"a:4:{s:10:\"session_id\";s:32:\"a1dc009a7f1f0c8267d940d0a036156f\";s:10:\"ip_address\";s:12:\"1.1.1.1\";s:10:\"user_agent\";s:120:\"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OP\";s:13:\"last_activity\";i:1709914958;}793884e7bccc89ec798c06ef1279fcf2","isDemo":0,"uid":27658142,"platform":1}]'


async def websocket_client(pro_callback):
    """
    Connects to PocketOption WebSocket using available region URLs and
    passes incoming messages to a processing callback.

    Args:
        pro_callback: An asynchronous callback function to process received messages.
                      It should accept (message, websocket, connected_url) as arguments.
    """
    # Get a list of all region URLs, which are randomized by default in REGIONS.get_all().
    region_urls = REGIONS.get_all()
    for url in region_urls:
        print(f"Trying to connect to {url}...")
        try:
            sio = socketio.AsyncClient()

            @sio.event
            async def message(data):
                await pro_callback(data, sio, url)

            await sio.connect(url, headers=DEFAULT_HEADERS)
            print(f"Successfully connected to {url}. Listening for messages...")

            await sio.wait()
        except KeyboardInterrupt:
            print("Exiting due to KeyboardInterrupt.")
            exit()
        except Exception as e:
            print(f"Connection to {url} lost or failed: {e}. Trying next region...")
    return True


async def pro(message, websocket, connected_url):
    """
    Processes incoming WebSocket messages from PocketOption.
    This function handles initial handshake messages and sends the session ID.

    Args:
        message: The raw message received from the WebSocket (can be bytes or str).
        websocket: The active WebSocketClientProtocol object.
        connected_url: The URL of the currently connected WebSocket server.
    """
    # Decode byte messages for printing, cutting long ones to prevent spam.
    if isinstance(message, bytes):
        print(f"Binary message (first 100 chars): {str(message)[:100]}")
        return
    else:
        # Print string messages.
        print(f"String message: {message}")

    # --- Handshake and Authentication Logic ---
    # These messages are part of the Socket.IO handshake protocol.
    # The `websocket.host` is used for clearer logging of the connected server.

    # Server sends "0" message with session ID. Client responds with "40".
    if message.startswith('0{"sid":"'):
        print(f"{websocket.host} received initial '0' message, sending '40' response.")
        await websocket.send("40")
    # Server sends "2" message (ping). Client responds with "3" (pong).
    elif message == "2":
        print(f"{websocket.host} received '2' (ping), sending '3' (pong).")
        await websocket.send("3")
    # Server sends "40" message with session ID (connection confirmed). Client sends authentication SESSION.
    elif message.startswith('40{"sid":"'):
        print(
            f"{websocket.host} received '40' (connection confirmed), sending authentication SESSION."
        )
        await websocket.send(SESSION)
        print(f"Authentication SESSION sent to {websocket.host}.")
    # Optional: Handle successful authentication confirmation from the server.
    # The server might send a '42["successauth",...]' message after successful login.
    elif message.startswith('42["successauth"'):
        print(f"Authentication successful with {websocket.host}!")
    # Example for sending an order (uncomment and modify to use)
    # data = r'42["openOrder",{"asset":"#AXP_otc","amount":1,"action":"call","isDemo":1,"requestId":14680035,"optionType":100,"time":20}]'
    # await websocket.send(data)


async def main():
    """
    Main function to start the WebSocket client.
    It calls `websocket_client` with the `pro` callback to handle messages.
    """
    # The 'url' variable here is not directly used for connection,
    # as `websocket_client` iterates through `REGIONS.get_all()`.
    # It's kept for consistency with the original structure.
    await websocket_client(pro)


if __name__ == "__main__":
    # Run the main asynchronous function using anyio.
    anyio.run(main)
