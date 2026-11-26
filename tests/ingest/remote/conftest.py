"""Shared by the collectors' tests: a source served from a dict."""

from datetime import datetime

from riffle import net


class Source:
    """A source as net.get, net.download and net.fetch_new: url -> body, None (404), or an
    exception to raise, each body's ETag its hash. A new answer carries its ETag unless tag_new
    is off; one hung up on carries it once tag_known is on. Records every url asked for, and for
    fetch_new each (url, ETag sent) and the missing codes asked with."""

    def __init__(self, answers: dict[str, bytes | None | Exception]):
        self.answers = answers
        self.asked: list[str] = []
        self.sent: list[tuple[str, str | None]] = []
        self.missing: set = set()
        self.tag_new, self.tag_known = True, False

    @property
    def etags(self) -> list[str | None]:
        return [etag for _, etag in self.sent]

    def _answer(self, url: str) -> bytes | None:
        self.asked.append(url)
        body = self.answers.get(url)
        if isinstance(body, Exception):
            raise body
        return body

    def get(self, url: str) -> bytes | None:
        return self._answer(url)

    def download(self, url, dest, progress=None):
        body = self._answer(url)
        if body is None:
            return None
        # net.download makes the folder it writes into
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        if progress:
            progress(len(body), len(body))
        return len(body)

    def tag(self, url: str, body: bytes) -> str | None:
        return f'"{hash(body)}"'

    def stamp(self, body: bytes) -> datetime | None:
        """The answer's Last-Modified: none, unless the source's own tests date it."""
        return None

    def fetch_new(self, url, dest, known, etag=None, accept="*/*", progress=None, missing=(404,)):
        self.sent.append((url, etag))
        self.missing.add(missing)
        body = self._answer(url)
        if body is None:
            return None
        tag = self.tag(url, body)
        if etag is not None and etag == tag:
            return net.Fetched("unchanged", b"", etag)
        head = body[: net.HEAD]
        if known(head):
            return net.Fetched("known", head, tag if self.tag_known else None)
        dest.write_bytes(body)
        if progress:
            progress(len(body), None)
        return net.Fetched("new", head, tag if self.tag_new else None, len(body), self.stamp(body))


class Auto:
    """The stamp read from the body, as the real source reads it."""


AUTO = Auto()


class Dated(Source):
    """A source whose lists say nothing in their first bytes, so none is hung up on: each is
    known by its Last-Modified, which `made` reads from the body (or `modified`). `accept` is
    what a list must be asked with, where a source's tests say."""

    accept: str | None = None

    def __init__(
        self,
        answers: dict[str, bytes | None | Exception],
        modified: datetime | None | Auto = AUTO,
        tagged: bool = True,
    ):
        super().__init__(answers)
        self.modified, self.tag_new = modified, tagged

    def made(self, body: bytes) -> datetime:
        raise NotImplementedError

    def stamp(self, body: bytes) -> datetime | None:
        return self.made(body) if isinstance(self.modified, Auto) else self.modified

    def fetch_new(self, url, dest, known, etag=None, accept="*/*", **kwargs):
        def unknown(head: bytes) -> bool:
            assert self.accept in (None, accept) and not known(head)
            return False

        return super().fetch_new(url, dest, unknown, etag, accept, **kwargs)
