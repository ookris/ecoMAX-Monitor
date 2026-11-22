"""Pomiar ruchu z magistrali RS485 przez EW11A (tryb TCP-SERVER).

Faza 1 (pasywna): tylko odbior, nic nie jest wysylane.
Faza 2 (aktywna): odpowiedz B0 na kazde 0x30 adresowane do ecoNET (jak aplikacja),
nic poza tym.

UWAGA: uruchamiac tylko przy WYLACZONEJ aplikacji ecoMAX Monitor - w fazie aktywnej
dwa urzadzenia odpowiadalyby na 0x30 na magistrali RS485. Przy dzialajacej aplikacji
uzyj --active 0 (sam nasluch).

Przyklad:
    python tools/measure_bus.py --host 10.0.0.226 --passive 120 --active 180

Wynik (JSON) na stdout. Opis wynikow pomiaru z 2026-11-22:
docs/KONCEPCJA-serwis-chmurowy.md, pkt 6a.
"""
import argparse
import collections
import json
import os
import socket
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from econet import protocol  # noqa: E402

# Szacunkowy narzut na jedna wiadomosc MQTT po TLS 1.2 (AES-GCM) w TCP/IPv4:
# MQTT fixed header 2 + dlugosc tematu 2 + temat ~12 + rekord TLS 5+8+16 + TCP/IP 40
OVERHEAD_PER_MSG = 2 + 2 + 12 + 29 + 40


def run_phase(sock, seconds, respond):
    buf = bytearray()
    total = 0
    chunks = []
    stats = collections.defaultdict(lambda: [0, 0])  # (typ, zrodlo, cel) -> [liczba, bajty]
    frame_bytes = 0
    check_times = []
    sent_b0 = 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        try:
            data = sock.recv(4096)
        except socket.timeout:
            continue
        if not data:
            raise RuntimeError('EW11A zamknelo polaczenie')
        now = time.monotonic()
        total += len(data)
        chunks.append(len(data))
        buf.extend(data)
        frames, rest = protocol.find_frames(buf)
        buf = bytearray(rest)
        if len(buf) > 20000:
            buf = buf[-2000:]
        for f in frames:
            key = (f['type'], f['source'], f['dest'])
            stats[key][0] += 1
            stats[key][1] += f['length']
            frame_bytes += f['length']
            if f['type'] == protocol.FRAME_CHECK_DEVICE and f['dest'] == protocol.ADDR_ECONET:
                check_times.append(now)
                if respond:
                    sock.sendall(protocol.FrameBuilder.device_available_response(f['source']))
                    sent_b0 += 1
    dur = time.monotonic() - t0
    intervals = [b - a for a, b in zip(check_times, check_times[1:])]
    n = len(chunks)
    return {
        'duration_s': round(dur, 1),
        'bytes_total': total,
        'bytes_per_s': round(total / dur, 1),
        'valid_frame_bytes': frame_bytes,
        'recv_chunks': n,
        'chunks_per_s': round(n / dur, 2),
        'chunk_size_median': statistics.median(chunks) if chunks else 0,
        'chunk_size_max': max(chunks) if chunks else 0,
        'est_wire_bytes_per_s_mqtt_tls': round((total + n * OVERHEAD_PER_MSG) / dur, 1),
        'check_0x30_count': len(check_times),
        'check_0x30_interval_median_s': round(statistics.median(intervals), 2) if intervals else None,
        'b0_sent': sent_b0,
        'frames': sorted(
            ({'type': f'0x{k[0]:02X}', 'src': f'0x{k[1]:02X}', 'dst': f'0x{k[2]:02X}',
              'count': v[0], 'bytes': v[1], 'per_min': round(v[0] / dur * 60, 1)}
             for k, v in stats.items()),
            key=lambda r: -r['bytes']),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--host', required=True, help='adres EW11A')
    parser.add_argument('--port', type=int, default=8899, help='port TCP EW11A (domyslnie 8899)')
    parser.add_argument('--passive', type=int, default=120, help='czas fazy pasywnej [s]')
    parser.add_argument('--active', type=int, default=180,
                        help='czas fazy aktywnej z odpowiedziami B0 [s]; 0 = pomin')
    args = parser.parse_args()

    sock = socket.create_connection((args.host, args.port), timeout=10)
    sock.settimeout(1)
    result = {}
    try:
        if args.passive > 0:
            result['passive'] = run_phase(sock, args.passive, respond=False)
        if args.active > 0:
            result['active'] = run_phase(sock, args.active, respond=True)
    finally:
        sock.close()
    print(json.dumps(result, indent=1))


if __name__ == '__main__':
    main()
