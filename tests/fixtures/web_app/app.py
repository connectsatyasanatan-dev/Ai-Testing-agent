"""
tests.fixtures.web_app.app
==========================
Deterministic web application fixture for Playwright adapter tests (P2-00).

Provides HTML pages and API routes for:
  - Login form (username, password, submit button, error messages)
  - Post-login dashboard with heading and navigation
  - Navigation element with multiple links (/dashboard, /settings, /login)
  - Element with known stable text value ("Welcome to Test Portal")
  - Conditionally visible element toggled via button click
  - Form inputs: text fields, password fields, select dropdowns
  - Action button with client-side feedback
  - SSRF test redirects (including redirect to AWS metadata 169.254.169.254)
  - Slow loading route for timeout enforcement
  - Popup window triggers for popup limit tests
"""
from __future__ import annotations

import asyncio
import socket
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Login - Test Portal</title>
    <style>
        body { font-family: sans-serif; margin: 2rem; }
        .error { color: #d32f2f; margin-bottom: 1rem; }
    </style>
</head>
<body>
    <h1 id="login-title">Sign In</h1>
    {error_html}
    <form id="login-form" method="POST" action="/login">
        <div>
            <label for="username">Username</label>
            <input type="text" id="username" name="username" placeholder="Enter username" />
        </div>
        <div style="margin-top: 1rem;">
            <label for="password">Password</label>
            <input type="password" id="password" name="password" placeholder="Enter password" />
        </div>
        <div style="margin-top: 1rem;">
            <button type="submit" id="login-btn">Log In</button>
        </div>
    </form>
</body>
</html>
"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Dashboard - Test Portal</title>
    <style>
        body { font-family: sans-serif; margin: 2rem; }
        nav a { margin-right: 1rem; }
        .box { border: 1px solid #ccc; padding: 1rem; margin-top: 1rem; }
    </style>
</head>
<body>
    <h1 id="page-title">Dashboard</h1>
    <nav id="main-nav">
        <a href="/dashboard" id="nav-dashboard">Dashboard</a>
        <a href="/settings" id="nav-settings">Settings</a>
        <a href="/login" id="nav-logout">Log Out</a>
    </nav>
    <div class="box">
        <div id="welcome-message">Welcome to Test Portal</div>
        <p id="user-display">Logged in as: <strong>{username}</strong></p>
    </div>

    <div class="box">
        <h3>Visibility Controls</h3>
        <button id="toggle-btn" type="button" onclick="
            var el = document.getElementById('cond-element');
            if (el.style.display === 'none') {
                el.style.display = 'block';
            } else {
                el.style.display = 'none';
            }
        ">Toggle Details</button>
        <div id="cond-element" style="display: none; margin-top: 0.5rem; color: #2e7d32;">
            Conditionally Visible Content
        </div>
    </div>

    <div class="box">
        <h3>User Preferences</h3>
        <label for="role-select">Role:</label>
        <select id="role-select" name="role">
            <option value="viewer">Viewer</option>
            <option value="editor">Editor</option>
            <option value="admin">Admin</option>
        </select>

        <div style="margin-top: 1rem;">
            <button id="action-btn" type="button" onclick="
                var f = document.getElementById('feedback-message');
                f.innerText = 'Action Executed Successfully';
                f.style.display = 'block';
            ">Execute Action</button>
            <div id="feedback-message" style="display: none; margin-top: 0.5rem; font-weight: bold;"></div>
        </div>
    </div>
</body>
</html>
"""

SETTINGS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Settings - Test Portal</title>
    <style>body { font-family: sans-serif; margin: 2rem; } nav a { margin-right: 1rem; }</style>
</head>
<body>
    <h1 id="page-title">Settings</h1>
    <nav id="main-nav">
        <a href="/dashboard" id="nav-dashboard">Dashboard</a>
        <a href="/settings" id="nav-settings">Settings</a>
        <a href="/login" id="nav-logout">Log Out</a>
    </nav>
    <p id="settings-description">Manage platform settings and test configurations.</p>
</body>
</html>
"""

POPUP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Popup Test</title>
</head>
<body>
    <h1>Popup Tests</h1>
    <button id="open-single-popup" onclick="window.open('/dashboard', '_blank');">Open Single Popup</button>
    <button id="open-many-popups" onclick="
        for (let i = 0; i < 6; i++) {
            window.open('/settings', '_blank');
        }
    ">Spawn 6 Popups</button>
</body>
</html>
"""


async def login_get(request: Request) -> HTMLResponse:
    error_msg = request.query_params.get("error")
    error_html = f'<div id="error-message" class="error">{error_msg}</div>' if error_msg else ""
    return HTMLResponse(LOGIN_HTML.replace("{error_html}", error_html))


async def login_post(request: Request) -> Response:
    from urllib.parse import parse_qs

    raw_body = await request.body()
    parsed = parse_qs(raw_body.decode("utf-8"))
    username = parsed.get("username", [""])[0]
    password = parsed.get("password", [""])[0]

    if username == "testuser" and password == "secret123":
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie("auth_session", f"user_{username}")
        return response

    return HTMLResponse(
        LOGIN_HTML.replace(
            "{error_html}",
            '<div id="error-message" class="error">Invalid username or password.</div>',
        ),
        status_code=401,
    )


async def dashboard_get(request: Request) -> HTMLResponse:
    cookie = request.cookies.get("auth_session", "guest")
    user = cookie.replace("user_", "") if cookie.startswith("user_") else "guest"
    return HTMLResponse(DASHBOARD_HTML.replace("{username}", user))


async def settings_get(request: Request) -> HTMLResponse:
    return HTMLResponse(SETTINGS_HTML)


async def slow_get(request: Request) -> HTMLResponse:
    delay = float(request.query_params.get("delay", "1.0"))
    await asyncio.sleep(delay)
    return HTMLResponse("<html><body><h1 id='slow-title'>Slow Response Complete</h1></body></html>")


async def redirect_get(request: Request) -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=302)


async def private_redirect_get(request: Request) -> RedirectResponse:
    # Intentionally redirects to AWS metadata endpoint for SSRF defense verification
    return RedirectResponse(url="http://169.254.169.254/latest/meta-data/", status_code=302)


async def popup_get(request: Request) -> HTMLResponse:
    return HTMLResponse(POPUP_HTML)


def create_web_test_app() -> Starlette:
    """Create a configured Starlette test web application."""
    routes = [
        Route("/", endpoint=lambda req: RedirectResponse("/login", status_code=302)),
        Route("/login", endpoint=login_get, methods=["GET"]),
        Route("/login", endpoint=login_post, methods=["POST"]),
        Route("/dashboard", endpoint=dashboard_get, methods=["GET"]),
        Route("/settings", endpoint=settings_get, methods=["GET"]),
        Route("/slow", endpoint=slow_get, methods=["GET"]),
        Route("/redirect", endpoint=redirect_get, methods=["GET"]),
        Route("/private-redirect", endpoint=private_redirect_get, methods=["GET"]),
        Route("/popup", endpoint=popup_get, methods=["GET"]),
    ]
    return Starlette(debug=True, routes=routes)


class EphemeralWebServer:
    """Spins up a lightweight uvicorn server in a separate thread on an ephemeral port."""

    def __init__(self, app: Starlette, host: str = "127.0.0.1") -> None:
        self.app = app
        self.host = host
        self.port: int = 0
        self.server: Any = None
        self.thread: threading.Thread | None = None

    def find_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((self.host, 0))
            return int(s.getsockname()[1])

    def start(self) -> str:
        import uvicorn

        self.port = self.find_free_port()
        config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="error",
            access_log=False,
        )
        self.server = uvicorn.Server(config)

        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()

        # Wait until server is started
        import time
        deadline = time.monotonic() + 5.0
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.05)

        return self.base_url

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3.0)


@asynccontextmanager
async def run_test_web_server(host: str = "127.0.0.1") -> AsyncGenerator[str, None]:
    """Async context manager that yields the base URL of a running test web server."""
    app = create_web_test_app()
    server = EphemeralWebServer(app, host=host)
    base_url = server.start()
    try:
        yield base_url
    finally:
        server.stop()
