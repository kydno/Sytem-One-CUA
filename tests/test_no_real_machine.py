"""The guard in conftest.py: a test that forgets to patch the machine fails instead of driving it."""

import importlib
import os
import socket
import subprocess
import urllib.request
from pathlib import Path

import anthropic
import httpx
import openai
import pytest
import websocket
from conftest import _loopback_only

from typesafe_computer_use import macos, windows
from typesafe_computer_use.browser import cdp
from typesafe_computer_use.browser.cdp import Chrome, Session


@pytest.mark.parametrize(
    "touch",
    [
        lambda: macos.click_at((200.0, 200.0)),
        lambda: macos.press("return"),
        lambda: macos.type_text("hi"),
        lambda: macos.scroll(-5),
        lambda: macos.open_url("Google Chrome", "https://example.com"),
        lambda: macos.activate("Finder"),
        lambda: macos.screenshot(),
        lambda: macos.default_browser(),
        lambda: macos.keyboard_focus(),
    ],
)
def test_an_unpatched_call_refuses_instead_of_reaching_the_machine(touch):
    # Off macOS the stand-in Quartz refuses first, before the guard is reached.
    with pytest.raises(RuntimeError, match=r"real machine|unavailable on this OS"):
        touch()


def test_the_pointer_reads_as_mid_screen_so_no_test_aborts_by_chance():
    assert macos.mouse_location() == (500.0, 500.0)
    macos.check_abort()


@pytest.mark.parametrize(
    "touch",
    [
        lambda: windows.click_at((200.0, 200.0)),
        lambda: windows.press("return"),
        lambda: windows.press("[", command=True),
        lambda: windows.type_text("hi"),
        lambda: windows.scroll(-5),
        lambda: windows.open_url("Google Chrome", "https://example.com"),
        lambda: windows.activate("notepad.exe"),
        lambda: windows.open_path(Path("state.txt")),
        lambda: windows.screenshot(),
        lambda: windows.ax_press(object()),
        lambda: windows.ax_focus(object()),
        lambda: windows.ax_set_value(object(), "hi"),
    ],
)
def test_an_unpatched_windows_call_refuses_instead_of_reaching_the_machine(touch):
    # Off Windows a stand-in module may refuse first, before the guard is reached.
    with pytest.raises(RuntimeError, match=r"real machine|unavailable on this OS"):
        touch()


def test_the_windows_pointer_reads_as_mid_screen_too():
    assert windows.mouse_location() == (500.0, 500.0)
    windows.check_abort()


# ------------------------------------------------------------- the browser backend
@pytest.mark.parametrize(
    "touch",
    [
        lambda tmp: Chrome(port=9, profile=str(tmp)).start(),
        lambda tmp: cdp.find_chrome(),
        lambda tmp: cdp._get_json("http://127.0.0.1:9/json/version"),
        lambda tmp: Session("ws://127.0.0.1:9/devtools/page/X", origin="http://127.0.0.1:9"),
        lambda tmp: subprocess.Popen(["true"]),
        lambda tmp: subprocess.run(["true"]),
        lambda tmp: os.system("true"),
        lambda tmp: socket.create_connection(("93.184.215.14", 443), timeout=1),
        lambda tmp: socket.getaddrinfo("example.com", 443),
        lambda tmp: urllib.request.urlopen("https://example.com", timeout=1),
    ],
)
def test_no_browser_process_or_network_is_reachable(touch, tmp_path):
    with pytest.raises(RuntimeError, match="real machine"):
        touch(tmp_path)


def _refused_by_the_guard(exc: BaseException | None) -> bool:
    """Whether the guard refused a lookup or a connection. An SDK wraps that refusal in its own
    connection error, so look down the chain."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if (
            isinstance(exc, RuntimeError)
            and "real machine" in str(exc)
            and ("a lookup of" in str(exc) or "a connection to" in str(exc))
        ):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


@pytest.mark.parametrize(
    "touch",
    [
        lambda: urllib.request.urlopen("https://example.com", timeout=1),
        lambda: httpx.get("https://example.com", timeout=1),
        # httpx2 comes in with the Anthropic SDK; imported here, a release without it fails this case only.
        lambda: importlib.import_module("httpx2").get("https://example.com", timeout=1),
        # Each SDK builds its own proxy map from its own copy of getproxies.
        lambda: anthropic.Anthropic(api_key="x", base_url="https://example.com", max_retries=0, timeout=1).models.list(),
        lambda: openai.OpenAI(api_key="x", base_url="https://example.com/v1", max_retries=0, timeout=1).models.list(),
    ],
    ids=["urllib", "httpx", "httpx2", "anthropic", "openai"],
)
def test_a_proxy_on_loopback_does_not_carry_a_request_out(touch, monkeypatch):
    # A proxy that listens here would pass the loopback check and forward the request anywhere.
    with socket.socket() as proxy:
        proxy.bind(("127.0.0.1", 0))
        proxy.listen(1)
        host, port = proxy.getsockname()
        for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
            monkeypatch.setenv(name, f"http://{host}:{port}")
        # A host listed here would skip the proxy and meet the refusal anyway, proving nothing.
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
        try:
            touch()
        except Exception as exc:
            assert _refused_by_the_guard(exc), f"{exc!r} is not the guard's refusal"
        else:
            pytest.fail("the request went through")


def test_the_guard_drops_the_proxy_variables_a_client_reads_itself(monkeypatch):
    # websocket-client reads https_proxy from the environment on each connect, not through
    # getproxies, so only unsetting the variables keeps it off a proxy. The guard ran before this
    # test could set them, so its proxy step runs again here, after they are set.
    def refuse(what: str):
        def call(*args, **kwargs):
            raise RuntimeError(f"a test reached the real machine through {what}")

        return call

    with socket.socket() as proxy:
        proxy.bind(("127.0.0.1", 0))
        proxy.listen(1)
        host, port = proxy.getsockname()
        for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
            monkeypatch.setenv(name, f"http://{host}:{port}")
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
        _loopback_only(monkeypatch, refuse)
        assert not [name for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY") if name in os.environ]
        try:
            websocket.WebSocket().connect("wss://example.com", timeout=1)
        except Exception as exc:
            assert _refused_by_the_guard(exc), f"{exc!r} is not the guard's refusal"
        else:
            pytest.fail("the connection went through")


def test_a_server_the_test_started_on_loopback_is_still_reachable():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=1):
            pass
