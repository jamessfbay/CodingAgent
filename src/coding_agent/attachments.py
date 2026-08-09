from __future__ import annotations

import html
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser


class AttachmentError(RuntimeError):
    pass


_MARKDOWN_IMAGE = re.compile(
    r"!\[[^\]]*\]\(\s*(?:<(?P<angle>https://[^>]+)>|(?P<plain>https://[^\s)]+))",
    re.IGNORECASE,
)
_GITHUB_ASSET_HOSTS = {
    "user-images.githubusercontent.com",
    "private-user-images.githubusercontent.com",
    "objects.githubusercontent.com",
}
_GITHUB_S3_ASSET = re.compile(
    r"^github-production-user-asset-[0-9a-f]+\.s3\.amazonaws\.com$",
    re.IGNORECASE,
)


class _ImageHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        for name, value in attrs:
            if name.lower() == "src" and value:
                self.urls.append(value)
                break


def _allowed_asset_url(url: str, *, redirect: bool = False) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme.lower() != "https" or port not in (None, 443):
        return False
    if parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").lower()
    if host == "github.com":
        return parsed.path.startswith("/user-attachments/assets/")
    if host in _GITHUB_ASSET_HOSTS:
        return True
    return redirect and bool(_GITHUB_S3_ASSET.fullmatch(host))


def _safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def extract_issue_image_urls(body: str, comments: tuple[dict[str, str], ...]) -> tuple[str, ...]:
    urls: list[str] = []
    for text in (body, *(comment.get("body", "") for comment in comments)):
        for match in _MARKDOWN_IMAGE.finditer(text):
            urls.append(html.unescape(match.group("angle") or match.group("plain")))
        parser = _ImageHTMLParser()
        parser.feed(text)
        urls.extend(html.unescape(url) for url in parser.urls)

    unique: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url not in seen and _allowed_asset_url(url):
            seen.add(url)
            unique.append(url)
    return tuple(unique)


class _GitHubAssetRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not _allowed_asset_url(newurl, redirect=True):
            raise AttachmentError(f"GitHub image redirected to a disallowed host: {_safe_url(newurl)}")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            old_host = (urllib.parse.urlsplit(req.full_url).hostname or "").lower()
            new_host = (urllib.parse.urlsplit(newurl).hostname or "").lower()
            if old_host != new_host:
                redirected.remove_header("Authorization")
        return redirected


def _image_extension(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def download_issue_images(
    urls: tuple[str, ...],
    directory: pathlib.Path,
    *,
    github_token: str,
    max_images: int,
    max_bytes: int,
) -> tuple[pathlib.Path, ...]:
    if len(urls) > max_images:
        raise AttachmentError(f"Issue contains {len(urls)} images; limit is {max_images}")
    if not urls:
        return ()
    if not github_token:
        raise AttachmentError("GitHub authentication token is unavailable for image download")

    directory.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(_GitHubAssetRedirectHandler())
    paths: list[pathlib.Path] = []
    for index, url in enumerate(urls, start=1):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif",
                "Authorization": f"Bearer {github_token}",
                "User-Agent": "JOX-CodingAgent/0.1",
            },
        )
        try:
            with opener.open(request, timeout=60) as response:
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > max_bytes:
                    raise AttachmentError(
                        f"GitHub image exceeds {max_bytes} bytes: {_safe_url(url)}"
                    )
                data = response.read(max_bytes + 1)
        except AttachmentError:
            raise
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise AttachmentError(
                f"Could not download GitHub image {_safe_url(url)} ({type(exc).__name__})"
            ) from exc

        if len(data) > max_bytes:
            raise AttachmentError(f"GitHub image exceeds {max_bytes} bytes: {_safe_url(url)}")
        extension = _image_extension(data)
        if extension is None:
            raise AttachmentError(f"GitHub attachment is not a supported image: {_safe_url(url)}")
        path = directory / f"issue-image-{index}{extension}"
        path.write_bytes(data)
        path.chmod(0o600)
        paths.append(path)
    return tuple(paths)
