"""
The text of a PDF somebody sent: a widget visitor's attachment, a file on Google Drive.

A PDF is untrusted input to a large parser, and one crafted to keep the parser busy
must not take the server with it. So it is read in a process of its own, which is
killed after TIMEOUT_SECONDS and, where the system has it, limited in memory; at most
MAX_CONCURRENT are read at once, and a further one is refused rather than queued. The
process gets none of the server's environment, so no API keys.

pdf_text() waits for that process: call it off the event loop (asyncio.to_thread).
This file is also the program the process runs, so it imports nothing of MATE.
"""

import io
import json
import logging
import os
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024    # as the chat clients allow (MAX_FILE_SIZE)
MAX_CHARS = 1_000_000
MAX_CONCURRENT = 4
MAX_MEMORY = 1024 * 1024 * 1024
TIMEOUT_SECONDS = 15
# What Python itself may need to start
_ENVIRONMENT = ("LD_LIBRARY_PATH", "SYSTEMROOT")

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)


class PdfError(Exception):
    """Why a PDF has no text, in words for whoever sent it."""


def pdf_text(data: bytes, max_bytes: int = MAX_BYTES) -> str:
    """The text of a PDF's pages, at most MAX_CHARS of it; PdfError when it can't be read."""
    if len(data) > max_bytes:
        raise PdfError(f"the file is larger than {max_bytes // (1024 * 1024)} MB")
    if not _slots.acquire(blocking=False):
        raise PdfError("too many PDF files are being read at the moment; try again")
    try:
        if getattr(sys, "frozen", False):
            # A packaged standalone agent: sys.executable is the app, not Python
            return _extract(data)
        done = subprocess.run([sys.executable, "-I", __file__], input=data, capture_output=True,
                              timeout=TIMEOUT_SECONDS,
                              env={k: os.environ[k] for k in _ENVIRONMENT if k in os.environ})
    except subprocess.TimeoutExpired:
        logger.warning("A PDF of %d bytes was not read in %d seconds", len(data), TIMEOUT_SECONDS)
        raise PdfError("it takes too long to read") from None
    finally:
        _slots.release()
    try:
        answer = json.loads(done.stdout)
    except ValueError:
        logger.warning("Reading a PDF of %d bytes ended with %s: %s", len(data), done.returncode,
                       done.stderr.decode("utf-8", "replace")[-500:])
        raise PdfError("it could not be read") from None
    if "error" in answer:
        raise PdfError(answer["error"])
    return answer["text"]


def _extract(data: bytes) -> str:
    from pypdf import PdfReader
    texts, length = [], 0
    for page in PdfReader(io.BytesIO(data)).pages:
        text = page.extract_text()
        if text:
            texts.append(text)
            length += len(text) + 1
        if length >= MAX_CHARS:
            break
    return "\n".join(texts)[:MAX_CHARS].strip()


def _limit() -> None:
    """Bound this process's memory, and its time should the server that started it be gone."""
    try:
        import resource
    except ImportError:     # Windows
        return
    for what, limit in ((resource.RLIMIT_AS, MAX_MEMORY), (resource.RLIMIT_CPU, TIMEOUT_SECONDS + 5)):
        try:
            resource.setrlimit(what, (limit, limit))
        except (ValueError, OSError):   # macOS has no RLIMIT_AS to set
            pass


def _main() -> None:
    _limit()
    try:
        answer = {"text": _extract(sys.stdin.buffer.read())}
    except Exception as e:
        answer = {"error": str(e)[:300] or type(e).__name__}
    # ASCII, so a lone surrogate or the console's encoding can't break the answer
    sys.stdout.write(json.dumps(answer))


if __name__ == "__main__":
    _main()
