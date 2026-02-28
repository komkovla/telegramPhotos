"""Async Google Photos Library API client with token refresh and retries."""

import asyncio
import logging
from typing import Any

import httpx
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)

BASE_URL = "https://photoslibrary.googleapis.com/v1"
UPLOAD_URL = "https://photoslibrary.googleapis.com/v1/uploads"
MIN_RETRY_DELAY_SEC = 30  # API doc: 429 requires at least 30s before retry
MAX_RETRIES = 4
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class GooglePhotosError(Exception):
    """Raised when a Google Photos API request fails after retries."""

    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class GooglePhotosClient:
    """Async client for the Google Photos Library API."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        *,
        timeout: float = 60.0,
    ) -> None:
        self._credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=[
                "https://www.googleapis.com/auth/photoslibrary.appendonly",
                "https://www.googleapis.com/auth/photoslibrary.readonly.appcreateddata",
            ],
        )
        self._timeout = timeout

    async def _get_access_token(self) -> str:
        """Return a valid access token, refreshing if necessary."""
        if self._credentials.expired or not self._credentials.valid:
            await asyncio.to_thread(self._credentials.refresh, Request())
        return self._credentials.token

    def _auth_headers(self, access_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}"}

    async def get_or_create_album(self, title: str) -> str:
        """
        Return the album ID for the given title. If no album with this title
        exists, create it.
        """
        album_id = await self._find_album_by_title(title)
        if album_id:
            logger.debug("Found existing album title=%r album_id=%s", title, album_id)
            return album_id
        album_id = await self._create_album(title)
        logger.info("Created new album title=%r album_id=%s", title, album_id)
        return album_id

    async def _find_album_by_title(self, title: str) -> str | None:
        page_token: str | None = None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            while True:
                params: dict[str, Any] = {
                    "pageSize": 50,
                    "excludeNonAppCreatedData": True,
                }
                if page_token:
                    params["pageToken"] = page_token

                resp = await self._request_with_retry(
                    client,
                    "GET",
                    f"{BASE_URL}/albums",
                    params=params,
                )
                data = resp.json()
                for album in data.get("albums", []):
                    if album.get("title") == title:
                        return album["id"]
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
        return None

    async def _create_album(self, title: str) -> str:
        title_trimmed = title[:500] if len(title) > 500 else title
        body = {"album": {"title": title_trimmed}}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await self._request_with_retry(
                client,
                "POST",
                f"{BASE_URL}/albums",
                json=body,
            )
        data = resp.json()
        album_id = data.get("id")
        if not album_id:
            raise GooglePhotosError("Create album response missing id", body=resp.text)
        return album_id

    async def get_album_product_url(self, album_id: str) -> str:
        """Return the Google Photos web URL for the given album."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await self._request_with_retry(
                client, "GET", f"{BASE_URL}/albums/{album_id}",
            )
        data = resp.json()
        url = data.get("productUrl")
        if not url:
            raise GooglePhotosError("Album response missing productUrl", body=resp.text)
        return url

    async def upload_media(
        self,
        file_bytes: bytes,
        filename: str,
        mime_type: str,
        album_id: str,
    ) -> None:
        """Upload media to Google Photos and add it to the given album."""
        upload_token = await self._upload_bytes(file_bytes, mime_type)
        await self._create_media_item(upload_token, filename, album_id)

    async def _upload_bytes(self, file_bytes: bytes, mime_type: str) -> str:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await self._request_with_retry(
                client,
                "POST",
                UPLOAD_URL,
                content=file_bytes,
                extra_headers={
                    "Content-type": "application/octet-stream",
                    "X-Goog-Upload-Content-Type": mime_type,
                    "X-Goog-Upload-Protocol": "raw",
                },
            )
        return resp.text.strip()

    async def _create_media_item(
        self,
        upload_token: str,
        filename: str,
        album_id: str,
    ) -> None:
        filename_safe = filename[:255] if len(filename) > 255 else filename
        body = {
            "albumId": album_id,
            "newMediaItems": [
                {
                    "description": "",
                    "simpleMediaItem": {
                        "uploadToken": upload_token,
                        "fileName": filename_safe,
                    },
                }
            ],
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await self._request_with_retry(
                client,
                "POST",
                f"{BASE_URL}/mediaItems:batchCreate",
                json=body,
            )
        data = resp.json()
        results = data.get("newMediaItemResults", [])
        if not results:
            raise GooglePhotosError("batchCreate returned no results", body=resp.text)
        status = results[0].get("status", {})
        if status.get("code") and status["code"] != 0:
            msg = status.get("message", "Unknown error")
            raise GooglePhotosError(f"batchCreate failed: {msg}", body=resp.text)

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        content: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """
        Execute an HTTP request with automatic retry on transient failures.

        Always refreshes the OAuth token on each attempt so retries after token
        expiry succeed. Caller-provided ``extra_headers`` are merged on every
        attempt (Authorization is always overwritten with a fresh token).
        """
        last_exc: Exception | None = None
        delay = MIN_RETRY_DELAY_SEC

        for attempt in range(MAX_RETRIES):
            if attempt > 0:
                logger.warning(
                    "Retry attempt=%d/%d delay=%ds method=%s url=%s",
                    attempt, MAX_RETRIES - 1, delay, method, url,
                )
                await asyncio.sleep(delay)

            token = await self._get_access_token()
            request_headers = self._auth_headers(token)
            if extra_headers:
                request_headers.update(extra_headers)

            try:
                if json is not None:
                    resp = await client.request(
                        method, url, params=params, json=json, headers=request_headers
                    )
                else:
                    resp = await client.request(
                        method, url, params=params, content=content, headers=request_headers
                    )
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning(
                    "HTTP error method=%s url=%s attempt=%d/%d error=%s",
                    method, url, attempt + 1, MAX_RETRIES, exc,
                )
                delay = min(delay * 2, 120)
                continue

            if resp.status_code in RETRYABLE_STATUS_CODES:
                last_exc = GooglePhotosError(
                    f"API returned {resp.status_code}",
                    status_code=resp.status_code,
                    body=resp.text,
                )
                logger.warning(
                    "Retryable status method=%s url=%s status=%d attempt=%d/%d",
                    method, url, resp.status_code, attempt + 1, MAX_RETRIES,
                )
                delay = max(delay, MIN_RETRY_DELAY_SEC)
                delay = min(delay * 2, 120)
                continue

            resp.raise_for_status()
            return resp

        if last_exc:
            raise last_exc
        raise GooglePhotosError("Request failed after retries")
