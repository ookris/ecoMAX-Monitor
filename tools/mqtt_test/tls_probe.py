"""Sonda TLS: przyjmuje polaczenie TLS od EW11A, zapisuje parametry handshake
i przekazuje odszyfrowany ruch do lokalnego brokera (bez TLS).

Loguje:
  - wersje TLS i zestawy szyfrow oferowane przez klienta (ClientHello),
  - czy klient wysyla SNI (nazwe serwera),
  - wynegocjowana wersje i szyfr albo powod nieudanego handshake.

Sonda akceptuje rowniez stare szyfry (SECLEVEL=0), zeby zobaczyc, co EW11A potrafi.

Przyklad:
    python tls_probe.py --cert ~/mqtt_test/certs/server.crt --key ~/mqtt_test/certs/server.key \
        --listen 8884 --upstream 127.0.0.1:1884
"""
import argparse
import datetime
import socket
import ssl
import struct
import os
import sys
import threading
import time
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

CIPHER_NAMES = {
    0x002F: 'TLS_RSA_WITH_AES_128_CBC_SHA', 0x0035: 'TLS_RSA_WITH_AES_256_CBC_SHA',
    0x003C: 'TLS_RSA_WITH_AES_128_CBC_SHA256', 0x003D: 'TLS_RSA_WITH_AES_256_CBC_SHA256',
    0x009C: 'TLS_RSA_WITH_AES_128_GCM_SHA256', 0x009D: 'TLS_RSA_WITH_AES_256_GCM_SHA384',
    0x000A: 'TLS_RSA_WITH_3DES_EDE_CBC_SHA', 0x0005: 'TLS_RSA_WITH_RC4_128_SHA',
    0xC009: 'TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA', 0xC00A: 'TLS_ECDHE_ECDSA_WITH_AES_256_CBC_SHA',
    0xC013: 'TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA', 0xC014: 'TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA',
    0xC023: 'TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA256', 0xC024: 'TLS_ECDHE_ECDSA_WITH_AES_256_CBC_SHA384',
    0xC027: 'TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA256', 0xC028: 'TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA384',
    0xC02B: 'TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256', 0xC02C: 'TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384',
    0xC02F: 'TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256', 0xC030: 'TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384',
    0xCCA8: 'TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305', 0xCCA9: 'TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305',
    0x0033: 'TLS_DHE_RSA_WITH_AES_128_CBC_SHA', 0x0039: 'TLS_DHE_RSA_WITH_AES_256_CBC_SHA',
    0x009E: 'TLS_DHE_RSA_WITH_AES_128_GCM_SHA256', 0x009F: 'TLS_DHE_RSA_WITH_AES_256_GCM_SHA384',
    0x1301: 'TLS_AES_128_GCM_SHA256 (1.3)', 0x1302: 'TLS_AES_256_GCM_SHA384 (1.3)',
    0x0067: 'TLS_DHE_RSA_WITH_AES_128_CBC_SHA256', 0x006B: 'TLS_DHE_RSA_WITH_AES_256_CBC_SHA256',
    0xCCAA: 'TLS_DHE_RSA_WITH_CHACHA20_POLY1305',
    0x1303: 'TLS_CHACHA20_POLY1305_SHA256 (1.3)', 0x00FF: 'TLS_EMPTY_RENEGOTIATION_INFO_SCSV',
}
VERSIONS = {0x0301: 'TLS1.0', 0x0302: 'TLS1.1', 0x0303: 'TLS1.2', 0x0304: 'TLS1.3'}


def log(msg):
    print(f"{datetime.datetime.now():%H:%M:%S} {msg}", flush=True)


def parse_client_hello(data: bytes) -> dict:
    """Minimalny parser ClientHello (rekord TLS z typem handshake 1)."""
    info = {}
    if len(data) < 9 or data[0] != 0x16 or data[5] != 0x01:
        return {'error': f'to nie jest ClientHello (pierwsze bajty: {data[:8].hex()})'}
    p = 9
    info['client_version'] = VERSIONS.get(struct.unpack('>H', data[p:p + 2])[0], data[p:p + 2].hex())
    p += 2 + 32  # wersja + random
    sid_len = data[p]
    p += 1 + sid_len
    cs_len = struct.unpack('>H', data[p:p + 2])[0]
    p += 2
    suites = [struct.unpack('>H', data[p + i:p + i + 2])[0] for i in range(0, cs_len, 2)]
    info['ciphers'] = [CIPHER_NAMES.get(s, f'0x{s:04X}') for s in suites]
    p += cs_len
    comp_len = data[p]
    p += 1 + comp_len
    info['sni'] = None
    info['extensions'] = []
    if p + 2 <= len(data):
        ext_total = struct.unpack('>H', data[p:p + 2])[0]
        p += 2
        end = min(len(data), p + ext_total)
        while p + 4 <= end:
            etype, elen = struct.unpack('>HH', data[p:p + 4])
            body = data[p + 4:p + 4 + elen]
            info['extensions'].append(f'0x{etype:04X}')
            if etype == 0 and len(body) > 5:  # server_name
                name_len = struct.unpack('>H', body[3:5])[0]
                info['sni'] = body[5:5 + name_len].decode('ascii', 'replace')
            if etype == 43:  # supported_versions
                info['supported_versions'] = [
                    VERSIONS.get(struct.unpack('>H', body[1 + i:3 + i])[0], body[1 + i:3 + i].hex())
                    for i in range(0, body[0], 2)]
            p += 4 + elen
    return info


def pipe(src, dst):
    try:
        while True:
            data = src.recv(4096)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def sink(tls, addr):
    """Tryb bez brokera: tylko odbior i zliczanie danych (nic nie jest wysylane)."""
    from econet import protocol  # sklada ramki, zeby potwierdzic poprawnosc strumienia
    total, frames, buf = 0, 0, bytearray()
    last = t0 = time.monotonic()
    tls.settimeout(1)
    while True:
        try:
            data = tls.recv(4096)
        except socket.timeout:
            data = b''
            if time.monotonic() - last < 1:
                continue
        except (ssl.SSLError, OSError) as e:
            log(f"  Blad odczytu: {e}")
            break
        else:
            if not data:
                break
        total += len(data)
        buf.extend(data)
        found, rest = protocol.find_frames(buf)
        frames += len(found)
        buf = bytearray(rest)
        if time.monotonic() - last >= 10:
            last = time.monotonic()
            log(f"  odebrano {total} B, {frames} poprawnych ramek ({total / (last - t0):.0f} B/s)")
    log(f"Rozlaczono {addr[0]}:{addr[1]} (odebrano {total} B, {frames} ramek)")


def handle(raw, addr, ctx, upstream):
    log(f"Polaczenie od {addr[0]}:{addr[1]}")
    try:
        raw.settimeout(10)
        hello = raw.recv(4096, socket.MSG_PEEK)
        info = parse_client_hello(hello)
        log(f"  ClientHello: {info.get('client_version')}, SNI: {info.get('sni')!r}, "
            f"supported_versions: {info.get('supported_versions')}")
        if 'error' in info:
            log(f"  {info['error']}")
        log(f"  Oferowane szyfry ({len(info.get('ciphers', []))}): {', '.join(info.get('ciphers', []))}")
        log(f"  Rozszerzenia: {' '.join(info.get('extensions', []))}")
        tls = ctx.wrap_socket(raw, server_side=True)
        log(f"  Handshake OK: {tls.version()}, {tls.cipher()[0]}")
    except (ssl.SSLError, OSError) as e:
        log(f"  Handshake NIEUDANY: {e}")
        raw.close()
        return
    tls.settimeout(None)
    if upstream is None:
        sink(tls, addr)
        return
    try:
        up = socket.create_connection(upstream, timeout=5)
        up.settimeout(None)
    except OSError as e:
        log(f"  Brak polaczenia z brokerem {upstream[0]}:{upstream[1]}: {e}")
        tls.close()
        return
    t1 = threading.Thread(target=pipe, args=(tls, up), daemon=True)
    t2 = threading.Thread(target=pipe, args=(up, tls), daemon=True)
    t1.start(); t2.start()
    t1.join(); t2.join()
    log(f"Rozlaczono {addr[0]}:{addr[1]}")


def main():
    ap = argparse.ArgumentParser(description='Sonda TLS przed brokerem MQTT')
    ap.add_argument('--cert', required=True)
    ap.add_argument('--key', required=True)
    ap.add_argument('--listen', type=int, default=8884)
    ap.add_argument('--upstream', default='127.0.0.1:1884',
                    help='host:port brokera albo "none" = tylko odbior i zliczanie (tryb TCP client)')
    args = ap.parse_args()
    if args.upstream == 'none':
        upstream = None
    else:
        host, port = args.upstream.rsplit(':', 1)
        upstream = (host, int(port))

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', DeprecationWarning)
        ctx.minimum_version = ssl.TLSVersion.TLSv1  # dopusc tez stare wersje, zeby zobaczyc mozliwosci EW11A
    try:
        ctx.set_ciphers('ALL:@SECLEVEL=0')  # OpenSSL: dopusc tez stare szyfry
    except ssl.SSLError:
        ctx.set_ciphers('ALL')  # LibreSSL (macOS) nie zna @SECLEVEL
    ctx.load_cert_chain(args.cert, args.key)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', args.listen))
    srv.listen(5)
    log(f"Sonda TLS nasluchuje na :{args.listen}, przekazuje do {args.upstream}")
    while True:
        raw, addr = srv.accept()
        threading.Thread(target=handle, args=(raw, addr, ctx, upstream), daemon=True).start()


if __name__ == '__main__':
    main()
