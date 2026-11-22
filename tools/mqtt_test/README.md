# Test EW11A: MQTT z TLS

Zestaw do sprawdzenia trybu MQTT w EW11A (koncepcja: [docs/KONCEPCJA-serwis-chmurowy.md](../../docs/KONCEPCJA-serwis-chmurowy.md), pkt 9).

| Plik | Rola |
|---|---|
| `setup.sh` | tworzy certyfikat self-signed, konta, ACL i `mosquitto.conf` |
| `tls_probe.py` | sonda TLS na porcie 8884: loguje, co EW11A oferuje w handshake (wersja TLS, szyfry, SNI), i przekazuje ruch do Mosquitto |
| `bus_test.py` | odbiera strumień z `ecomax/test/up`, składa ramki, zbiera statystyki; opcjonalnie odpowiada B0 (z opóźnieniem) i mierzy czas odpowiedzi na zapytanie o UID |

```text
EW11A ──TLS──> :8884 tls_probe.py ──> 127.0.0.1:1884 Mosquitto <── bus_test.py
EW11A ──TLS──> :8883 Mosquitto (natywny TLS, wariant docelowy)
```

Broker musi stać w sieci, do której EW11A ma dostęp, czyli na serwerze domowym (10.0.0.114). Mac łączy się z tą siecią przez VPN, więc EW11A nie nawiąże połączenia z Makiem.

## 1. Przygotowanie serwera (jednorazowo)

Z Maca (katalog repozytorium):

```bash
rsync -av econet tools ookris@10.0.0.114:~/mqtt_test/repo/
```

Na serwerze:

```bash
sudo apt install mosquitto mosquitto-clients
sudo systemctl disable --now mosquitto      # usługa systemowa niepotrzebna, broker uruchamiamy ręcznie

python3 -m venv ~/mqtt_test/venv
~/mqtt_test/venv/bin/pip install paho-mqtt

bash ~/mqtt_test/repo/tools/mqtt_test/setup.sh ~/mqtt_test
# wypisze hasła (zapisane też w ~/mqtt_test/creds.txt)
# gotowe hasła można podać: EW_PASS=... TEST_PASS=... bash .../setup.sh ~/mqtt_test

# jeśli działa ufw:
sudo ufw allow from 10.0.0.226 to any port 8883:8884 proto tcp
```

## 2. Uruchomienie (trzy terminale na serwerze)

```bash
# T1: broker
mosquitto -c ~/mqtt_test/mosquitto.conf

# T2: sonda TLS
~/mqtt_test/venv/bin/python ~/mqtt_test/repo/tools/mqtt_test/tls_probe.py \
    --cert ~/mqtt_test/certs/server.crt --key ~/mqtt_test/certs/server.key

# T3: test (hasło użytkownika tester z creds.txt)
export MQTT_PASS='...'
~/mqtt_test/venv/bin/python ~/mqtt_test/repo/tools/mqtt_test/bus_test.py --duration 120
```

## 3. Konfiguracja EW11A (nowy socket)

Obecny socket `netp` (TCP-SERVER, 8899) **zostaw bez zmian**. Dodaj nowy:

| Pole | Wartość | Uwagi |
|---|---|---|
| Name | `mqtt-test` | |
| Protocol | MQTT | |
| Server | `10.0.0.114` | |
| Server Port | **`8884`** (sonda), potem `8883` | patrz kolejność testów |
| Local Port | `0` | losowy, właściwy dla klienta |
| Buffer Size | `512` | domyślne; największa ramka ma ~324 B |
| Keep Alive(s) | `60` | keepalive TCP |
| Timeout(s) | `300` | zrywa połączenie, gdy przez 300 s nic nie przyjdzie z sieci; odpowiedzi na ping MQTT co 30 s to zapewniają |
| Connect Mode | Always | |
| Register Mode | Disable | |
| Heart Beat | OFF | |
| MQTT Version | `4` (= 3.1.1), jeśli jest; inaczej `3` | |
| MQTT Client ID | `ew11a-test` | |
| MQTT Account | `ew11a` | |
| MQTT Password | hasło `ew11a` z `creds.txt` | |
| Subscribe Topic | `ecomax/test/down` | |
| Subscribe QoS | `1` | |
| Publish Topic | `ecomax/test/up` | |
| Publish QoS | `0` | |
| Ping Period(s) | `30` | |
| Retain Message | **OFF** | obowiązkowo |
| Last Will And Testament | ON | jeśli pojawią się pola: temat `ecomax/test/status`, treść `offline` |
| Security | **TLS** | jeśli pojawi się pole klucza, zostaw puste (dotyczy AES/DES3) |
| Route | `uart` | dane z brokera trafiają na RS485 |

Po zapisaniu może być potrzebny restart EW11A.

## 4. Kolejność testów

**A. Handshake TLS (port 8884, sonda).** W terminalu T2 pojawi się wpis:
```text
ClientHello: TLS1.2, SNI: None, ...
Oferowane szyfry (...): ...
Handshake OK: TLSv1.2, <szyfr>
```
Zapisz cały wpis: odpowiada na pytania o wersję TLS, szyfry i SNI (pkt 5 koncepcji). W T1 Mosquitto powinien pokazać `New client connected ... as ew11a-test`.

**B. Strumień bez wysyłania (aplikacja może działać).**
```bash
bus_test.py --duration 120
```
Porównaj z pomiarem TCP (~250 B/s, ~3,3 porcji/s, 0 błędnych bajtów).

Sprawdź też, czy aplikacja na `netp` dalej dostaje dane. Odpowie to na pytanie, czy EW11A wysyła dane z RS485 do obu socketów naraz.

**C. Last Will.** W trakcie testu B wyłącz socket w EW11A albo odłącz moduł od zasilania. Po ok. 45 s (1,5 × Ping Period) skrypt powinien wypisać `STATUS: 'offline'`.

**D. Ruch w obie strony (aplikacja musi być zatrzymana!).**
```bash
sudo systemctl stop ecomax-monitor
bus_test.py --duration 120 --respond-b0 --uid-every 15
bus_test.py --duration 180 --respond-b0 --b0-delay-ms 300 --uid-every 15
bus_test.py --duration 180 --respond-b0 --b0-delay-ms 1000 --uid-every 15
sudo systemctl start ecomax-monitor
```
- `uid_answers` równe `uid_requests` oznacza, że zapytania przez MQTT docierają do sterownika, a odpowiedzi wracają. Wynik `uid_rtt_ms` pokazuje czas pełnej pętli w sieci lokalnej.
- Obserwuj, czy przy opóźnionym B0 zmienia się rytm `0x30` (`check_0x30_interval_median_s`) lub zestaw ramek.
- Zapis parametrów sprawdzimy w osobnym kroku, po pozytywnym wyniku tego testu.

**E. Natywny TLS w Mosquitto (port 8883).** Zmień w EW11A Server Port na `8883` i sprawdź w T1, czy moduł się łączy. Domyślne szyfry Mosquitto (OpenSSL 3) są ostrzejsze niż w sondzie. Jeśli połączenie nie wyjdzie, porównaj z listą szyfrów z testu A.

**F. Domena zamiast IP (opcjonalnie).** Wpisz w polu Server `10.0.0.114.nip.io` (publiczny DNS, który zwraca adres zawarty w nazwie).
- Nieudane połączenie może być winą routera, który blokuje odpowiedzi DNS z prywatnymi adresami (tzw. ochrona przed DNS rebinding), a nie EW11A.
- Pełny test domeny zrobimy na VPS-ie.

## 5. Po testach

Wyłącz lub usuń socket `mqtt-test` w EW11A, żeby moduł nie próbował się łączyć z wyłączonym brokerem. Jeśli aplikacja była zatrzymana, uruchom ją ponownie.
