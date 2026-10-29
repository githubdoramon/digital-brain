"""Speech-only numeric response timing; no audio, credentials, or identifiers."""

import time

from fastapi.routing import APIRoute


class SpeechTimedRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def timed(request):
            started = time.perf_counter()
            response = await handler(request)
            parts = [f"speech_backend;dur={(time.perf_counter() - started) * 1000:.2f}"]
            auth_ms = getattr(request.state, "auth_duration_ms", None)
            if auth_ms is not None:
                parts.append(f"speech_auth;dur={auth_ms:.2f}")
            upload_ms = getattr(request.state, "speech_upload_ms", None)
            if upload_ms is not None:
                parts.append(f"speech_upload;dur={upload_ms:.2f}")
            response.headers["Server-Timing"] = ", ".join(parts)
            response.headers["Cache-Control"] = "no-store"
            return response

        return timed
