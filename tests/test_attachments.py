import urllib.request

import pytest

from coding_agent.attachments import (
    AttachmentError,
    _GitHubAssetRedirectHandler,
    download_issue_images,
    extract_issue_image_urls,
)


def test_extracts_github_images_from_body_and_comments():
    body = """
![screen](https://github.com/user-attachments/assets/11111111-1111-1111-1111-111111111111)
![ignored](https://example.com/secret.png)
"""
    comments = (
        {
            "author": "alice",
            "body": '<img src="https://private-user-images.githubusercontent.com/1/example.png?jwt=secret&amp;x=1">',
        },
        {
            "author": "bob",
            "body": "![duplicate](https://github.com/user-attachments/assets/11111111-1111-1111-1111-111111111111)",
        },
    )

    assert extract_issue_image_urls(body, comments) == (
        "https://github.com/user-attachments/assets/11111111-1111-1111-1111-111111111111",
        "https://private-user-images.githubusercontent.com/1/example.png?jwt=secret&x=1",
    )


def test_download_validates_image_and_uses_authentication(monkeypatch, tmp_path):
    png = b"\x89PNG\r\n\x1a\n" + b"payload"
    captured = {}

    class Response:
        headers = {"Content-Length": str(len(png)), "Content-Type": "image/png"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit):
            return png[:limit]

    class Opener:
        def open(self, request, timeout):
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr("coding_agent.attachments.urllib.request.build_opener", lambda *_args: Opener())
    paths = download_issue_images(
        ("https://github.com/user-attachments/assets/example",),
        tmp_path,
        github_token="token-value",
        max_images=4,
        max_bytes=1024,
    )

    assert paths == (tmp_path / "issue-image-1.png",)
    assert paths[0].read_bytes() == png
    assert paths[0].stat().st_mode & 0o777 == 0o600
    assert captured == {"authorization": "Bearer token-value", "timeout": 60}


def test_download_rejects_excess_images(tmp_path):
    with pytest.raises(AttachmentError, match="limit is 1"):
        download_issue_images(
            (
                "https://github.com/user-attachments/assets/one",
                "https://github.com/user-attachments/assets/two",
            ),
            tmp_path,
            github_token="token-value",
            max_images=1,
            max_bytes=1024,
        )


def test_redirect_rejects_unknown_host_and_strips_auth_cross_host(monkeypatch):
    def fake_redirect(_self, req, _fp, _code, _msg, _headers, newurl):
        return urllib.request.Request(newurl, headers=dict(req.header_items()))

    monkeypatch.setattr(urllib.request.HTTPRedirectHandler, "redirect_request", fake_redirect)
    handler = _GitHubAssetRedirectHandler()
    request = urllib.request.Request(
        "https://github.com/user-attachments/assets/example",
        headers={"Authorization": "Bearer secret"},
    )
    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://objects.githubusercontent.com/example.png",
    )
    assert redirected is not None
    assert redirected.get_header("Authorization") is None

    github_s3_redirect = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://github-production-user-asset-6210df.s3.amazonaws.com/193846962/image.png",
    )
    assert github_s3_redirect is not None
    assert github_s3_redirect.get_header("Authorization") is None

    with pytest.raises(AttachmentError, match="disallowed host"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://attacker.example/image.png",
        )
