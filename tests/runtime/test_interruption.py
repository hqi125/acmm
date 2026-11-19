from __future__ import annotations

import signal

from acmm.runtime.interruption import protect_interruption_cleanup


def test_additional_sigint_becomes_cooperative_force_request(monkeypatch) -> None:
    installed = {}
    previous = object()

    monkeypatch.setattr(signal, "getsignal", lambda sig: previous)

    def fake_signal(sig, handler):
        installed["handler"] = handler
        return previous

    monkeypatch.setattr(signal, "signal", fake_signal)
    messages: list[str] = []

    with protect_interruption_cleanup(messages.append) as state:
        handler = installed["handler"]
        handler(signal.SIGINT, None)
        assert state.force_requested is True
        assert state.additional_interrupt_count == 1
        handler(signal.SIGINT, None)
        assert state.additional_interrupt_count == 2

    assert any("forced stop was requested" in item for item in messages)
    assert any("still inside a critical section" in item for item in messages)
    assert installed["handler"] is previous
