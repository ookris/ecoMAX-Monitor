#!/usr/bin/env bash
# Przygotowanie testowego brokera MQTT (Mosquitto) z TLS dla EW11A.
#
# Tworzy w katalogu docelowym (domyslnie ~/mqtt_test):
#   certs/server.{crt,key}  - certyfikat self-signed RSA 2048 (EW11A i tak go nie weryfikuje)
#   passwd, acl             - konta: ew11a (modul) i tester (skrypt testowy)
#   mosquitto.conf          - listenery: 1884 (127.0.0.1, bez TLS, dla skryptu)
#                                        8883 (TLS, natywny Mosquitto)
#   creds.txt               - wygenerowane hasla (chmod 600)
#
# Wymaga: mosquitto + mosquitto_passwd (apt install mosquitto), openssl.
set -euo pipefail

DIR="${1:-$HOME/mqtt_test}"
PREFIX="${2:-ecomax/test}"

command -v mosquitto_passwd >/dev/null || { echo "Brak mosquitto_passwd (sudo apt install mosquitto)"; exit 1; }

mkdir -p "$DIR/certs"
cd "$DIR"

if [[ ! -f certs/server.key ]]; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
        -subj "/CN=ecomax-mqtt-test" \
        -keyout certs/server.key -out certs/server.crt 2>/dev/null
    chmod 600 certs/server.key
fi

# Hasla mozna podac z zewnatrz (EW_PASS=... TEST_PASS=... bash setup.sh), inaczej losowe.
EW_PASS="${EW_PASS:-$(openssl rand -hex 8)}"      # 16 znakow - krotko na wypadek limitu pola w EW11A
TEST_PASS="${TEST_PASS:-$(openssl rand -hex 16)}"

rm -f passwd
touch passwd && chmod 600 passwd
mosquitto_passwd -b passwd ew11a "$EW_PASS"
mosquitto_passwd -b passwd tester "$TEST_PASS"

cat > acl <<EOF
# Modul EW11A: publikuje strumien z RS485 i status, czyta tylko komendy
user ew11a
topic write $PREFIX/up
topic write $PREFIX/status
topic read $PREFIX/down

# Skrypt testowy
user tester
topic readwrite $PREFIX/#
EOF
chmod 600 acl

cat > mosquitto.conf <<EOF
persistence false
log_dest stdout
log_type error
log_type warning
log_type notice
log_type information
connection_messages true
log_timestamp_format %Y-%m-%d %H:%M:%S

per_listener_settings false
allow_anonymous false
password_file $DIR/passwd
acl_file $DIR/acl

# Lokalnie dla skryptu testowego i sondy TLS (bez TLS, tylko localhost)
listener 1884 127.0.0.1

# Docelowy wariant: TLS bezposrednio w Mosquitto
listener 8883 0.0.0.0
certfile $DIR/certs/server.crt
keyfile $DIR/certs/server.key

# Bez TLS (tylko do testow w LAN): firmware EW11A 1.50.0 nie wlacza TLS w trybie MQTT
listener 1883 0.0.0.0
EOF

cat > creds.txt <<EOF
EW11A (MQTT Account / Password): ew11a / $EW_PASS
Skrypt testowy (tester):         tester / $TEST_PASS
Tematy: $PREFIX/up  $PREFIX/down  $PREFIX/status
EOF
chmod 600 creds.txt

echo "Gotowe w $DIR"
cat creds.txt
echo
echo "Start brokera:  mosquitto -c $DIR/mosquitto.conf"
