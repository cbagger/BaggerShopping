from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import httpx
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from .config import settings


class AuthError(RuntimeError):
    pass


class AuthInteractionRequired(AuthError):
    pass


@dataclass
class AuthState:
    token: str | None = None
    updated_at: float | None = None
    source: str | None = None


class SamsungAuthManager:
    def __init__(self) -> None:
        self.state_file = settings.auth_state_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def load_state(self) -> AuthState:
        if not self.state_file.exists():
            return AuthState()
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return AuthState(
                token=data.get("token"),
                updated_at=data.get("updated_at"),
                source=data.get("source"),
            )
        except Exception:
            return AuthState()

    def save_state(self, state: AuthState) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
        tmp.replace(self.state_file)

    async def token_valid(self, token: str | None) -> bool:
        if not token:
            return False
        url = (
            f"{settings.samsung_food_web_base}"
            "/api/grpc-web/whisk.x.user.v1.UserAPI/GetMe"
        )
        headers = {
            "Accept": "*/*",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/grpc-web+proto",
            "Cookie": f"whisk.USER_TOKEN={token}; _whsk=3",
            "Origin": settings.samsung_food_web_base,
            "x-grpc-web": "1",
            "x-user-agent": "grpc-web-ts/1.0",
            "x-whisk-app-name": "webapp",
            "x-whisk-app-version": settings.samsung_food_app_version,
            "x-whisk-device-type": "Desktop",
        }
        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
        ) as client:
            r = await client.post(url, headers=headers, content=b"\x00\x00\x00\x00\x00")
        return r.status_code == 200

    async def get_token(self, force_refresh: bool = False) -> str:
        # 1) Persistent state from a previous successful login
        state = self.load_state()
        if not force_refresh and await self.token_valid(state.token):
            return state.token  # type: ignore[return-value]

        # 2) Optional env-token fallback for initial migration/testing
        if not force_refresh and settings.samsung_food_token:
            if await self.token_valid(settings.samsung_food_token):
                self.save_state(
                    AuthState(
                        token=settings.samsung_food_token,
                        updated_at=time.time(),
                        source="env-token",
                    )
                )
                return settings.samsung_food_token

        # 3) Use a persistent browser profile. If Samsung's browser session is
        # still alive, this can recover a fresh whisk.USER_TOKEN without
        # re-entering credentials.
        token = await self._token_from_persistent_browser()
        if token and await self.token_valid(token):
            self.save_state(
                AuthState(token=token, updated_at=time.time(), source="browser-session")
            )
            return token

        # 4) If the session is gone, attempt Samsung Account login using credentials.
        if settings.samsung_account_email and settings.samsung_account_password:
            token = await self._login_with_credentials()
            if token and await self.token_valid(token):
                self.save_state(
                    AuthState(token=token, updated_at=time.time(), source="credentials")
                )
                return token

        raise AuthInteractionRequired(
            "Samsung Food needs a new interactive browser login. "
            "Start the login-ui profile, sign in to Samsung Food, open Lists > Indkøbsliste, "
            "then stop login-ui and call /api/auth/refresh again."
        )

    async def _extract_token_from_context(self, context) -> str | None:
        cookies = await context.cookies()
        for cookie in cookies:
            if cookie.get("name") == "whisk.USER_TOKEN":
                value = cookie.get("value")
                if value:
                    return value
        return None

    async def _token_from_persistent_browser(self) -> str | None:
        Path(settings.browser_user_data_dir).mkdir(parents=True, exist_ok=True)

        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=settings.browser_user_data_dir,
                headless=settings.auth_headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(
                    settings.samsung_food_web_base,
                    wait_until="domcontentloaded",
                    timeout=int(settings.auth_timeout_seconds * 1000),
                )
                await page.wait_for_timeout(2500)
                return await self._extract_token_from_context(context)
            finally:
                await context.close()

    async def _login_with_credentials(self) -> str | None:
        Path(settings.browser_user_data_dir).mkdir(parents=True, exist_ok=True)

        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=settings.browser_user_data_dir,
                headless=settings.auth_headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(
                    settings.samsung_food_web_base,
                    wait_until="domcontentloaded",
                    timeout=int(settings.auth_timeout_seconds * 1000),
                )
                await page.wait_for_timeout(1500)

                token = await self._extract_token_from_context(context)
                if token:
                    return token

                # Open Samsung Food login wall.
                try:
                    await page.get_by_text("Log in", exact=True).first.click(timeout=8000)
                except PlaywrightTimeoutError:
                    pass

                # Samsung provider button may be in a modal on app.samsungfood.com.
                provider = page.get_by_text("Continue with Samsung", exact=True)
                if await provider.count():
                    async with page.expect_popup(timeout=15000) as popup_info:
                        await provider.first.click()
                    login_page = await popup_info.value
                else:
                    login_page = page

                await login_page.wait_for_load_state("domcontentloaded")
                await login_page.wait_for_timeout(1200)

                # Email/ID step.
                email_selectors = [
                    'input[type="email"]',
                    'input[name="email"]',
                    'input[name="loginId"]',
                    'input[id*="email"]',
                    'input[id*="login"]',
                    'input[type="text"]',
                ]
                email_input = None
                for selector in email_selectors:
                    locator = login_page.locator(selector).first
                    if await locator.count():
                        email_input = locator
                        break
                if email_input is None:
                    raise AuthInteractionRequired(
                        "Samsung login page did not expose an email field."
                    )

                await email_input.fill(settings.samsung_account_email or "")

                next_candidates = [
                    login_page.get_by_text("Næste", exact=True),
                    login_page.get_by_text("Next", exact=True),
                    login_page.locator('button[type="submit"]'),
                ]
                for candidate in next_candidates:
                    if await candidate.count():
                        await candidate.first.click()
                        break

                await login_page.wait_for_timeout(1200)

                # Password step.
                password_input = login_page.locator('input[type="password"]').first
                try:
                    await password_input.wait_for(
                        state="visible",
                        timeout=15000,
                    )
                except PlaywrightTimeoutError as exc:
                    raise AuthInteractionRequired(
                        "Samsung did not present a password field. "
                        "CAPTCHA, 2FA or device approval may be required."
                    ) from exc

                await password_input.fill(settings.samsung_account_password or "")

                submit_candidates = [
                    login_page.get_by_text("Log på", exact=True),
                    login_page.get_by_text("Sign in", exact=True),
                    login_page.locator('button[type="submit"]'),
                ]
                for candidate in submit_candidates:
                    if await candidate.count():
                        await candidate.first.click()
                        break

                # Wait for OAuth callback / Samsung Food page to receive the token.
                deadline = time.monotonic() + settings.auth_timeout_seconds
                while time.monotonic() < deadline:
                    token = await self._extract_token_from_context(context)
                    if token:
                        return token

                    # Detect common cases requiring human interaction.
                    body = ""
                    try:
                        body = (await login_page.locator("body").inner_text()).lower()
                    except Exception:
                        pass

                    blockers = [
                        "verification code",
                        "bekræftelseskode",
                        "two-step",
                        "2-step",
                        "captcha",
                        "verify it's you",
                        "bekræft, at det er dig",
                    ]
                    if any(marker in body for marker in blockers):
                        raise AuthInteractionRequired(
                            "Samsung requires CAPTCHA/2FA/device verification. "
                            "Automatic background login cannot complete this step."
                        )

                    await login_page.wait_for_timeout(1000)

                raise AuthError(
                    "Samsung login completed without yielding whisk.USER_TOKEN."
                )
            finally:
                await context.close()
