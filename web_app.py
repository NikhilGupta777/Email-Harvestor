# web_app.py
import asyncio
import logging
import argparse # Keep import in case needed elsewhere, but not for /run endpoint parsing
import shlex # Keep import in case needed elsewhere
import sys
import io # Keep import in case needed elsewhere
import os # Needed for path joining
from contextlib import redirect_stdout, redirect_stderr

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState
from pydantic import BaseModel, Field, validator # Import for structured request body
from typing import Optional, Literal

# --- Assume your project structure allows these imports ---
# Add the parent directory to sys.path if running web_app.py directly from root
parent_dir = os.path.dirname(os.path.abspath(__file__))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

try:
    # Ensure logging_setup is called early to configure the root logger
    from utils import config, logging_setup, helpers
    from core.crawler import Crawler
    from core.storage import EmailStorage
    # Call logging setup here to configure default handlers before we modify them
    logging_setup.setup_logging()
except ImportError as e:
    print(f"Error importing project modules: {e}")
    print("Ensure web_app.py is in the EMAIL_HARVESTER project root or adjust sys.path.")
    sys.exit(1)
# --- End Project Imports ---


# --- WebSocket Logging Handler ---
class WebSocketLogHandler(logging.Handler):
    """Custom logging handler to send logs over a WebSocket."""
    def __init__(self, websocket: WebSocket):
        super().__init__()
        self.websocket = websocket
        # Get the loop when the handler is created, assuming it's within an async context
        try:
             self._loop = asyncio.get_running_loop()
        except RuntimeError:
             # Fallback if not in a running loop (less likely in FastAPI/uvicorn)
             self._loop = asyncio.get_event_loop()


    async def _send_log(self, record):
        """Asynchronously send log message."""
        log_entry = self.format(record)
        # Double check state before sending
        if self.websocket and self.websocket.client_state == WebSocketState.CONNECTED:
            try:
                await self.websocket.send_text(f"[LOG] {log_entry}")
            except WebSocketDisconnect:
                # Handle disconnection if it happens during send
                print(f"WebSocket disconnected for {self.websocket.client} during log send.")
                self.websocket = None # Invalidate handler's websocket
            except Exception as e:
                # Log the error to the server console, not back to the client via ws
                print(f"Error sending log over WebSocket {self.websocket.client}: {e}")
                self.websocket = None # Invalidate handler's websocket


    def emit(self, record):
        """Emit a record."""
        # Schedule the async send operation in the event loop
        # This prevents blocking the logging call
        if self.websocket and self.websocket.client_state == WebSocketState.CONNECTED:
            # run_coroutine_threadsafe is used to submit the coroutine to the event loop
            # from potentially a different thread (logging calls might not be in the main loop)
            try:
                 asyncio.run_coroutine_threadsafe(self._send_log(record), self._loop)
            except Exception as e:
                 print(f"Error scheduling log send for {self.websocket.client}: {e}")
                 self.websocket = None # Invalidate handler's websocket if scheduling fails


# --- Refactored Harvester Logic ---
async def run_harvester(args_dict: dict, websocket: WebSocket):
    """
    Runs the core email harvesting logic, accepting args as a dict
    and sending output over the WebSocket.
    """
    ws_handler = None
    # Get the logger instance for run_harvester *before* potentially modifying root logger
    logger = logging.getLogger(__name__)

    # Get the root logger to add/remove handlers
    root_logger = logging.getLogger()
    original_level = root_logger.level # Store original level
    added_handlers = [] # Track handlers added by this task

    try:
        # --- Setup WebSocket Logging ---
        ws_handler = WebSocketLogHandler(websocket)
        ws_formatter = logging.Formatter(config.LOG_FORMAT, datefmt=config.LOG_DATE_FORMAT)
        ws_handler.setFormatter(ws_formatter)

        # Determine log level from args or default
        log_level = logging.DEBUG if args_dict.get('verbose', False) else config.DEFAULT_LOG_LEVEL
        ws_handler.setLevel(log_level)
        # Temporarily increase root logger level if task is less verbose than default
        # Or set to task's requested level
        root_logger.setLevel(min(root_logger.level, log_level))


        # Add WebSocket handler
        root_logger.addHandler(ws_handler)
        added_handlers.append(ws_handler)

        # Optionally add a temporary console handler if root didn't have one, or adjust its level
        # Simpler: just ensure root logger level is appropriate and existing console handlers work
        # If logging_setup is called, there should be a console handler already.
        # We'll rely on that, just adjusting the root level temporarily.
        # For this task's logging, we use the logger *of this module* (`logger`), which
        # inherits settings from the root logger, including our new ws_handler.


        # --- Argument Validation/Defaults ---
        # These should align with the Pydantic model
        start_url = args_dict.get('start_url')
        if not start_url: # This check is also done by Pydantic now, but keep for safety
             await websocket.send_text("[ERROR] Start URL is required.")
             return

        output_file = args_dict.get('output', config.DEFAULT_OUTPUT_FILENAME) # Note: Saving server-side disabled
        max_depth = args_dict.get('depth', config.DEFAULT_MAX_DEPTH)
        max_pages = args_dict.get('max_pages', config.DEFAULT_MAX_PAGES)
        rate_limit = args_dict.get('rate_limit', config.DEFAULT_RATE_LIMIT) # Corrected key name
        scope = args_dict.get('scope', config.DEFAULT_CRAWL_SCOPE)
        user_agent = args_dict.get('user_agent', config.DEFAULT_USER_AGENT)
        concurrency = args_dict.get('concurrency', 10)

        # Validate and normalize start URL
        if not start_url.startswith(('http://', 'https://')):
            logger.warning(f"Start URL '{start_url}' lacks scheme, prepending https://")
            start_url = f"https://{start_url}"

        normalized_start_url = helpers.normalize_url(start_url)
        if not normalized_start_url or not helpers.get_domain(normalized_start_url):
            await websocket.send_text(f"[ERROR] Invalid or could not normalize the starting URL: {start_url}")
            return

        logger.info("--- Email Harvester Initializing (Web Mode) ---")
        logger.info(f"Start URL: {normalized_start_url}")
        logger.info(f"Max Depth: {max_depth}")
        logger.info(f"Max Pages: {max_pages}")
        logger.info(f"Rate Limit: {rate_limit} req/s")
        logger.info(f"Scope: {scope}")
        logger.info(f"Concurrency: {concurrency}")
        logger.info(f"Output File: {output_file}") # Note: Saving might be less useful in web mode

        # --- Initialize Core Components ---
        storage = EmailStorage()
        crawler = Crawler(
            start_url=normalized_start_url,
            storage=storage,
            max_depth=max_depth,
            max_pages=max_pages,
            rate_limit=rate_limit,
            scope=scope,
            user_agent=user_agent,
            concurrency=concurrency
        )

        # --- Run Crawler ---
        # Capture print statements specifically if needed (logs are better)
        # string_io = io.StringIO()
        # with redirect_stdout(string_io), redirect_stderr(string_io):

        await crawler.crawl() # Run the async crawl

        # --- Process Results ---
        logger.info("Crawl finished. Processing results...")
        found_emails = storage.get_emails() # Synchronous call
        final_email_list = sorted(list(found_emails))
        final_email_count = len(final_email_list)

        await websocket.send_text("\n--- Found Emails ---")
        if final_email_count > 0:
            for email in final_email_list:
                await websocket.send_text(email)
            await websocket.send_text(f"--- End of {final_email_count} emails ---")
            # Optionally save to file on the server (consider security/permissions)
            # try:
            #     storage.save_to_file(output_file)
            #     logger.info(f"Results also saved to server file: {output_file}")
            # except Exception as save_e:
            #     logger.error(f"Failed to save results to server file: {save_e}")
        else:
            await websocket.send_text("No emails found.")

        logger.info("--- Email Harvester Finished (Web Mode) ---")
        # captured_output = string_io.getvalue() # Get captured prints if used
        # if captured_output:
        #     await websocket.send_text("\n--- Captured Output ---")
        #     await websocket.send_text(captured_output)


    except WebSocketDisconnect:
         logger.warning("WebSocket disconnected during crawl execution.")
         # No need to send message back to client as they are disconnected
    except KeyboardInterrupt:
        logger.warning("Crawling interrupted (likely server shutdown).")
        # Check if websocket is still connected before sending
        if websocket.client_state == WebSocketState.CONNECTED:
             await websocket.send_text("[WARN] Crawling interrupted.")
    except ValueError as ve:
        logger.critical(f"Initialization Error: {ve}", exc_info=True) # Log to server console
        if websocket.client_state == WebSocketState.CONNECTED:
             await websocket.send_text(f"[ERROR] Initialization Error: {ve}")
    except Exception as e:
        logger.critical(f"An unexpected critical error occurred during crawl: {e}", exc_info=True) # Log to server console
        if websocket.client_state == WebSocketState.CONNECTED:
             await websocket.send_text(f"[CRITICAL] An unexpected error occurred: {e}")
    finally:
        # --- Restore Original Logging ---
        # Remove only the handlers we added
        for handler in added_handlers:
             if handler in root_logger.handlers:
                 root_logger.removeHandler(handler)
        # Restore original level if it was changed (optional, can leave it)
        root_logger.setLevel(original_level)

        # Ensure WebSocket is closed gracefully if still connected
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                 await websocket.send_text("[INFO] Task finished.")
                 # Optional: await websocket.close() # Or let the client disconnect
            except WebSocketDisconnect:
                 pass # Already disconnected
            except Exception as close_e:
                 print(f"Error sending final message/closing WebSocket: {close_e}")


# --- FastAPI App Setup ---
app = FastAPI()

# Serve static files (like index.html, css, js) if needed
# Make sure you have a 'static' directory at your project root if uncommenting
# app.mount("/static", StaticFiles(directory="static"), name="static")


# In-memory store for active WebSocket connections (simple approach)
# Use a dictionary mapping client_id (str) to WebSocket
active_connections: dict[str, WebSocket] = {}

@app.get("/", response_class=HTMLResponse)
async def get_index(request: Request):
    # Serve the index.html file from the 'templates' directory
    index_path = os.path.join(parent_dir, "templates", "index.html")
    try:
        with open(index_path, "r") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        # Log this error on the server side
        logging.error(f"index.html not found at {index_path}")
        return HTMLResponse(content="<h1>Error: index.html not found</h1>", status_code=404)

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """Handles WebSocket connections for log streaming and results."""
    await websocket.accept()
    # Check if client_id is already in use (simple handling, might need more robust logic)
    if client_id in active_connections and active_connections[client_id].client_state == WebSocketState.CONNECTED:
         print(f"Warning: Client ID {client_id} already has an active connection. Closing old one.")
         try:
             await active_connections[client_id].close(code=1008) # Close with a policy violation code
         except Exception:
             pass # Ignore errors if closing fails
         del active_connections[client_id]


    active_connections[client_id] = websocket
    print(f"WebSocket connection established for client: {client_id}")

    try:
        # Keep the connection alive. We don't expect messages FROM the client
        # in this setup, but we need to wait for the connection to close.
        # The receive_text() loop is removed. The connection stays open as long as
        # both ends don't close it and asyncio manages it. The exception handling
        # will catch the disconnect.
        while True:
            # This will block until a message is received or the connection is closed.
            # If no messages are expected from the client, this loop might hang
            # indefinitely or raise errors if the client sends unexpected data.
            # A better approach might be to rely purely on the except WebSocketDisconnect.
            # However, keeping a minimal receive loop can help detect connection issues sooner.
            # Let's simplify and just rely on disconnect for now.
             await websocket.receive_text() # This line is key to keeping the async task alive

    except WebSocketDisconnect:
        print(f"WebSocket connection closed for client: {client_id}")
    except Exception as e:
        print(f"WebSocket error for client {client_id}: {e}")
    finally:
        # Ensure cleanup happens regardless of how the connection closes
        if client_id in active_connections and active_connections[client_id] == websocket:
            del active_connections[client_id]
            print(f"Cleaned up WebSocket connection for client: {client_id}")


# --- Request Body Model for /run endpoint ---
class RunHarvesterRequest(BaseModel):
    client_id: str = Field(..., description="Unique identifier for the client WebSocket connection.")
    start_url: str = Field(..., description="The starting URL for the crawl.")
    output: Optional[str] = Field(config.DEFAULT_OUTPUT_FILENAME, description="Output filename (server-side, saving disabled by default).")
    depth: Optional[int] = Field(config.DEFAULT_MAX_DEPTH, description="Maximum crawl depth.")
    max_pages: Optional[int] = Field(config.DEFAULT_MAX_PAGES, description="Maximum number of pages to crawl.")
    rate_limit: Optional[float] = Field(config.DEFAULT_RATE_LIMIT, description="Maximum requests per second.")
    scope: Optional[Literal['domain', 'subdomain']] = Field(config.DEFAULT_CRAWL_SCOPE, description="Crawl scope: 'domain' or 'subdomain'.")
    user_agent: Optional[str] = Field(config.DEFAULT_USER_AGENT, description="User-Agent string.")
    concurrency: Optional[int] = Field(10, description="Number of concurrent requests.")
    verbose: Optional[bool] = Field(False, description="Enable verbose logging.")

    @validator('start_url')
    def start_url_must_be_valid(cls, v):
        # Basic validation, more detailed validation happens in run_harvester
        if not v or not isinstance(v, str):
            raise ValueError('Start URL must be a non-empty string')
        # Add more sophisticated URL validation if needed (e.g., using urllib.parse)
        return v

    @validator('depth', 'max_pages', 'concurrency')
    def must_be_positive(cls, v):
        if v is not None and v < 0:
             raise ValueError('must be a non-negative number')
        return v

    @validator('rate_limit')
    def rate_limit_must_be_positive(cls, v):
        if v is not None and v <= 0:
             raise ValueError('must be a positive number')
        return v


@app.post("/run")
async def run_command(request_data: RunHarvesterRequest):
    """Receives harvest parameters, validates them, and starts the harvester task."""

    client_id = request_data.client_id

    websocket = active_connections.get(client_id)
    if not websocket or websocket.client_state != WebSocketState.CONNECTED:
        # Return HTTP error if websocket is not found or not connected
        raise HTTPException(status_code=400, detail=f"No active WebSocket found for client_id: {client_id}")

    # Convert the Pydantic model data to a dictionary format expected by run_harvester
    # Use .model_dump() for Pydantic v2+, or .dict() for Pydantic v1
    try:
        args_dict = request_data.model_dump() # Pydantic v2+
    except AttributeError:
        args_dict = request_data.dict() # Pydantic v1

    # Remove client_id from args_dict as it's not a crawler argument
    args_dict.pop('client_id', None)


    try:
        # Run the harvester logic in the background
        # asyncio.create_task is preferred over asyncio.run_coroutine_threadsafe
        # for starting new top-level tasks *within* an existing asyncio event loop
        asyncio.create_task(run_harvester(args_dict, websocket))

        return {"status": "ok", "message": "Harvester task started."}

    except Exception as e:
        # This catch block is mainly for errors *before* asyncio.create_task succeeds
        # Errors inside run_harvester are handled within run_harvester's try/except
        logging.error(f"Failed to start harvester task for client {client_id}: {e}", exc_info=True)
        # Attempt to send error to client if WS is still open, but don't block response
        if websocket.client_state == WebSocketState.CONNECTED:
             asyncio.create_task(websocket.send_text(f"[ERROR] Failed to start task: {e}"))
        raise HTTPException(status_code=500, detail=f"Failed to start harvester task: {e}")


# --- Run Server (for local testing) ---
if __name__ == "__main__":
    import uvicorn
    # Ensure platform-specific asyncio setup if needed (e.g., Windows policies)
    if sys.platform == "win32":
        # Use the ProactorEventLoopPolicy on Windows for compatibility with async operations like files/sockets
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy()) # Or WindowsProactorEventLoopPolicy() if needed for specific async file ops


    print("Starting FastAPI server...")
    print("Access the web interface at http://127.0.0.1:8000")
    # Use reload=True for development, remove for production
    uvicorn.run("web_app:app", host="127.0.0.1", port=8000, reload=True)
