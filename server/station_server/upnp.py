"""Opening a port on the router (UPnP), so listeners outside can connect.

Most home routers let a program on the network forward a port to itself
(UPnP Internet Gateway Device). Some have it turned off; then the port is
forwarded by hand in the router's settings (Help › Streaming says how).
"""
from __future__ import annotations

import errno
import ipaddress
import logging
import re
import select
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from station_server.hostaddr import host_addresses

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


# macOS 15 and later ask before an app talks to other devices on the
# network; until it's allowed, every attempt fails with "No route to host".
MAC_PERMISSION = ("macOS isn't letting Station Server reach the router. Quit Station Server and open it again "
                  "from Applications, and choose Allow if macOS asks about your local network; if Station Server "
                  "is listed in System Settings › Privacy & Security › Local Network, turn it on. A VPN can also "
                  "be in the way. Otherwise forward the port by hand (Help › Streaming). The router keeps the "
                  "forward, so this is needed only once")
def _blocked(exc: OSError) -> str:
    if isinstance(getattr(exc, "reason", None), OSError):  # urlopen wraps it
        exc = exc.reason
    if sys.platform == "darwin" and exc.errno in (errno.EHOSTUNREACH, errno.EPERM, errno.EACCES):
        return MAC_PERMISSION
    return f"The router can't be reached: {exc}"


def _private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private and not ip.startswith("127.")
    except ValueError:
        return False


def lan_addresses() -> list[str]:
    """This computer's addresses on home networks (192.168.x.x and so on).
    With a VPN on, the route to the internet can be a tunnel, so the search
    goes out of each of these rather than wherever the system would send it."""
    found: list[str] = []
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # no packet is sent: this only picks a route
        found.append(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()
    found += host_addresses()  # looked up in the background (see hostaddr)
    out = []
    for ip in found:
        if _private(ip) and ip not in out:
            out.append(ip)
    return out


def discover(timeout: float = 2.5, sock_factory=None, addresses=None) -> list[str]:
    """The description URLs of the gateways that answer on this network."""
    make = sock_factory or (lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP))
    here = lan_addresses() if addresses is None else addresses
    socks = []
    sent = None
    found: list[str] = []
    try:
        for addr in here or [None]:
            sock = make()
            socks.append(sock)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            targets = [SSDP]
            if addr:
                try:  # out of this network's interface, not a VPN's
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(addr))
                except OSError:
                    pass
                # most routers also answer when asked directly, at the usual .1
                targets.append((addr.rsplit(".", 1)[0] + ".1", SSDP[1]))
            sock.settimeout(0)
            for st in SEARCH_TARGETS:
                msg = (f"M-SEARCH * HTTP/1.1\r\nHOST: {SSDP[0]}:{SSDP[1]}\r\nMAN: \"ssdp:discover\"\r\n"
                       f"MX: 2\r\nST: {st}\r\n\r\n")
                for to in targets:
                    try:
                        sock.sendto(msg.encode(), to)
                        sent = True
                    except OSError as exc:
                        sent = sent or exc
        if isinstance(sent, OSError) or sent is None:  # nothing went out
            raise UpnpError(_blocked(sent) if isinstance(sent, OSError) else "No network to search")
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                ready, _, _ = select.select(socks, [], [], 0.25)
            except (OSError, ValueError):
                break
            for sock in ready:
                try:
                    data, _ = sock.recvfrom(4096)
                except OSError:  # Windows reports an unreachable answer this way
                    continue
                m = re.search(rb"(?im)^location:\s*(\S+)", data)
                if m:
                    url = m.group(1).decode("latin-1")
                    if url.startswith("http://") and url not in found:
                        found.append(url)
            if found and time.monotonic() > end - timeout + 1.0:
                break  # the router answers within a second; others are rare
    finally:
        for sock in socks:
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
        except OSError as exc:
            raise UpnpError(_blocked(exc)) from None
        except ET.ParseError as exc:
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
        except OSError as exc:
            raise UpnpError(_blocked(exc)) from None
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
        except UpnpError as exc:
            problems.append(str(exc))
        except OSError as exc:
            problems.append(_blocked(exc))
        except ET.ParseError as exc:
            problems.append(str(exc))
    if MAC_PERMISSION in problems:
        raise UpnpError(MAC_PERMISSION)
    raise UpnpError("No router on this network offers UPnP port forwarding"
                    + (f" ({problems[0]})" if problems else "")
                    + ": turn on UPnP in the router's settings, or forward the port by hand")
