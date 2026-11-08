"""Outbound HTML fetching for vendor research with SSRF protections.

Only public HTTPS hosts are allowed. Every redirect hop is re-validated, the body
is size-capped, and robots.txt is honoured. DNS is resolved before the request;
the remaining DNS-rebinding window is accepted for this milestone.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

USER_AGENT = "BloomBotResearchBot/0.1 (+vendor pricing research)"


class FetchError(Exception):
    """Raised with a customer-safe message when a page cannot be used."""


@dataclass
class Page:
    url: str
    title: str
    description: str
    headings: str
    text: str
    links: list[str] = field(default_factory=list)  # absolute outbound https links


def _host_allowed(host: str, allowed_domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in allowed_domains)


def check_url(url: str, allowed_domains=None, resolver=socket.getaddrinfo) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.port not in (None, 443) or parsed.username or parsed.password:
        raise FetchError("Only standard HTTPS links can be fetched.")
    if allowed_domains is not None and not _host_allowed(host, allowed_domains):
        raise FetchError("This domain is not on the approved list.")
    try:
        infos = resolver(host, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise FetchError("The site's address could not be resolved.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise FetchError("Links to private or internal network addresses are not allowed.")
    return host


def page_from_html(url: str, html: str) -> Page:
    soup = BeautifulSoup(html[:600_000], "html.parser")
    for junk in soup(["script", "style", "noscript", "svg", "template"]):
        junk.decompose()
    title = (soup.title.get_text(" ", strip=True) if soup.title else urlparse(url).hostname or "")[:160]
    meta = soup.find("meta", attrs={"name": "description"})
    description = (meta.get("content", "") if meta else "")[:800]
    headings = " | ".join(h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2"], limit=10))[:1200]
    for junk in soup(["nav", "footer"]):
        junk.decompose()
    links = list(dict.fromkeys(urljoin(url, a["href"]) for a in soup.find_all("a", href=True)
                               if urljoin(url, a["href"]).startswith("https://")))[:300]
    text = " ".join(soup.get_text(" ", strip=True).split())
    return Page(url=url, title=title, description=description, headings=headings, text=text, links=links)


class SafeFetcher:
    def __init__(self, session=None, resolver=socket.getaddrinfo, timeout=8,
                 max_bytes=1_500_000, max_redirects=3, respect_robots=True):
        self.session = session or requests.Session()
        self.resolver = resolver
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.respect_robots = respect_robots
        self._robots: dict[str, RobotFileParser | None] = {}

    def _get(self, url: str, allowed_domains, accept_html=True):
        for _ in range(self.max_redirects + 1):
            check_url(url, allowed_domains, self.resolver)
            resp = self.session.get(url, timeout=self.timeout, allow_redirects=False, stream=True,
                                    headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain"})
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                url = urljoin(url, resp.headers["location"])
                resp.close()
                continue
            body = b""
            for chunk in resp.iter_content(64 * 1024):
                body += chunk
                if len(body) > self.max_bytes:
                    break
            resp.close()
            return url, resp.status_code, resp.headers.get("content-type", ""), body[: self.max_bytes]
        raise FetchError("The page redirected too many times.")

    def robots_allowed(self, url: str, allowed_domains=None) -> bool:
        parsed = urlparse(url)
        origin = f"https://{parsed.hostname}"
        if origin not in self._robots:
            parser = None
            try:
                _, status, _, body = self._get(origin + "/robots.txt", allowed_domains)
                if status in (401, 403):
                    parser = RobotFileParser()
                    parser.disallow_all = True
                elif status < 400:
                    parser = RobotFileParser()
                    parser.parse(body.decode("utf-8", "replace").splitlines())
            except (FetchError, requests.RequestException):
                parser = None  # robots.txt unreachable: treat as allowed
            self._robots[origin] = parser
        parser = self._robots[origin]
        return parser is None or parser.can_fetch(USER_AGENT, url)

    def fetch(self, url: str, allowed_domains=None) -> Page:
        check_url(url, allowed_domains, self.resolver)
        if self.respect_robots and not self.robots_allowed(url, allowed_domains):
            raise FetchError("The site's robots.txt does not allow automated reading of this page.")
        try:
            final_url, status, content_type, body = self._get(url, allowed_domains)
        except requests.RequestException:
            raise FetchError("The site couldn't be reached. Some vendors block automated requests.")
        if status >= 400:
            raise FetchError("The vendor page did not return readable HTML.")
        if "text/html" not in content_type:
            raise FetchError("This link doesn't appear to serve an HTML page.")
        return page_from_html(final_url, body.decode("utf-8", "replace"))
