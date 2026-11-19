from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import signal
import threading
from typing import Callable, Iterator


@dataclass
class interruption_cleanup_state:
    """Tracks additional SIGINT requests while graceful cleanup is in progress."""

    additional_interrupt_count: int = 0
    force_requested: bool = False


@contextmanager
def protect_interruption_cleanup(
    report: Callable[[str], None] | None = None,
    *,
    already_interrupted: bool = True,
) -> Iterator[interruption_cleanup_state]:
    """Prevent later Ctrl+C presses from tearing through critical cleanup.

    The first Ctrl+C is handled by the caller as a normal ``KeyboardInterrupt``.
    While the caller is preserving/finalizing an interrupted execution, this
    context converts additional SIGINTs into a cooperative force request rather
    than raising another asynchronous exception into arbitrary cleanup code.

    The force request is advisory: callers may skip optional work at safe
    boundaries, but critical resource cleanup can finish without being torn down
    mid-operation.
    """

    state = interruption_cleanup_state()
    reporter = report if report is not None else (lambda _message: None)

    if threading.current_thread() is not threading.main_thread():
        yield state
        return

    try:
        previous = signal.getsignal(signal.SIGINT)
    except (AttributeError, ValueError):
        yield state
        return

    def _handle_additional_sigint(signum, frame) -> None:  # noqa: ARG001
        state.additional_interrupt_count += 1
        if not state.force_requested:
            state.force_requested = True
            prefix = "Additional Ctrl+C received" if already_interrupted else "Ctrl+C received"
            reporter(
                f"{prefix}; cleanup is already in progress. A forced stop was requested "
                "and remaining optional cleanup will be skipped at the next safe boundary."
            )
        else:
            prefix = (
                "Additional Ctrl+C received again"
                if already_interrupted
                else "Another Ctrl+C received"
            )
            reporter(
                f"{prefix}; cleanup is still inside a critical section and will stop "
                "at the next safe boundary."
            )

    try:
        signal.signal(signal.SIGINT, _handle_additional_sigint)
    except (OSError, RuntimeError, ValueError):
        yield state
        return

    try:
        yield state
    finally:
        try:
            signal.signal(signal.SIGINT, previous)
        except (OSError, RuntimeError, ValueError):
            pass


__all__ = [
    "interruption_cleanup_state",
    "protect_interruption_cleanup",
]
