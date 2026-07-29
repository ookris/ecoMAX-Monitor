# ecoMAX Monitor

Aplikacja webowa do monitorowania i sterowania sterownikiem kotła **ecoMAX** (Plum/ecoNET) przez moduł **Elfin EW11A** (konwerter RS485 → TCP/WiFi). Odczytuje dane w czasie rzeczywistym, zapisuje historię do bazy SQLite, pozwala zmieniać ustawienia kotła i udostępnia dashboard oraz REST API.

```
[ecoMAX] --RS485/G2--> [Elfin EW11A] --TCP/WiFi--> [ecoMAX Monitor (Flask + SocketIO)]
```

## Spis treści

- [Funkcje](#funkcje)
- [Wymagania](#wymagania)
- [Instalacja](#instalacja)
- [Konfiguracja EW11A](#konfiguracja-ew11a)
- [Pierwsze uruchomienie i konfiguracja aplikacji](#pierwsze-uruchomienie-i-konfiguracja-aplikacji)
- [Uruchomienie](#uruchomienie)
- [Autostart (systemd)](#autostart-systemd)
- [Strony aplikacji](#strony-aplikacji)
- [REST API](#rest-api)
- [Integracje](#integracje)
- [Struktura plików](#struktura-plików)
- [Protokół komunikacji (ecoNET)](#protokół-komunikacji-econet)
- [Rozwiązywanie problemów](#rozwiązywanie-problemów)

## Funkcje

- **Dashboard** – temperatury (CO, CWU, powrót, podajnik, spaliny, zewnętrzna), moc kotła (% i kW), wentylator, płomień, poziom i strumień paliwa, stan pomp (CO/CWU/cyrkulacja), pogoda dla lokalizacji kotła, aktualizacja na żywo przez WebSocket.
- **Analiza** – wykresy historyczne (1h/6h/24h/7 dni), statystyki min/max/avg, szacowany koszt zużytego paliwa i raport dzienny.
- **Parametry** – podgląd wszystkich dostępnych parametrów/telemetrii zwracanych przez sterownik (tryb tylko do odczytu).
- **Sterowanie kotłem** – odczyt i zapis nastaw sterownika (ecoMAX, mieszacze, termostaty), harmonogramy tygodniowe (grzanie, CWU, cyrkulacja itd.), historia zmian ustawień, opcjonalny profil serwisowy.
- **Alerty** – automatyczne wykrywanie trybu ALARM, zbyt wysokiej temperatury spalin, zbyt niskiej temperatury CO, niskiego poziomu paliwa i braku połączenia z EW11A; powiadomienia na żywo przez WebSocket.
- **Logi** – podgląd logów aplikacji w przeglądarce oraz pobieranie pliku logu.
- **Ustawienia** – cała konfiguracja aplikacji (adres EW11A, interwały, progi alertów, cena paliwa, strefa czasowa/NTP, retencja danych) edytowana z poziomu przeglądarki i zapisywana w bazie SQLite.
- **Integracje** – endpoint REST dla Home Assistant (`/api/ha`) oraz eksport metryk w formacie Prometheus (`/metrics`).
- **Tryb symulacji** – generator losowych danych do testów bez podłączonego sprzętu.

## Wymagania

- **Python 3.10+**
- Moduł **Elfin EW11A** skonfigurowany jako serwer TCP (domyślnie port 8899)
- Sterownik **ecoMAX** podłączony do EW11A przez RS485 (port G2, wtyczka RJ11)
- Komputer/serwer/Raspberry Pi/VM w tej samej sieci co EW11A

## Instalacja

### 1. Pobranie kodu

```bash
git clone https://github.com/ookris/ecoMAX-Monitor.git
cd ecoMAX-Monitor
```

### 2. Środowisko wirtualne (zalecane)

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Instalacja zależności

```bash
pip install -r requirements.txt
```

Zależności: `flask`, `flask-socketio`, `eventlet` (reszta to biblioteka standardowa Pythona).

### 4. Utworzenie katalogu na bazę danych

```bash
mkdir -p data
```

Baza SQLite (`data/measurements.db`) oraz logi (`data/logs/`) zostaną utworzone automatycznie przy pierwszym uruchomieniu.

## Konfiguracja EW11A

Moduł EW11A musi być skonfigurowany w trybie **TCP Server**:

| Parametr   | Wartość            |
|------------|---------------------|
| Baud rate  | **115200**          |
| Data bits  | 8                    |
| Parity     | None                 |
| Stop bits  | 1                    |
| TCP port   | **8899** (domyślny) |

Konfiguracja odbywa się przez panel webowy EW11A (domyślnie `http://10.10.100.254`) lub aplikację mobilną producenta.

> Adres `192.168.1.100` używany w tej instrukcji to tylko przykład (i domyślna wartość w aplikacji). Sprawdź w panelu swojego routera lub w konfiguracji EW11A, jaki adres IP faktycznie przydzielił mu Twój router, i podmieniaj go w poniższych komendach.

Sprawdzenie połączenia przed uruchomieniem aplikacji:

```bash
# Czy port TCP jest otwarty (podmień IP na adres swojego EW11A)
nc -zv 192.168.1.100 8899

# Podgląd surowych danych (Ctrl+C aby zakończyć)
nc 192.168.1.100 8899 | xxd | head -50
```

Jeśli widzisz dane binarne — połączenie działa.

## Pierwsze uruchomienie i konfiguracja aplikacji

Aplikacja **nie ma pliku `config.py`** — cała konfiguracja jest przechowywana w bazie SQLite i edytowana z poziomu strony **Ustawienia** (`/settings`) w przeglądarce. Dopóki adres EW11A nie zostanie zapisany, na dashboardzie widoczny jest baner z linkiem do konfiguracji.

Najważniejsze ustawienia (z domyślnymi wartościami):

| Klucz                     | Domyślnie          | Opis                                            |
|----------------------------|--------------------|--------------------------------------------------|
| `ew11a_host`               | `192.168.1.100`    | Adres IP modułu EW11A                            |
| `ew11a_port`                | `8899`              | Port TCP EW11A                                   |
| `read_interval`             | `5`                 | Co ile sekund odczytywać dane z kotła            |
| `db_save_interval`          | `60`                | Co ile sekund zapisywać próbkę do bazy           |
| `db_retention_days`         | `30`                | Ile dni przechowywać historię pomiarów           |
| `fuel_price_per_ton`        | `1000`              | Cena paliwa (do wyliczania kosztów)              |
| `alert_temp_flue_max`       | `250`               | Próg alertu: temperatura spalin (°C)             |
| `alert_temp_co_min`         | `30`                | Próg alertu: minimalna temperatura CO (°C)       |
| `alert_fuel_level_min`      | `15`                | Próg alertu: minimalny poziom paliwa (%)         |
| `alert_no_connection_min`   | `5`                 | Po ilu minutach braku połączenia zgłosić alert   |
| `weather_lat` / `weather_lon` | *(puste)*         | Współrzędne do pobierania pogody                 |
| `time_zone`                 | `Europe/Warsaw`     | Strefa czasowa aplikacji                         |
| `log_enabled` / `log_retention_days` | `1` / `14` | Logowanie do pliku i jego retencja               |

Zmiany adresu/portu EW11A i interwałów odczytu wymagają restartu aplikacji (patrz [Autostart](#autostart-systemd) → restart usługi).

### Opcjonalne zmienne środowiskowe

Nadpisują wartości z bazy/domyślne i dodają dodatkowe zabezpieczenia dostępu przy udostępnianiu aplikacji poza siecią lokalną:

| Zmienna                        | Zastosowanie                                              |
|---------------------------------|-------------------------------------------------------------|
| `CORS_ALLOWED_ORIGINS`          | Lista dozwolonych originów CORS/WebSocket (domyślnie `*`)   |
| `LOGS_ACCESS_TOKEN`             | Token wymagany do podglądu/pobierania logów spoza LAN       |
| `TIME_ACCESS_TOKEN`             | Token do operacji na czasie systemowym/NTP spoza LAN        |
| `RESET_ACCESS_TOKEN`            | Token do resetu danych/bazy spoza LAN                       |
| `BOILER_CONTROL_ACCESS_TOKEN`   | Token do zmiany ustawień kotła spoza LAN                    |
| `BOILER_SERVICE_ACCESS_TOKEN`   | Token odblokowujący profil serwisowy parametrów kotła        |
| `ASSET_VERSION`                 | Wersja plików statycznych (cache-busting)                    |

Bez skonfigurowanych tokenów dostęp do operacji wrażliwych (logi, czas, reset, sterowanie kotłem) jest ograniczony do sieci lokalnej (loopback/private/link-local).

## Uruchomienie

### Tryb normalny (z EW11A)

```bash
source venv/bin/activate
python run.py
```

### Z niestandardowym adresem EW11A (nadpisuje wartość z bazy)

```bash
python run.py --host 192.168.0.50 --port 8899
```

### Tryb symulacji (bez sprzętu, do testów)

```bash
python run.py --simulate
```

### Tryb debug

```bash
python run.py --debug
```

### Wszystkie opcje

| Opcja           | Opis                                            | Domyślnie         |
|------------------|--------------------------------------------------|--------------------|
| `--host HOST`    | IP modułu EW11A                                  | z bazy / `192.168.1.100` |
| `--port PORT`    | Port TCP EW11A                                   | z bazy / `8899`    |
| `--simulate`     | Tryb symulacji — generuj losowe dane             | wyłączony          |
| `--web-port PORT`| Port serwera WWW                                 | `5050`             |
| `--debug`        | Tryb debug (więcej logów)                        | wyłączony          |

Po uruchomieniu otwórz w przeglądarce `http://<IP_serwera>:5050`.

## Autostart (systemd)

Aby aplikacja uruchamiała się automatycznie po restarcie serwera:

### 1. Utwórz plik usługi systemd

```bash
sudo nano /etc/systemd/system/ecomax-monitor.service
```

Wklej poniższą zawartość — **koniecznie podmień `ubuntu` na swojego użytkownika systemowego oraz ścieżkę na tę, w której faktycznie umieściłeś/aś katalog `ecoMAX-Monitor`** (poniżej jest tylko przykład, u Ciebie może to być np. `/home/pi/ecoMAX-Monitor` albo dowolna inna lokalizacja):

```ini
[Unit]
Description=ecoMAX Monitor - odczyt i sterowanie kotlem
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/ecoMAX-Monitor
Environment="PATH=/home/ubuntu/ecoMAX-Monitor/venv/bin:/usr/bin"
ExecStart=/home/ubuntu/ecoMAX-Monitor/venv/bin/python run.py
Restart=always
RestartSec=10

# Logi
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ecomax-monitor

[Install]
WantedBy=multi-user.target
```

> **Uwaga:** endpoint `POST /api/restart` oraz przycisk restartu w Ustawieniach wywołują `sudo systemctl restart ecomax-monitor` — nazwa usługi musi się zgadzać (`ecomax-monitor`), a użytkownik uruchamiający aplikację powinien mieć uprawnienia `sudo` bez hasła do tej konkretnej komendy (np. wpis w `/etc/sudoers.d/`), jeśli funkcja ma działać z UI.

### 2. Aktywuj i uruchom usługę

```bash
sudo systemctl daemon-reload
sudo systemctl enable ecomax-monitor
sudo systemctl start ecomax-monitor
```

### 3. Zarządzanie usługą

| Polecenie                                        | Opis                          |
|---------------------------------------------------|--------------------------------|
| `sudo systemctl status ecomax-monitor`             | Sprawdź status                |
| `sudo journalctl -u ecomax-monitor -f`             | Podgląd logów (na żywo)       |
| `sudo journalctl -u ecomax-monitor -n 50`          | Ostatnie 50 linii logów       |
| `sudo systemctl restart ecomax-monitor`            | Restart                       |
| `sudo systemctl stop ecomax-monitor`               | Zatrzymanie                   |
| `sudo systemctl disable ecomax-monitor`            | Wyłącz autostart               |

## Strony aplikacji

| Strona            | Adres              | Opis                                                        |
|--------------------|---------------------|----------------------------------------------------------------|
| Dashboard          | `/`                 | Bieżące dane, kafelki sensorów, pogoda, ostatnie alerty        |
| Analiza            | `/analysis`         | Wykresy historyczne, statystyki, koszty paliwa, raport dzienny |
| Parametry          | `/parameters`       | Pełna lista parametrów/telemetrii kotła (odczyt)               |
| Sterowanie         | `/boiler-control`   | Zmiana nastaw kotła, mieszaczy, termostatów i harmonogramów    |
| Alerty             | `/alerts`           | Historia alertów                                                |
| Logi               | `/logs`             | Podgląd i pobieranie logów aplikacji                            |
| Ustawienia         | `/settings`         | Konfiguracja połączenia, alertów, czasu, retencji danych        |

## REST API

Wybrane, najważniejsze endpointy (pełna lista w [web/routes.py](web/routes.py)):

| Endpoint                                    | Opis                                                     |
|-----------------------------------------------|-------------------------------------------------------------|
| `GET /api/data`                               | Aktualne dane z kotła + status połączenia                   |
| `GET /api/parameters`                         | Pełna lista parametrów telemetrii (read-only)                |
| `GET /api/history?hours=24&limit=500`         | Historia pomiarów                                            |
| `GET /api/statistics?hours=24`                | Statystyki (min/max/avg)                                     |
| `GET /api/export`                             | Eksport historii do CSV                                      |
| `GET /api/costs?hours=24`                     | Szacowany koszt zużytego paliwa                              |
| `GET /api/report?days=7`                      | Raport dzienny zużycia paliwa                                |
| `GET /api/alerts` / `POST /api/alerts/read`   | Historia alertów / oznaczenie jako przeczytane               |
| `GET/POST /api/config`                        | Odczyt/zapis konfiguracji aplikacji                           |
| `GET /api/boiler-control/parameters`          | Katalog parametrów sterowania z bieżącymi wartościami         |
| `POST /api/boiler-control/set-parameter`      | Zmiana pojedynczego parametru kotła/mieszacza/termostatu      |
| `POST /api/boiler-control/set-batch`          | Zmiana wielu parametrów naraz                                |
| `GET/POST /api/boiler-control/schedules`      | Odczyt/zapis harmonogramów tygodniowych                       |
| `GET /api/boiler-control/history`             | Historia zmian ustawień kotła                                 |
| `GET /api/weather`                            | Pogoda dla skonfigurowanej lokalizacji                        |
| `GET /api/status`                             | Status połączenia z EW11A                                     |
| `GET /api/setup-status`                       | Czy aplikacja została skonfigurowana                          |
| `GET /api/logs` / `GET /api/logs/download`    | Podgląd / pobranie logów                                     |
| `GET/POST /api/time/status`, `/api/time/set`  | Status i ustawianie czasu systemowego / strefy / NTP           |

Przykład:

```bash
curl http://localhost:5050/api/data | python3 -m json.tool
```

## Integracje

### Home Assistant

```yaml
sensor:
  - platform: rest
    resource: http://<IP>:5050/api/ha
    name: ecoMAX
    json_attributes: [temp_co, temp_cwu, temp_back, temp_flue_gas,
                       boiler_power, fuel_stream, mode_name]
    value_template: '{{ value_json.mode_name }}'
```

### Prometheus

```yaml
scrape_configs:
  - job_name: ecomax
    static_configs:
      - targets: ['<IP>:5050']
    metrics_path: /metrics
```

## Struktura plików

```
ecoMAX-Monitor/
├── run.py                     # Entry point + symulator testowy
├── database.py                # SQLite - historia pomiarów, konfiguracja, alerty
├── alerts.py                  # Silnik alertów
├── sensors.py                 # Definicje kafelków dashboardu i widocznych sensorów
├── logger.py                  # Logowanie do pliku + rotacja/retencja
├── requirements.txt           # Zależności Python
├── econet/
│   ├── protocol.py            # Parser ramek ecoNET (CRC, budowa/rozpoznawanie ramek)
│   ├── decoder.py             # Dekoder danych ecoMAX (ramka 0x35 i inne)
│   ├── connection.py          # Połączenie TCP z EW11A, reconnect, żądania/odpowiedzi
│   └── boiler_control.py      # Katalog parametrów sterowania kotłem
├── web/
│   ├── app.py                 # Flask + SocketIO
│   ├── routes.py              # REST API i routing stron
│   ├── templates/             # Dashboard, Analiza, Parametry, Sterowanie, Alerty, Logi, Ustawienia
│   └── static/                # CSS, JS, obrazy
└── data/
    ├── measurements.db        # Baza danych (tworzona automatycznie)
    └── logs/                  # Logi aplikacji (tworzone automatycznie)
```

## Protokół komunikacji (ecoNET)

Aplikacja komunikuje się ze sterownikiem aktywnie, protokołem **ecoNET** używanym przez sterowniki ecoMAX:

- Format ramki: `[0x68][Długość:2 LE][DA][SA][Typ][Wersja 0x05][Payload][CRC XOR][0x16]`
- Ramka `FRAME_DATA_ANS` (0x35) zawiera bieżące dane telemetryczne kotła
- Obsługiwane są zapytania o parametry (`FRAME_GET_PARAMS`), zapis parametrów (`FRAME_SET_PARAM`), parametry mieszaczy i termostatów, harmonogramy tygodniowe (`FRAME_GET_SCHEDULES` / `FRAME_SET_SCHEDULE`), identyfikator urządzenia (`FRAME_GET_UID`) oraz alarmy
- Aplikacja odpowiada na ramkę `FRAME_DEVICE_AVAILABLE` (0xB0), zgodnie z wymaganiami protokołu

> Dekodowanie ramek ecoNET (typy ramek, struktura payloadu, obliczanie CRC) opiera się na wiedzy zgromadzonej w projekcie [homeassistant-plum-ecomax](https://github.com/denpamusic/homeassistant-plum-ecomax) — integracji Home Assistant dla sterowników Plum ecoMAX autorstwa [denpamusic](https://github.com/denpamusic). Dzięki temu repozytorium możliwe było odtworzenie protokołu bez oficjalnej dokumentacji producenta.

## Rozwiązywanie problemów

### Aplikacja nie łączy się z EW11A

1. Sprawdź czy EW11A jest w sieci: `ping <IP_EW11A>` (adres IP ustawiony w `/settings`)
2. Sprawdź port: `nc -zv <IP_EW11A> 8899`
3. Sprawdź logi: `sudo journalctl -u ecomax-monitor -n 20` lub stronę `/logs`
4. Sprawdź konfigurację EW11A (tryb TCP Server, port 8899, baud 115200) oraz `/settings` w aplikacji

### Dashboard pokazuje brak połączenia

- Aplikacja automatycznie próbuje reconnect z rosnącym opóźnieniem (do 30 s)
- Sprawdź logi — może być problem z siecią lub EW11A

### Brak danych na wykresie

- Dane są zapisywane do bazy co `db_save_interval` sekund (domyślnie 60, konfigurowalne w `/settings`)
- Poczekaj minutę po uruchomieniu i odśwież stronę

### Port 5050 jest zajęty

```bash
python run.py --web-port 8080
```
