import os
import time
import re
import logging
import argparse
from typing import Callable, Optional, List

from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, WebDriverException

from driver import get_driver

# Section: Logging Configuration
# ------------------------------
# Configure logging to output structured JSON for better traceability and analysis.
logging.basicConfig(
    level=logging.INFO,  # Set the minimum level of messages to log.
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "module": "%(name)s", "message": "%(message)s"}',
    datefmt="%Y-%m-%d %H:%M:%S",  # Define the format for the timestamp.
)
logger = logging.getLogger(__name__)  # Get a logger instance for this module.


# Section: Helper Functions
# -------------------------


def save_to_env(key: str, value: str) -> None:
    """
    Saves or updates a key-value pair in a .env file in the current directory.

    Args:
        key (str): The environment variable key (e.g., "SSID").
        value (str): The value to associate with the key.
    """
    env_file_path = ".env"  # Define the path to the .env file.
    env_content = {}  # Initialize a dictionary to hold environment variables.

    # Read existing .env file if it exists.
    if os.path.exists(env_file_path):
        with open(env_file_path, "r") as f:  # Open the file for reading.
            for line in f:  # Iterate over each line.
                line = line.strip()  # Remove whitespace.
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)  # Split into key and value.
                    env_content[k.strip()] = v.strip()  # Store in dictionary.

    # Update or add the new key-value pair.
    env_content[key] = value

    # Write the updated contents back to the .env file.
    with open(env_file_path, "w") as f:  # Open the file for writing.
        for k, v in env_content.items():  # Iterate over the dictionary items.
            f.write(f"{k}={v}\n")  # Write each key-value pair.
    logger.info(f"Key '{key}' was successfully saved to {env_file_path}.")


def url_contains_safe(url: str) -> Callable[[WebDriver], bool]:
    """
    Creates a Selenium expected condition to safely check if the current URL contains a substring.

    Args:
        url (str): The substring to look for in the browser's current URL.

    Returns:
        Callable[[WebDriver], bool]: A predicate function for use with WebDriverWait.
    """

    def _predicate(driver: WebDriver) -> bool:
        """The actual predicate function that WebDriverWait will execute."""
        try:
            # Safely access the current URL.
            current_url = driver.current_url
            # Return False if URL is None (e.g., window closed), otherwise check.
            return current_url is not None and url in current_url
        except WebDriverException:
            # Handle cases where the driver or window is no longer available.
            return False

    return _predicate


# Section: Core Logic
# -------------------


def get_pocketoption_ssid(
    browser: str, login_timeout: int, wait_after_navigate: int
) -> None:
    """
    Automates capturing the full PocketOption auth message using JavaScript injection.

    Args:
        browser (str): The browser to use (e.g., "chrome").
        login_timeout (int): The max time in seconds to wait for manual login.
        wait_after_navigate (int): The time in seconds to wait for WebSockets to communicate.
    """
    driver: Optional[WebDriver] = None  # Initialize driver for the finally block.
    try:
        # Initialize the Selenium WebDriver.
        logger.info("Initializing WebDriver...")
        driver = get_driver(browser, message="auth")
        login_url = "https://pocketoption.com/en/login/"  # The explicit login page URL.
        cabinet_base_url = (
            "https://pocketoption.com/en/cabinet"  # The base URL of the user cabinet.
        )

        logger.info(f"Navigating to PocketOption login page: {login_url}")
        driver.get(login_url)  # Open the login page.

        logger.info(f"Please log in manually. Waiting up to {login_timeout} seconds...")

        # Wait for the user to log in and be redirected to the cabinet.
        WebDriverWait(driver, login_timeout).until(url_contains_safe(cabinet_base_url))
        logger.info("Login successful! Preparing to intercept WebSocket traffic.")

        # This JavaScript hooks into the WebSocket 'send' method to capture outgoing messages.
        # It stores any message containing 'auth' in a global array.
        interception_script = """
            window.ws_messages = window.ws_messages || [];
            if (!WebSocket.prototype._original_send) {
                WebSocket.prototype._original_send = WebSocket.prototype.send;
                WebSocket.prototype.send = function(data) {
                    if (typeof data === 'string' && data.includes('auth')) {
                        window.ws_messages.push(data);
                    }
                    WebSocket.prototype._original_send.apply(this, arguments);
                };
            }
        """
        # Inject the script *before* navigating to the page that sends the auth message.
        # This prevents a race condition.
        driver.execute_script(interception_script)
        logger.info("Injected WebSocket interception script.")

        # Navigate to a page known to trigger the required WebSocket authentication.
        target_url = "https://pocketoption.com/en/cabinet/quick-high-low/"  # https://pocketoption.com/en/cabinet/demo-quick-high-low/
        logger.info(f"Navigating to: {target_url}")
        driver.get(target_url)

        # Wait for a few seconds to ensure WebSocket frames are sent and captured.
        logger.info(f"Waiting {wait_after_navigate} seconds for WebSocket traffic...")
        time.sleep(wait_after_navigate)

        # Retrieve the captured WebSocket messages.
        logger.info("Retrieving captured WebSocket messages...")
        retrieval_script = "return window.ws_messages || [];"
        messages: List[str] = driver.execute_script(retrieval_script)

        # This regex is more flexible, matching the required '42["auth",{...}]' structure
        # while allowing for other keys in the object.
        ssid_pattern = re.compile(r'(42\["auth",\{.*?"session":".*?.*\}\])')
        found_ssid_string = None  # Initialize variable to store the found string.

        if not messages:
            logger.warning(
                "No WebSocket 'auth' messages were captured. The site may have changed."
            )
        else:
            logger.info(
                f"Captured {len(messages)} potential auth messages. Searching for exact pattern..."
            )
            # Iterate through captured messages to find the one matching the regex.
            for i, msg in enumerate(messages):
                match = ssid_pattern.search(msg)  # Search for the auth pattern.
                if match:
                    found_ssid_string = match.group(
                        1
                    )  # Capture the full matched string.
                    logger.info(
                        f"Successfully found the target auth string: {found_ssid_string}"
                    )
                    break  # Exit the loop once the target is found.
                else:
                    # Log messages that were captured but didn't match for debugging.
                    logger.warning(
                        f"Message {i + 1} did not match pattern. Content: {msg}"
                    )

        # After checking all messages, save the result if it was found.
        if found_ssid_string:
            save_to_env("SSID", found_ssid_string)  # Save the complete string.
        else:
            logger.warning(
                "Could not find the target SSID auth string in the captured traffic."
            )

    except TimeoutException:
        # Handle login timeout.
        logger.error(
            f"Login timeout of {login_timeout} seconds exceeded. Navigation to cabinet failed."
        )
    except Exception as e:
        # Log any other exceptions.
        logger.error(f"An unexpected error occurred: {e}", exc_info=True)
    finally:
        # Ensure the WebDriver is always closed.
        if driver:
            driver.quit()
            logger.info("WebDriver has been closed.")


# Section: Main Execution Block
# -----------------------------


def main() -> None:
    """
    Parses command-line arguments and executes the SSID extraction script.
    """
    # Initialize the argument parser.
    parser = argparse.ArgumentParser(
        description="Extract the full SSID auth message from PocketOption and save it to a .env file."
    )
    # Add command-line arguments.
    parser.add_argument(
        "--browser",
        type=str,
        default="chrome",
        help="Browser to use (e.g., 'chrome', 'firefox'). Default: chrome.",
    )
    parser.add_argument(
        "--login-timeout",
        type=int,
        default=600,
        help="Maximum time in seconds to wait for manual login. Default: 600.",
    )
    parser.add_argument(
        "--wait-after-navigate",
        type=int,
        default=7,
        help="Time in seconds to wait after navigation for WebSockets to communicate. Default: 7.",
    )
    # Parse the arguments.
    args = parser.parse_args()

    # Call the core function with the parsed arguments.
    get_pocketoption_ssid(
        browser=args.browser,
        login_timeout=args.login_timeout,
        wait_after_navigate=args.wait_after_navigate,
    )


# Entry point for direct execution.
if __name__ == "__main__":
    main()
