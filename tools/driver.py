import os
import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options as ChromeOptions
from webdriver_manager.chrome import (
    ChromeDriverManager,
)  # Automatically downloads and manages ChromeDriver.

# Configure logging for this module to provide clear output.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s",
)
logger = logging.getLogger(__name__)


def get_driver(browser_name: str = "chrome"):
    """
    Initializes and returns a Selenium WebDriver instance for the specified browser.
    Automatically handles driver downloads and configuration, and allows for persistent sessions
    by storing browser profiles.

    Args:
        browser_name: The name of the browser to use. Defaults to 'chrome'.

    Returns:
        A configured Selenium WebDriver instance.

    Raises:
        ValueError: If an unsupported browser name is provided.
    """
    # Define a base directory for storing browser profiles to maintain cookies, sessions, and logins.
    # This allows for persistent sessions across multiple script runs.
    base_profile_dir = os.path.join(os.getcwd(), "browser_profiles")
    os.makedirs(base_profile_dir, exist_ok=True)

    if browser_name.lower() == "chrome":
        chrome_options = ChromeOptions()

        # Define the path for the Chrome user data directory. Using a persistent directory
        # allows Selenium to remember cookies, cache, and login sessions.
        user_data_dir = os.path.join(base_profile_dir, "chrome_profile")
        chrome_options.add_argument(f"--user-data-dir={user_data_dir}")

        # Add various arguments to optimize browser operation for automation.
        chrome_options.add_argument(
            "--disable-gpu"
        )  # Disable GPU hardware acceleration, which can cause issues in some environments.
        chrome_options.add_argument(
            "--no-sandbox"
        )  # Bypass OS security model; necessary for running as root in Docker/Linux.
        chrome_options.add_argument(
            "--disable-dev-shm-usage"
        )  # Overcome limited resource problems in Docker and certain CI/CD environments.
        chrome_options.add_argument(
            "--window-size=1920,1080"
        )  # Set a consistent window size for predictable rendering.
        chrome_options.add_argument("--start-maximized")  # Start the browser maximized.
        chrome_options.add_argument(
            "--log-level=3"
        )  # Suppress excessive console logging from Chrome itself.
        chrome_options.add_experimental_option("excludeSwitches", ["enable-logging"])

        # Enable performance logging to capture network events, which can be useful for
        # monitoring network traffic or waiting for specific resources to load.
        chrome_options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

        logger.info("Initializing Chrome WebDriver...")
        try:
            # Use ChromeDriverManager to automatically download and manage the appropriate ChromeDriver.
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            logger.info("Chrome WebDriver initialized successfully.")
            return driver
        except Exception as e:
            logger.error(f"Error initializing Chrome WebDriver: {e}")
            raise
    else:
        raise ValueError(f"Unsupported browser: {browser_name}.")
