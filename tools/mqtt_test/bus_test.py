"""Test strumienia RS485 przesylanego przez EW11A w trybie MQTT.

Subskrybuje <prefix>/up i <prefix>/status, sklada ramki obecnym parserem (econet/)
i wypisuje statystyki. Opcjonalnie:
  --respond-b0 [--b0-delay-ms N]  odpowiada B0 na 0x30 (jak aplikacja), z opoznieniem
  --uid-every S                   co S sekund wysyla zapytanie o UID (0x39) i mierzy
                                  czas do odpowiedzi 0xB9 (pelna petla przez MQTT)

UWAGA: --respond-b0 i --uid-every wysylaja ramki na magistrale. Uruchamiac tylko przy
zatrzymanej aplikacji ecoMAX Monitor (inaczej dwa moduly ecoNET na jednej magistrali).
Sam nasluch (bez tych opcji) jest bezpieczny takze przy dzialajacej aplikacji.

Przyklad:
    MQTT_PASS=... python bus_test.py --duration 120
    MQTT_PASS=... python bus_test.py --duration 180 --respond-b0 --b0-delay-ms 300 --uid-every 15
"""
import argparse
import collections
import json
import os
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from econet import protocol  # noqa: E402
from econet.connection import EW11AConnection  # noqa: E402

import paho.mqtt.client as mqtt  # noqa: E402


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


class BusTest:
    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()
        self.buf = bytearray()
        self.msg_sizes = []
        self.msg_times = []
        self.bytes_total = 0
        self.frame_bytes = 0
        self.frames = collections.defaultdict(lambda: [0, 0])
        self.check_times = []
        self.b0_sent = 0
        self.uid_pending = None
        self.uid_rtts = []
        self.uid_info = None
        self.uid_sent = 0
        self.status_msgs = []
        self.client = None

    # --- MQTT ---
    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        log(f"Polaczono z brokerem: {reason_code}")
        client.subscribe(f"{self.args.prefix}/up", qos=0)
        client.subscribe(f"{self.args.prefix}/status", qos=0)

    def on_message(self, client, userdata, msg):
        now = time.monotonic()
        if msg.topic.endswith('/status'):
            text = msg.payload.decode('utf-8', 'replace')
            log(f"STATUS: {text!r} (retain={msg.retain})")
            self.status_msgs.append(text)
            return
        with self.lock:
            self.msg_sizes.append(len(msg.payload))
            self.msg_times.append(now)
            self.bytes_total += len(msg.payload)
            self.buf.extend(msg.payload)
            frames, rest = protocol.find_frames(self.buf)
            self.buf = bytearray(rest)
            if len(self.buf) > 20000:
                self.buf = self.buf[-2000:]
        for f in frames:
            self.handle_frame(f, now)

    def publish_down(self, frame_bytes):
        self.client.publish(f"{self.args.prefix}/down", bytes(frame_bytes), qos=self.args.down_qos)

    # --- ramki ---
    def handle_frame(self, f, now):
        key = (f['type'], f['source'], f['dest'])
        with self.lock:
            self.frames[key][0] += 1
            self.frames[key][1] += f['length']
            self.frame_bytes += f['length']
        if f['type'] == protocol.FRAME_CHECK_DEVICE and f['dest'] == protocol.ADDR_ECONET:
            self.check_times.append(now)
            if self.args.respond_b0:
                frame = protocol.FrameBuilder.device_available_response(f['source'])
                delay = self.args.b0_delay_ms / 1000.0
                if delay > 0:
                    threading.Timer(delay, self.publish_down, args=(frame,)).start()
                else:
                    self.publish_down(frame)
                self.b0_sent += 1
        elif f['type'] == protocol.FRAME_UID_ANS:
            if self.uid_pending is not None:
                rtt = (now - self.uid_pending) * 1000
                self.uid_rtts.append(rtt)
                self.uid_pending = None
                info = EW11AConnection._parse_uid_payload(bytes(f['payload']))
                self.uid_info = info
                log(f"UID: {info.get('uid')} model: {info.get('model')} RTT: {rtt:.0f} ms")

    def send_uid_request(self):
        if self.uid_pending is not None:
            log("UID: brak odpowiedzi na poprzednie zapytanie")
        self.uid_pending = time.monotonic()
        self.uid_sent += 1
        self.publish_down(protocol.FrameBuilder.request_uid())

    # --- przebieg ---
    def run(self):
        a = self.args
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='ecomax-bus-test')
        except AttributeError:  # paho-mqtt 1.x
            client = mqtt.Client(client_id='ecomax-bus-test')
        client.username_pw_set(a.user, os.environ.get('MQTT_PASS', a.password))
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        self.client = client
        client.connect(a.host, a.port, keepalive=30)
        client.loop_start()

        t0 = time.monotonic()
        next_uid = t0 + 5 if a.uid_every else None
        next_report = t0 + 10
        while time.monotonic() - t0 < a.duration:
            time.sleep(0.2)
            now = time.monotonic()
            if next_uid and now >= next_uid:
                self.send_uid_request()
                next_uid = now + a.uid_every
            if now >= next_report:
                log(f"... {self.bytes_total} B, {len(self.msg_sizes)} wiadomosci, "
                    f"{sum(v[0] for v in self.frames.values())} ramek")
                next_report = now + 10
        time.sleep(1)
        client.loop_stop()
        client.disconnect()
        return self.summary(time.monotonic() - t0)

    def summary(self, dur):
        sizes = self.msg_sizes
        gaps = [b - a for a, b in zip(self.check_times, self.check_times[1:])]
        return {
            'duration_s': round(dur, 1),
            'mqtt_messages': len(sizes),
            'messages_per_s': round(len(sizes) / dur, 2),
            'msg_size_median': statistics.median(sizes) if sizes else 0,
            'msg_size_max': max(sizes) if sizes else 0,
            'bytes_total': self.bytes_total,
            'bytes_per_s': round(self.bytes_total / dur, 1),
            'valid_frame_bytes': self.frame_bytes,
            'invalid_bytes': self.bytes_total - self.frame_bytes,
            'check_0x30_count': len(self.check_times),
            'check_0x30_interval_median_s': round(statistics.median(gaps), 2) if gaps else None,
            'b0_sent': self.b0_sent,
            'b0_delay_ms': self.args.b0_delay_ms if self.args.respond_b0 else None,
            'uid_requests': self.uid_sent,
            'uid_answers': len(self.uid_rtts),
            'uid_rtt_ms': [round(x) for x in self.uid_rtts],
            'uid_info': self.uid_info,
            'status_messages': self.status_msgs,
            'frames': sorted(
                ({'type': f'0x{k[0]:02X}', 'src': f'0x{k[1]:02X}', 'dst': f'0x{k[2]:02X}',
                  'count': v[0], 'bytes': v[1], 'per_min': round(v[0] / dur * 60, 1)}
                 for k, v in self.frames.items()),
                key=lambda r: -r['bytes']),
        }


def main():
    ap = argparse.ArgumentParser(description='Test strumienia RS485 przez MQTT')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=1884)
    ap.add_argument('--user', default='tester')
    ap.add_argument('--password', default='', help='lepiej przez zmienna MQTT_PASS')
    ap.add_argument('--prefix', default='ecomax/test')
    ap.add_argument('--duration', type=int, default=120)
    ap.add_argument('--respond-b0', action='store_true')
    ap.add_argument('--b0-delay-ms', type=int, default=0)
    ap.add_argument('--uid-every', type=float, default=0, help='0 = bez zapytan o UID')
    ap.add_argument('--down-qos', type=int, default=1, choices=(0, 1))
    args = ap.parse_args()
    result = BusTest(args).run()
    print(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
