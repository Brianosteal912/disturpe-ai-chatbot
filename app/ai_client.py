from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

import requests

_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_RETRYABLE_STATUS_CODES = {408, 429}


class AIAPIError(RuntimeError):
    """A safe error that never includes credentials or provider response bodies."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AIClient:
    """Minimal client for OpenAI-compatible chat-completions endpoints."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        *,
        api_key: str = "",
        api_key_header: str = "Authorization",
        api_key_prefix: str = "Bearer",
        extra_headers: Mapping[str, str] | None = None,
        allow_insecure_http: bool = False,
        timeout: tuple[float, float] = (10.0, 60.0),
        max_retries: int = 3,
        max_response_bytes: int = 2_000_000,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.endpoint = self._validate_endpoint(endpoint, allow_insecure_http)
        self.model = model.strip()
        if not self.model:
            raise ValueError("AI_MODEL cannot be empty.")
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.max_response_bytes = max(1_024, max_response_bytes)
        self.sleep = sleep
        self.session = session or requests.Session()
        self._request_lock = threading.Lock()

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Disturpe-AI-Chatbot/3.0",
        }
        if api_key:
            self._validate_header(api_key_header, api_key)
            prefix = api_key_prefix.strip()
            headers[api_key_header] = f"{prefix} {api_key}".strip()

        protected = {name.casefold() for name in headers}
        protected.update({"content-length", "host", "transfer-encoding"})
        for name, value in (extra_headers or {}).items():
            self._validate_header(name, value)
            if name.casefold() in protected:
                raise ValueError(
                    f"AI_EXTRA_HEADERS_JSON cannot override protected header: {name}"
                )
            headers[name] = value
        self.session.headers.update(headers)

    @staticmethod
    def _validate_endpoint(endpoint: str, allow_insecure_http: bool) -> str:
        candidate = endpoint.strip()
        parsed = urlsplit(candidate)
        if not candidate or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(
                "AI_API_URL must be a valid URL without embedded credentials."
            )
        if parsed.fragment:
            raise ValueError("AI_API_URL cannot contain a URL fragment (#fragment).")
        if parsed.scheme == "https":
            return candidate
        is_local_http = (
            parsed.scheme == "http" and parsed.hostname.casefold() in _LOCAL_HOSTS
        )
        if is_local_http or (parsed.scheme == "http" and allow_insecure_http):
            return candidate
        raise ValueError(
            "AI_API_URL must use HTTPS. Explicitly set "
            "AI_ALLOW_INSECURE_HTTP=true to allow remote HTTP."
        )

    @staticmethod
    def _validate_header(name: str, value: str) -> None:
        if not _HEADER_NAME_RE.fullmatch(name) or "\r" in value or "\n" in value:
            raise ValueError("Invalid API header configuration.")

    def generate(
        self,
        prompt: str,
        *,
        system: str,
        history: list[dict[str, str]] | None = None,
        images: list[str] | None = None,
        files: list[str] | None = None,
        temperature: float = 0.88,
        max_tokens: int = 700,
        top_p: float = 0.92,
        presence_penalty: float | None = None,
        frequency_penalty: float | None = None,
    ) -> str:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for item in history or []:
            role = item.get("role")
            content = item.get("content")
            if role in {"user", "assistant"} and isinstance(content, str):
                messages.append({"role": role, "content": content})

        attachment_urls = list(images or []) + list(files or [])
        if attachment_urls:
            user_content: str | list[dict[str, Any]] = [
                {"type": "text", "text": prompt},
                *[
                    {"type": "image_url", "image_url": {"url": url}}
                    for url in images or []
                ],
            ]
            if files:
                user_content[0]["text"] += (
                    "\n\nAdditional file URLs provided by the user:\n"
                    + "\n".join(files)
                )
        else:
            user_content = prompt
        messages.append({"role": "user", "content": user_content})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": top_p,
        }
        if presence_penalty is not None:
            payload["presence_penalty"] = presence_penalty
        if frequency_penalty is not None:
            payload["frequency_penalty"] = frequency_penalty

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            response: requests.Response | None = None
            try:
                with self._request_lock:
                    response = self.session.post(
                        self.endpoint,
                        json=payload,
                        timeout=self.timeout,
                        allow_redirects=False,
                        stream=True,
                    )
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt + 1 < self.max_retries:
                    self.sleep(min(2**attempt * 2, 8))
                    continue
                raise AIAPIError("The AI API is currently unreachable.") from exc
            except requests.RequestException as exc:
                raise AIAPIError("Could not connect to the AI API.") from exc

            try:
                if (
                    response.status_code in _RETRYABLE_STATUS_CODES
                    or response.status_code >= 500
                ):
                    last_error = AIAPIError(
                        "The AI API returned a temporary error.",
                        status_code=response.status_code,
                    )
                    if attempt + 1 < self.max_retries:
                        self.sleep(self._retry_delay(response, attempt))
                        continue

                if 300 <= response.status_code < 400:
                    raise AIAPIError(
                        "The AI API redirect was rejected for security reasons.",
                        status_code=response.status_code,
                    )

                if not response.ok:
                    raise AIAPIError(
                        f"The AI API returned HTTP {response.status_code}.",
                        status_code=response.status_code,
                    )

                data = self._read_json(response)
                content = self._extract_content(data)
                if not content:
                    raise AIAPIError("The AI API returned an empty response.")
                return content
            finally:
                response.close()

        raise AIAPIError("The AI API did not respond.") from last_error

    def _read_json(self, response: requests.Response) -> dict[str, Any]:
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > self.max_response_bytes:
                    raise AIAPIError(
                        "The AI API response exceeded the safe size limit."
                    )
            except ValueError:
                pass

        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=65_536):
            total += len(chunk)
            if total > self.max_response_bytes:
                raise AIAPIError("The AI API response exceeded the safe size limit.")
            chunks.append(chunk)
        try:
            data = json.loads(b"".join(chunks).decode(response.encoding or "utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AIAPIError("The AI API returned invalid JSON.") from exc
        if not isinstance(data, dict):
            raise AIAPIError("The AI API returned an invalid response.")
        return data

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                message = choice.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str):
                        return content.strip()
                    if isinstance(content, list):
                        parts = [
                            part.get("text", "")
                            for part in content
                            if isinstance(part, dict)
                            and isinstance(part.get("text"), str)
                        ]
                        return "".join(parts).strip()
                text = choice.get("text")
                if isinstance(text, str):
                    return text.strip()
        for key in ("output_text", "content", "response"):
            value = data.get(key)
            if isinstance(value, str):
                return value.strip()
        return ""

    @staticmethod
    def _retry_delay(response: requests.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(max(float(retry_after), 0.5), 20.0)
            except ValueError:
                pass
        return min(2**attempt * 2, 8)
