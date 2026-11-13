"""Test-only launcher: refuse outbound sockets before importing the actual app."""
import ipaddress
import runpy
import socket
import sys


def local(address):
    if not isinstance(address, tuple) or len(address) < 2:
        raise PermissionError("acceptance transport requires numeric loopback")
    try:
        allowed = ipaddress.ip_address(address[0]).is_loopback
    except ValueError:
        allowed = False
    if not allowed:
        raise PermissionError("acceptance transport requires numeric loopback")


def install():
    connect, connect_ex, resolve = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def guarded_connect(sock, address):
        local(address)
        return connect(sock, address)

    def guarded_connect_ex(sock, address):
        local(address)
        return connect_ex(sock, address)

    def guarded_resolve(host, port, *args, **kwargs):
        local((host, port))
        return resolve(host, port, *args, **kwargs)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.getaddrinfo = guarded_resolve
    # This handshake must complete before app imports; no sitecustomize errors can be ignored.
    with socket.socket() as probe:
        operations = (lambda: socket.getaddrinfo("example.com", 443),
                      lambda: probe.connect(("192.0.2.1", 443)),
                      lambda: probe.connect_ex(("192.0.2.1", 443)))
        for operation in operations:
            try:
                operation()
            except PermissionError:
                continue
            raise RuntimeError("offline transport guard did not refuse")


if __name__ == "__main__":
    install()
    sys.argv[0] = "grimoire.app"
    runpy.run_module("grimoire.app", run_name="__main__")
