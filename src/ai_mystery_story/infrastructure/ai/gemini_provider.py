import os
import time

from google import genai
from google.genai import errors

from .ai_provider import AIProvider


class GeminiProvider(AIProvider):

    def __init__(
        self,
        model: str | None = None,
        max_retries: int = 5,
        request_delay_seconds: int | None = None,
    ):
        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured"
            )

        self.client = genai.Client(
            api_key=api_key
        )

        self.model = model or os.getenv(
            "GEMINI_MODEL",
            "gemini-2.5-flash",
        )

        self.max_retries = max_retries
        self.request_delay_seconds = (
            request_delay_seconds
            if request_delay_seconds is not None
            else int(
                os.getenv(
                    "GEMINI_REQUEST_DELAY_SECONDS",
                    "10",
                )
            )
        )
        self._last_response_completed_at: float | None = None

        if self.request_delay_seconds < 0:
            raise ValueError(
                "GEMINI_REQUEST_DELAY_SECONDS must be "
                "greater than or equal to 0"
            )

    def generate(self, prompt: str) -> str:

        last_error: Exception | None = None

        for attempt in range(
            1,
            self.max_retries + 1,
        ):
            try:
                self._wait_before_next_request()

                print(
                    f"Gemini request "
                    f"(attempt {attempt}/{self.max_retries})..."
                )

                response = (
                    self.client.models.generate_content(
                        model=self.model,
                        contents=prompt,
                    )
                )

                if not response.text:
                    raise RuntimeError(
                        "Gemini returned an empty response"
                    )

                # Record timestamp when response completed
                self._last_response_completed_at = time.monotonic()

                return response.text

            except errors.ServerError as exc:
                last_error = exc

                print(
                    f"Gemini server error: "
                    f"{exc}"
                )

                if attempt >= self.max_retries:
                    break

                delay = min(
                    2 ** (attempt - 1) * 2,
                    30,
                )

                print(
                    f"Retrying in {delay}s..."
                )

                time.sleep(delay)

        raise RuntimeError(
            "Gemini generation failed after "
            f"{self.max_retries} attempts: "
            f"{last_error}"
        )

    def _wait_before_next_request(self) -> None:
        """Enforce post-response delay between Gemini API requests."""
        now = time.monotonic()

        if self._last_response_completed_at is not None:
            elapsed = now - self._last_response_completed_at
            delay = self.request_delay_seconds - elapsed
            if delay > 0:
                print(
                    f"Waiting {delay:.1f} seconds after previous Gemini "
                    "response before next request..."
                )
                time.sleep(delay)
