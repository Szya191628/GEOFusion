"""Bounded public-web GET transport. DNS answers are checked then pinned per request."""
import http.client
import ipaddress
import socket
import ssl
import time
from functools import lru_cache
from urllib.parse import urlsplit
import httpx
import certifi
from omnirank.audit import audit_site
from omnirank.config import Config
from app import draft

@lru_cache(maxsize=128)
def public_dns(host, time_bucket):
    # Some local proxy clients return benchmark-range fake IPs for public names.
    # Resolve those names over authenticated HTTPS, then still pin a public IP.
    if '.' not in host or host.endswith(('.local', '.localhost', '.internal')):
        raise ValueError('Non-public hostname blocked')
    response = httpx.get('https://dns.google/resolve', params={'name': host, 'type': 'A',
        'edns_client_subnet': '0.0.0.0/0'}, timeout=6, follow_redirects=False)
    response.raise_for_status()
    data = response.json()
    ips = [a['data'] for a in data.get('Answer', []) if a.get('type') == 1]
    if data.get('Status') != 0 or not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError('Public DNS did not return public addresses')
    return ips

def public_target(url, allowed_host):
    p = urlsplit(url)
    if (p.scheme not in ('http', 'https') or p.hostname != allowed_host or p.username or p.password
            or p.port not in (None, 80, 443)):
        raise ValueError('Only same-host public HTTP(S) on standard ports is allowed')
    addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    if addresses and all(ipaddress.ip_address(a[4][0]) in ipaddress.ip_network('198.18.0.0/15') for a in addresses):
        try:
            ipaddress.ip_address(p.hostname)
        except ValueError:
            return p, public_dns(p.hostname, int(time.time()//60))[0]
        raise ValueError('Benchmark network IP blocked')
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Private, loopback, metadata or reserved network destination blocked')
    return p, addresses[0][4][0]

class PublicTransport:
    def __init__(self, host):
        self.host, self.requests, self.bytes, self.started = host, 0, 0, time.monotonic()
        self.blocked = []
        self.html_pages = set()

    def respond(self, request):
        self.requests += 1
        if self.requests > 65 or self.bytes > 12_000_000 or time.monotonic() - self.started > 110:
            self.blocked.append(str(request.url))
            return httpx.Response(599, text='Audit request/size/time budget exceeded')
        conn = None
        try:
            p, ip = public_target(str(request.url), self.host)
            port = p.port or (443 if p.scheme == 'https' else 80)
            conn = http.client.HTTPConnection(p.hostname, port, timeout=6)
            sock = socket.create_connection((ip, port), timeout=6)
            conn.sock = ssl.create_default_context(cafile=certifi.where()).wrap_socket(sock, server_hostname=p.hostname) if p.scheme == 'https' else sock
            path = (p.path or '/') + ('?' + p.query if p.query else '')
            conn.request('GET', path, headers={'Host': p.netloc, 'User-Agent': 'GEOFusion/1.0 public-site-audit', 'Accept-Encoding': 'identity'})
            response = conn.getresponse()
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise ValueError('Resource exceeds 2MB limit')
            self.bytes += len(body)
            if 200 <= response.status < 300 and 'text/html' in (response.getheader('Content-Type') or '').lower():
                self.html_pages.add(str(request.url))
            return httpx.Response(response.status, content=body, headers=dict(response.getheaders()))
        except (OSError, ValueError, http.client.HTTPException):
            self.blocked.append(str(request.url))
            return httpx.Response(599, text='Fetch failed or blocked by public-network policy')
        finally:
            if conn:
                conn.close()

def live_audit(url, sample_size=10):
    host = urlsplit(url).hostname
    public_target(url, host)
    transport = PublicTransport(host)
    config = Config({'site': {'url': url.rstrip('/'), 'name': host, 'entityType': 'Organization'},
                     'audit': {'sampleSize': sample_size}})
    with httpx.Client(transport=httpx.MockTransport(transport.respond), follow_redirects=False) as client:
        report = audit_site(config, client=client).to_dict()
    if not transport.html_pages:
        raise ValueError('No page could be fetched. Verify HTTPS, public DNS and website availability.')
    return draft('audit', report, coverage='live-http-html', upstream_commit='b2d572ad646835e1c4d927fe522c58fbb51af9f9',
        warnings=['HTTP HTML audit; JavaScript is not executed. Technical scores are not AI visibility metrics.'],
        blocked_or_failed_urls=sorted(set(transport.blocked)), requests=transport.requests,
        successful_html_pages=len(transport.html_pages))
