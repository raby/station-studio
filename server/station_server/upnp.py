"""Opening a port on the router (UPnP), so listeners outside can connect.

Most home routers let a program on the network forward a port to itself
(UPnP Internet Gateway Device). Some have it turned off; then the port is
forwarded by hand in the router's settings (Help › Streaming says how).
"""
from __future__ import annotations

import logging
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

SSDP = ("239.255.255.250", 1900)
SEARCH_TARGETS = (
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
)
SERVICES = ("urn:schemas-upnp-org:service:WANIPConnection:2", "urn:schemas-upnp-org:service:WANIPConnection:1",
            "urn:schemas-upnp-org:service:WANPPPConnection:1")
DESCRIPTION = "Station Server"


class UpnpError(Exception):
    pass


def discover(timeout: float = 2.5, sock_factory=None) -> list[str]:
    """The description URLs of the gateways that answer on this network."""
    sock = (sock_factory or (lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)))()
    found: list[str] = []
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.5)
        for st in SEARCH_TARGETS:
            msg = (f"M-SEARCH * HTTP/1.1\r\nHOST: {SSDP[0]}:{SSDP[1]}\r\nMAN: \"ssdp:discover\"\r\n"
                   f"MX: 2\r\nST: {st}\r\n\r\n")
            sock.sendto(msg.encode(), SSDP)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, _ = sock.recvfrom(4096)
            except TimeoutError:
                continue
            except OSError:  # Windows reports an unreachable answer this way
                time.sleep(0.05)
                continue
            m = re.search(rb"(?im)^location:\s*(\S+)", data)
            if m:
                url = m.group(1).decode("latin-1")
                if url.startswith("http://") and url not in found:
                    found.append(url)
    finally:
        sock.close()
    return found


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class Gateway:
    """The router's port-forwarding service, from its description URL."""

    def __init__(self, location: str, opener=urllib.request.urlopen) -> None:
        self.location = location
        self._open = opener
        with opener(location, timeout=5) as resp:
            root = ET.fromstring(resp.read(1_000_000))
        base = next((e.text for e in root.iter() if _local(e.tag) == "URLBase" and e.text), location)
        self.service = self.control = None
        for svc in root.iter():
            if _local(svc.tag) != "service":
                continue
            fields = {_local(c.tag): (c.text or "").strip() for c in svc}
            if fields.get("serviceType") in SERVICES:
                self.service = fields["serviceType"]
                self.control = urllib.parse.urljoin(base, fields.get("controlURL", ""))
                break
        if not self.control:
            raise UpnpError("The router answered but offers no port forwarding")
        self.host = urllib.parse.urlsplit(self.control).hostname or ""

    def _call(self, action: str, args: dict) -> dict:
        body = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
        envelope = (
            '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{self.service}">{body}</u:{action}></s:Body></s:Envelope>')
        req = urllib.request.Request(self.control, data=envelope.encode(), method="POST", headers={
            "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{self.service}#{action}"'})
        try:
            with self._open(req, timeout=5) as resp:
                root = ET.fromstring(resp.read(200_000))
        except urllib.error.HTTPError as exc:  # a SOAP fault
            detail = exc.read(20_000).decode("utf-8", "replace")
            m = re.search(r"<errorDescription>(.*?)</errorDescription>", detail)
            raise UpnpError(f"The router refused: {m.group(1) if m else exc.reason}") from None
        except (OSError, ET.ParseError) as exc:
            raise UpnpError(f"The router didn't answer: {exc}") from None
        out = {}
        for e in root.iter():
            if _local(e.tag) == f"{action}Response":
                out = {_local(c.tag): (c.text or "") for c in e}
        return out

    def local_address(self) -> str:
        """This computer's address on the router's network."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((self.host, 1900))
            return s.getsockname()[0]
        finally:
            s.close()

    def external_address(self) -> str:
        return self._call("GetExternalIPAddress", {}).get("NewExternalIPAddress", "")

    def mapping(self, port: int) -> str | None:
        """The local address the port goes to, or None if it isn't forwarded."""
        try:
            got = self._call("GetSpecificPortMappingEntry",
                             {"NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": "TCP"})
        except UpnpError:
            return None
        return got.get("NewInternalClient") or None

    def forward(self, port: int, to: str | None = None) -> str:
        to = to or self.local_address()
        self._call("AddPortMapping", {
            "NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": "TCP", "NewInternalPort": port,
            "NewInternalClient": to, "NewEnabled": 1, "NewPortMappingDescription": f"{DESCRIPTION} {port}",
            "NewLeaseDuration": 0})
        return to

    def unforward(self, port: int) -> None:
        self._call("DeletePortMapping", {"NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": "TCP"})


def find_gateway(discover=discover, opener=urllib.request.urlopen) -> Gateway:
    problems = []
    for location in discover():
        try:
            return Gateway(location, opener)
        except (UpnpError, OSError, ET.ParseError) as exc:
            problems.append(str(exc))
    raise UpnpError("No router on this network offers UPnP port forwarding"
                    + (f" ({problems[0]})" if problems else "")
                    + ": turn on UPnP in the router's settings, or forward the port by hand")
