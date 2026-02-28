"""Tests for bot.google_photos — Google Photos API client."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from bot.google_photos import (
    GooglePhotosClient,
    GooglePhotosError,
    MAX_RETRIES,
    RETRYABLE_STATUS_CODES,
)


# ── GooglePhotosError ────────────────────────────────────────────────


class TestGooglePhotosError:
    def test_attributes(self):
        err = GooglePhotosError("bad request", status_code=400, body='{"error":"bad"}')
        assert str(err) == "bad request"
        assert err.status_code == 400
        assert err.body == '{"error":"bad"}'

    def test_defaults(self):
        err = GooglePhotosError("fail")
        assert err.status_code is None
        assert err.body is None


# ── Helpers ──────────────────────────────────────────────────────────


def _make_client() -> GooglePhotosClient:
    """Create a client with dummy credentials (token refresh will be mocked)."""
    with patch("bot.google_photos.Credentials"):
        client = GooglePhotosClient(
            client_id="cid",
            client_secret="csecret",
            refresh_token="rtoken",
        )
    return client


def _mock_response(status_code: int = 200, json_data: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text or ""
    if json_data is not None:
        resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


# ── _request_with_retry ─────────────────────────────────────────────


class TestRequestWithRetry:
    async def test_success_on_first_attempt(self):
        client = _make_client()
        client._get_access_token = AsyncMock(return_value="token_abc")
        http_client = AsyncMock(spec=httpx.AsyncClient)
        resp = _mock_response(200, {"ok": True})
        http_client.request.return_value = resp

        result = await client._request_with_retry(
            http_client, "GET", "https://example.com/api",
        )
        assert result is resp
        http_client.request.assert_called_once()

    @patch("bot.google_photos.MIN_RETRY_DELAY_SEC", 0)
    async def test_retries_on_429(self):
        client = _make_client()
        client._get_access_token = AsyncMock(return_value="token")

        resp_429 = _mock_response(429, text="rate limited")
        resp_ok = _mock_response(200, {"ok": True})

        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.request.side_effect = [resp_429, resp_ok]

        result = await client._request_with_retry(
            http_client, "GET", "https://example.com/api",
        )
        assert result is resp_ok
        assert http_client.request.call_count == 2

    @patch("bot.google_photos.MIN_RETRY_DELAY_SEC", 0)
    async def test_retries_on_500(self):
        client = _make_client()
        client._get_access_token = AsyncMock(return_value="token")

        resp_500 = _mock_response(500, text="server error")
        resp_ok = _mock_response(200, {"ok": True})

        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.request.side_effect = [resp_500, resp_ok]

        result = await client._request_with_retry(
            http_client, "POST", "https://example.com/api",
        )
        assert result is resp_ok

    @patch("bot.google_photos.MIN_RETRY_DELAY_SEC", 0)
    async def test_retries_on_network_error(self):
        client = _make_client()
        client._get_access_token = AsyncMock(return_value="token")

        resp_ok = _mock_response(200, {"ok": True})
        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.request.side_effect = [
            httpx.ConnectError("connection refused"),
            resp_ok,
        ]

        result = await client._request_with_retry(
            http_client, "GET", "https://example.com/api",
        )
        assert result is resp_ok

    @patch("bot.google_photos.MIN_RETRY_DELAY_SEC", 0)
    @patch("bot.google_photos.MAX_RETRIES", 2)
    async def test_raises_after_exhausted_retries(self):
        client = _make_client()
        client._get_access_token = AsyncMock(return_value="token")

        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.request.side_effect = httpx.ConnectError("down")

        with pytest.raises(httpx.ConnectError):
            await client._request_with_retry(
                http_client, "GET", "https://example.com/api",
            )
        assert http_client.request.call_count == 2

    @patch("bot.google_photos.MIN_RETRY_DELAY_SEC", 0)
    @patch("bot.google_photos.MAX_RETRIES", 2)
    async def test_raises_google_photos_error_after_retryable_status_exhausted(self):
        client = _make_client()
        client._get_access_token = AsyncMock(return_value="token")

        resp_503 = _mock_response(503, text="unavailable")
        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.request.return_value = resp_503

        with pytest.raises(GooglePhotosError) as exc_info:
            await client._request_with_retry(
                http_client, "GET", "https://example.com/api",
            )
        assert exc_info.value.status_code == 503

    async def test_refreshes_token_on_each_attempt(self):
        client = _make_client()
        tokens = iter(["token_1", "token_2"])
        client._get_access_token = AsyncMock(side_effect=tokens)

        resp_429 = _mock_response(429, text="rate limited")
        resp_ok = _mock_response(200, {"ok": True})

        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.request.side_effect = [resp_429, resp_ok]

        with patch("bot.google_photos.MIN_RETRY_DELAY_SEC", 0):
            await client._request_with_retry(
                http_client, "GET", "https://example.com/api",
            )

        assert client._get_access_token.call_count == 2

    async def test_extra_headers_merged(self):
        client = _make_client()
        client._get_access_token = AsyncMock(return_value="tok")

        resp_ok = _mock_response(200, {"ok": True})
        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.request.return_value = resp_ok

        await client._request_with_retry(
            http_client, "POST", "https://example.com/upload",
            content=b"data",
            extra_headers={"X-Custom": "value"},
        )

        call_kwargs = http_client.request.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
        assert headers["Authorization"] == "Bearer tok"
        assert headers["X-Custom"] == "value"


class TestRetryableStatusCodes:
    @pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
    def test_status_code_is_retryable(self, code):
        assert code in RETRYABLE_STATUS_CODES


# ── Album operations ─────────────────────────────────────────────────


class TestGetOrCreateAlbum:
    async def test_finds_existing_album(self):
        client = _make_client()
        client._find_album_by_title = AsyncMock(return_value="existing_id")
        client._create_album = AsyncMock()

        result = await client.get_or_create_album("My Album")
        assert result == "existing_id"
        client._create_album.assert_not_called()

    async def test_creates_album_when_not_found(self):
        client = _make_client()
        client._find_album_by_title = AsyncMock(return_value=None)
        client._create_album = AsyncMock(return_value="new_id")

        result = await client.get_or_create_album("New Album")
        assert result == "new_id"
        client._create_album.assert_called_once_with("New Album")


class TestGetAlbumProductUrl:
    async def test_returns_product_url(self):
        client = _make_client()
        client._get_access_token = AsyncMock(return_value="token")

        resp = _mock_response(200, json_data={
            "id": "album_1",
            "title": "My Album",
            "productUrl": "https://photos.google.com/lr/album/album_1",
        })
        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.request.return_value = resp
        http_client.__aenter__ = AsyncMock(return_value=http_client)
        http_client.__aexit__ = AsyncMock(return_value=False)

        with patch("bot.google_photos.httpx.AsyncClient", return_value=http_client):
            url = await client.get_album_product_url("album_1")

        assert url == "https://photos.google.com/lr/album/album_1"

    async def test_raises_when_product_url_missing(self):
        client = _make_client()
        client._get_access_token = AsyncMock(return_value="token")

        resp = _mock_response(200, json_data={"id": "album_1", "title": "My Album"})
        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.request.return_value = resp
        http_client.__aenter__ = AsyncMock(return_value=http_client)
        http_client.__aexit__ = AsyncMock(return_value=False)

        with patch("bot.google_photos.httpx.AsyncClient", return_value=http_client):
            with pytest.raises(GooglePhotosError, match="productUrl"):
                await client.get_album_product_url("album_1")


class TestUploadMedia:
    async def test_upload_calls_both_steps(self):
        client = _make_client()
        client._upload_bytes = AsyncMock(return_value="upload_token_abc")
        client._create_media_item = AsyncMock()

        await client.upload_media(b"bytes", "file.jpg", "image/jpeg", "album_1")

        client._upload_bytes.assert_called_once_with(b"bytes", "image/jpeg")
        client._create_media_item.assert_called_once_with(
            "upload_token_abc", "file.jpg", "album_1",
        )
