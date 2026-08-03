"""Browser management with stealth Playwright setup."""
import time

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from scraper.config import (
    BROWSER_TIMEOUT,
    NAVIGATION_TIMEOUT,
    HEADLESS,
    BROWSER_CONTEXT_DIR,
    BROWSER_STATE_FILE,
    CHALLENGE_TIMEOUT,
)


class BoatBrowser:
    """Manages a stealth Playwright browser instance for BoatTrader scraping.

    Uses Chromium with anti-detection flags, persistent storage state, and
    a wait-and-retry loop for Cloudflare interstitials.
    """

    def __init__(self):
        self.playwright = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._authenticated_ok = False
        BROWSER_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

    def _is_challenge_page(self, page: Page) -> bool:
        """Detect if we're on a Cloudflare challenge/interstitial page."""
        title = page.title().lower()
        challenge_titles = [
            "performing security verification",
            "just a moment",
            "verify you are human",
            "ddos protection",
        ]
        if any(t in title for t in challenge_titles):
            return True

        try:
            content = page.content().lower()
        except Exception:
            return False

        challenge_markers = [
            "performing security verification",
            "just a moment",
            "verify you are human",
            "cf-turnstile",
            "ray id",
            "enable javascript and cookies to continue",
        ]
        return any(marker in content for marker in challenge_markers)

    def _launch_browser(self) -> None:
        """Launch Chromium with stealth flags and persistent storage state."""
        self.playwright = sync_playwright().start()

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-web-security",
            "--disable-features=IsolateOrigins,site-per-process",
            "--no-sandbox",
        ]

        self.browser = self.playwright.chromium.launch(
            headless=HEADLESS,
            args=launch_args,
        )

        storage_state = None
        if BROWSER_STATE_FILE.exists():
            storage_state = str(BROWSER_STATE_FILE)
            print(f"[browser] Restoring session state from {BROWSER_STATE_FILE}")

        self.context = self.browser.new_context(
            storage_state=storage_state,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/Los_Angeles",
            permissions=["geolocation"],
            color_scheme="light",
        )

        self.page = self.context.new_page()
        self.page.set_default_timeout(BROWSER_TIMEOUT)
        self.page.set_default_navigation_timeout(NAVIGATION_TIMEOUT)

    def _response_debug(self, response) -> str:
        """Build a short debug string for a Playwright response."""
        if response is None:
            return "status=unknown"
        parts = [f"status={response.status}"]
        try:
            ray = response.header_value("cf-ray")
            if ray:
                parts.append(f"cf-ray={ray}")
        except Exception:
            pass
        return ", ".join(parts)

    def _is_hard_blocked(self, page, response) -> bool:
        """Detect a hard Cloudflare access-denied block that won't self-clear."""
        if response is None or getattr(response, "status", None) != 403:
            return False
        try:
            title = page.title().lower()
        except Exception:
            title = ""
        return "access denied" in title or "forbidden" in title

    def start(self) -> Page:
        """Start browser and verify it can access BoatTrader."""
        self._authenticated_ok = False
        print(f"[browser] Launching Chromium (headless={HEADLESS})...")
        self._launch_browser()

        print("[browser] Verifying access to BoatTrader...")
        try:
            response = self.page.goto(
                "https://www.boattrader.com/boats/",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            print(
                f"[browser] Startup response: {self._response_debug(response)}, "
                f"title={self.page.title()[:80]!r}"
            )
        except Exception as e:
            print(f"[browser] Navigation test failed: {e}")
            self.shutdown()
            raise

        if self._is_hard_blocked(self.page, response):
            print(
                f"[browser] Hard Cloudflare block detected: "
                f"{self._response_debug(response)}, "
                f"title={self.page.title()[:80]!r}. "
                f"This IP/ASN is likely flagged."
            )
            self.shutdown()
            raise RuntimeError(
                "Cloudflare hard block (IP/ASN likely flagged); try a different egress IP."
            )

        if self._is_challenge_page(self.page):
            print(
                f"[browser] Cloudflare challenge detected; waiting up to "
                f"{CHALLENGE_TIMEOUT}s for it to clear..."
            )
            deadline = time.time() + CHALLENGE_TIMEOUT
            cleared = False
            while time.time() < deadline:
                try:
                    self.page.wait_for_timeout(2000)
                    if not self._is_challenge_page(self.page):
                        cleared = True
                        print("[browser] Challenge cleared.")
                        break
                except Exception:
                    # Page may be navigating; give it another cycle
                    pass

            if not cleared:
                print(
                    "[browser] WARNING: Cloudflare challenge still detected. "
                    f"Final title={self.page.title()[:80]!r}, "
                    f"url={self.page.url[:120]}"
                )
                self.shutdown()
                raise RuntimeError(
                    "Cloudflare challenge could not be bypassed automatically."
                )

        self._authenticated_ok = True
        print("[browser] Browser ready.")
        return self.page

    def recycle_page(self) -> Page:
        """Close current page and open a fresh one (keeps browser/context alive).

        Call this every ~200 scrapes to prevent renderer memory bloat/crashes.
        """
        if self.page:
            try:
                self.page.close()
            except Exception:
                pass
        if self.context:
            self.page = self.context.new_page()
            self.page.set_default_timeout(BROWSER_TIMEOUT)
            self.page.set_default_navigation_timeout(NAVIGATION_TIMEOUT)
            return self.page
        # Fallback — shouldn't happen if browser is already running
        return self.start()

    def shutdown(self) -> None:
        """Cleanly shut down browser and context, saving session state."""
        if self.context and self._authenticated_ok:
            try:
                self.context.storage_state(path=BROWSER_STATE_FILE)
                print(f"[browser] Saved session state to {BROWSER_STATE_FILE}")
            except Exception as e:
                print(f"[browser] Failed to save session state: {e}")
        if self.context:
            try:
                self.context.close()
            except Exception:
                pass
            self.context = None
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None
        if self.playwright:
            try:
                self.playwright.stop()
            except Exception:
                pass
            self.playwright = None
        self.page = None
        self._authenticated_ok = False

    def save_session(self) -> None:
        """Save current browser session state (no-op for now)."""
        pass

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.save_session()
        self.shutdown()
        return False
