"""Local HTTP server for Teller Connect enrollment flow."""

from __future__ import annotations

import asyncio
import json
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from typing import Any


TEMPLATE_DIR = Path(__file__).parent / "templates"


class EnrollmentResult:
    """Holds the result of an enrollment attempt."""

    def __init__(self) -> None:
        self.enrollment: dict | None = None
        self.error: str | None = None
        self.event: asyncio.Event | None = None
        self.loop: asyncio.AbstractEventLoop | None = None


class _EnrollmentHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the enrollment flow."""

    result: EnrollmentResult
    application_id: str

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "":
            template = (TEMPLATE_DIR / "connect.html").read_text()
            html = template.replace("{{APPLICATION_ID}}", self.application_id)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/callback":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                self.result.enrollment = json.loads(body)
            except json.JSONDecodeError as e:
                self.result.error = f"Invalid enrollment data: {e}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
            # Signal completion via the event loop
            if self.result.loop and self.result.event:
                self.result.loop.call_soon_threadsafe(self.result.event.set)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress default logging


async def run_enrollment(
    application_id: str, port: int = 8765, timeout: int = 300
) -> dict:
    """Run the enrollment flow. Opens browser, waits for callback.

    Returns the enrollment data dict with keys:
        accessToken, enrollment (with id), institution (with name), etc.
    """
    result = EnrollmentResult()
    result.loop = asyncio.get_running_loop()
    result.event = asyncio.Event()

    handler_class = type(
        "_Handler",
        (_EnrollmentHandler,),
        {"result": result, "application_id": application_id},
    )

    server = HTTPServer(("127.0.0.1", port), handler_class)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://localhost:{port}"
    webbrowser.open(url)

    try:
        await asyncio.wait_for(result.event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"Enrollment timed out after {timeout}s. "
            f"If your browser didn't open, visit: {url}"
        )
    finally:
        server.shutdown()

    if result.error:
        raise RuntimeError(result.error)
    if not result.enrollment:
        raise RuntimeError("Enrollment completed without data")

    return result.enrollment
