"""Shared pytest fixtures for the recovery test suite.

H9: tests must never open outbound sockets. This autouse fixture replaces
``socket.socket``, ``socket.create_connection`` and ``socket.getaddrinfo``
with functions that raise, so any accidental network attempt fails the test
instead of leaking a connection.
"""

import socket

import pytest


@pytest.fixture(autouse=True)
def block_sockets(monkeypatch):
    def _denied(*args, **kwargs):
        raise AssertionError(
            "outbound socket attempted during test (H9: fixtures/mocks only)"
        )

    monkeypatch.setattr(socket, "socket", _denied)
    monkeypatch.setattr(socket, "create_connection", _denied)
    monkeypatch.setattr(socket, "getaddrinfo", _denied)
    yield