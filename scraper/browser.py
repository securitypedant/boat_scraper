"""Browser management with stealth Playwright setup."""
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from scraper.config import (
    BROWSER_TIMEOUT,
    NAVIGATION_TIMEOUT,
)


class BoatBrowser:
    """Manages a stealth Playwright browser instance for BoatTrader scraping.

    Uses real Google Chrome (channel='chrome') with --headless=new to avoid
    Cloudflare detection that blocks Playwright's bundled headless_shell.
    """

    def __init__(self):
        self.playwright = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def _is_challenge_page(self, page: Page) -> bool:
        """Detect if we're on a Cloudflare challenge/interstitial page."""
        title = page.title().lower()
        challenge_titles = [
            "performing security verification",
            "just a moment",
            "verify you are human",
            "ddos protection",
        ]
        return any(t in title for t in challenge_titles)

    def _launch_browser(self) -> None:
        """Launch real Chrome in new headless mode."""
        self.playwright = sync_playwright().start()

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-web-security",
            "--disable-features=IsolateOrigins,site-per-process",
            "--headless=new",
            "--no-sandbox",
        ]

        self.browser = self.playwright.chromium.launch(
            headless=False,  # Required for --headless=new via args
            args=launch_args,
        )

        self.context = self.browser.new_context(
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

    def start(self) -> Page:
        """Start browser and verify it can access BoatTrader."""
        print("[browser] Launching Chrome (headless=new)...")
        self._launch_browser()

        print("[browser] Verifying access to BoatTrader...")
        try:
            self.page.goto(
                "https://www.boattrader.com/boats/",
                wait_until="domcontentloaded",
                timeout=30000,
            )
        except Exception as e:
            print(f"[browser] Navigation test failed: {e}")
            self.shutdown()
            raise

        if self._is_challenge_page(self.page):
            print("[browser] WARNING: Cloudflare challenge still detected.")
            print("[browser] The site may require a headed browser session,")
            print("[browser] or the scraper may need to run from a residential IP.")
            self.shutdown()
            raise RuntimeError(
                "Cloudflare challenge could not be bypassed automatically. "
                "Try running from an IP with better reputation, or use a VPN/proxy."
            )

        print("[browser] Browser ready.")
        return self.page

    def shutdown(self) -> None:
        """Cleanly shut down browser and context."""
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
