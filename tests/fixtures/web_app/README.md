# Web Test Application Fixture

**Created in:** P2-00  
**Used by:** P2 (Playwright adapter tests)

This directory contains a deterministic web application for testing browser automation without external internet access.

## Features & Routes

- `/login` (`GET`, `POST`): Login form with username, password fields, submit button, and error messaging.
- `/dashboard` (`GET`): Post-login dashboard page containing:
  - Header (`#page-title`)
  - Navigation bar with multiple links (`#nav-dashboard`, `#nav-settings`, `#nav-logout`)
  - Known stable text value (`#welcome-message`: "Welcome to Test Portal")
  - Conditionally visible element (`#cond-element`, toggled via `#toggle-btn`)
  - Form select dropdown (`#role-select`)
  - Client-side action execution with dynamic feedback (`#action-btn`, `#feedback-message`)
- `/settings` (`GET`): Secondary page for multi-page navigation.
- `/slow` (`GET`): Latency-simulating route (`?delay=<seconds>`) for timeout enforcement.
- `/redirect` (`GET`): 302 redirect to `/dashboard`.
- `/private-redirect` (`GET`): 302 redirect to AWS metadata endpoint `http://169.254.169.254/latest/meta-data/` for verifying SSRF route blocking.
- `/popup` (`GET`): Popup window triggers (`#open-single-popup`, `#open-many-popups`) for popup limit tests.

## Usage in Tests

```python
from tests.fixtures.web_app import run_test_web_server

async with run_test_web_server() as base_url:
    # Navigate browser to base_url + "/login"
    ...
```
