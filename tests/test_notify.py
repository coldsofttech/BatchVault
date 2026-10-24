import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from batchvault import notify
from batchvault.errors import OptionsError
from batchvault.models import FileResult, RunResult, Status


def make_result(status=Status.SUCCESS):
    return RunResult(entries=[FileResult("a.txt", status)])


@pytest.fixture
def server():
    received = []
    reply = {"code": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            received.append(json.loads(self.rfile.read(length)))
            self.send_response(reply["code"])
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/hook", received, reply
    httpd.shutdown()
    httpd.server_close()


def test_should_notify():
    ok, bad = make_result(), make_result(Status.FAILED)
    assert notify.should_notify("always", ok)
    assert notify.should_notify("success", ok)
    assert not notify.should_notify("success", bad)
    assert notify.should_notify("failure", bad)
    assert not notify.should_notify("failure", ok)


def test_send_posts_json(server):
    url, received, _ = server
    assert notify.send(url, notify.build_payload("pack", make_result())) is None

    payload = received[0]
    assert "BatchVault pack" in payload["text"]
    assert payload["text"] == payload["content"]
    assert payload["result"]["status"] == "success"


def test_send_reports_http_errors(server):
    url, _, reply = server
    reply["code"] = 500
    assert "500" in notify.send(url, {"text": "x"})


def test_send_reports_unreachable_server():
    assert notify.send("http://127.0.0.1:1/hook", {"text": "x"})


@pytest.mark.parametrize("url", ["ftp://x/y", "not a url", "http://"])
def test_invalid_url(url):
    with pytest.raises(OptionsError):
        notify.validate_url(url)
