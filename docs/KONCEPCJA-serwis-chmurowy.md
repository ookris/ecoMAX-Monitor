# Koncepcja: publiczny serwis chmurowy dla sterowników ecoMAX

Status: **koncepcja, przed testami** (2026-11-22)

## 1. Cel

Serwis www podobny w działaniu do econet24:

- użytkownik zakłada konto, dodaje własny sterownik ecoMAX i zarządza nim z przeglądarki,
- nie loguje się do domowego serwera, nie przekierowuje portów, nie potrzebuje publicznego IP,
- w domu nie ma lokalnego interfejsu www,
- serwis jest dostępny dla wszystkich, więc **bezpieczeństwo danych i sterowania jest priorytetem nr 1**.

Preferencja: **bez budowania dodatkowego sprzętu** (np. modułu na ESP32). Jeśli się da, w domu ma być tylko EW11A.

## 2. Zasada działania

Urządzenie w domu **samo nawiązuje połączenie wychodzące** z chmurą i utrzymuje je cały czas. Komendy z www wracają tym samym kanałem. Z internetu nikt nie łączy się do sieci domowej.

```text
DOM                                       INTERNET (VPS)
[ecoMAX] --RS485--> [EW11A] ==MQTT/TLS, połączenie wychodzące==> [Broker MQTT]
                                                                      │
                                                     [Gateway: ramki ↔ dane/komendy]
                                                                      │
                                                [Backend: API, baza, analityka, alerty]
                                                                      │
                                                         [Strona www] <── użytkownik
```

## 3. Rozważane warianty podłączenia

| Wariant | W domu | Plusy | Minusy |
|---|---|---|---|
| **A. Agent programowy** | EW11A + mały program (Docker / Raspberry Pi / OpenWrt) | pełny TLS z weryfikacją certyfikatu; lokalna walidacja komend i lokalny wyłącznik sterowania; buforowanie danych bez internetu; korzysta z obecnego pakietu `econet/` | wymaga dodatkowego urządzenia |
| **B. EW11A bezpośrednio (MQTT)** | tylko EW11A | zero dodatkowego sprzętu, konfiguracja przez stronę EW11A | TLS bez weryfikacji certyfikatu (patrz pkt 5); opóźnienie odpowiedzi do sterownika (patrz pkt 6); cała logika i walidacja tylko po stronie serwera |
| **C. Własny moduł ESP32 + RS485** | własny moduł zamiast EW11A | pełna kontrola, pełny TLS, tani w produkcji | przepisanie protokołu na C++, produkcja sprzętu; odłożone |

**Obecny kierunek:** wariant **B** jako podstawowy, czyli najprostszy dla użytkownika. Wariant **A** jako opcja dla osób, które chcą pełnego bezpieczeństwa przy sterowaniu. Wariant **C** ewentualnie w przyszłości.

Alternatywa tylko do użytku prywatnego (nie dla publicznego serwisu): Cloudflare Tunnel lub Tailscale Funnel przed obecną aplikacją, bez zmian w kodzie.

## 4. EW11A w trybie MQTT: co wiemy

Źródła: [EW11A manual/](../EW11A%20manual/) (`IOT_Device_Series_Software_Funtion_20250328.pdf`, rozdz. 1.4.7, 1.5, 2.2.51; `HF2211S_EW1X_PW1X_Wport-W30_Operation Guide(20220525).pdf`, rozdz. 5.7).

- EW11A ma tryb **klienta MQTT** i działa w nim jak przezroczysty most:
  - bajty odebrane z RS485 publikuje na *Publish Topic*,
  - wiadomości z *Subscribe Topic* wysyła na RS485.
- Dostępne ustawienia (strona konfiguracji socketu): Connect Mode, Register Mode, Heart Beat, MQTT Version, Client ID, Account, Password, Subscribe/Publish Topic i QoS, Ping Period, Retain Message, Last Will.
- Urządzenie obsługuje do 5 socketów. Do testów można więc prawdopodobnie dodać socket MQTT obok obecnego TCP-SERVER (do sprawdzenia).
- Domyślny login do panelu EW11A to `admin/admin`. Hasło MQTT jest widoczne w panelu z sieci lokalnej.

### Proponowana konfiguracja socketu MQTT

| Pole | Wartość | Uwagi |
|---|---|---|
| Server Address | **adres IP** serwera, nie domena | chroni przed podmianą DNS |
| Sock Security | TLS | jeśli dostępne w trybie MQTT (do sprawdzenia) |
| Connect Mode | Always | |
| Register Mode | Disable | identyfikacja przez Client ID i login |
| Heart Beat | OFF | wystarcza keepalive MQTT |
| MQTT Version | 3.1.1 (jeśli dostępna) | |
| MQTT Client ID | `dev-<id>` | generuje serwis |
| MQTT Account / Password | `<id>` / długi losowy token | osobne dla każdego urządzenia, z możliwością unieważnienia |
| Subscribe Topic | `d/<id>/down` | komendy do sterownika |
| Subscribe QoS | 1 | ponowny zapis tej samej wartości jest nieszkodliwy |
| Publish Topic | `d/<id>/up` | strumień bajtów z RS485 |
| Publish QoS | 0 | dekoder odrzuca uszkodzone ramki (CRC) i synchronizuje się dalej |
| Ping Period | 30 s | |
| **Retain Message** | **OFF, obowiązkowo** | inaczej stara komenda trafiałaby do kotła po każdym ponownym połączeniu |
| Last Will | ON, `d/<id>/status` = `offline` | natychmiastowa informacja o utracie łączności |

## 5. TLS w EW11A: ograniczenie

Według instrukcji:

> „TLS use no certificate method.” (rozdz. 1.5)
>
> „TLS: TLS1.2 encryption, We use no certificate method. Only support in TCP client mode.” (opis `Sock Security`, s. 51–52)

Wnioski:

- **Chroni przed podsłuchem.** Dostawca internetu ani nikt na trasie nie odczyta hasła MQTT, danych ani komend.
- **Nie chroni przed podszyciem się pod serwer (MITM).** EW11A nie weryfikuje certyfikatu. Ktoś, kto przejmie ruch na trasie (zhakowany router, podmieniony DNS, atak u operatora), może udawać broker, przejąć login i hasło oraz wysyłać ramki do kotła.
- **Skala ryzyka:**
  - Wysłanie czegoś do kotła wymaga **stałej obecności na trasie** konkretnego domu, więc nie da się tego łatwo przeprowadzić masowo.
  - Samo wykradzione hasło nie daje dostępu do kotła. Pozwala tylko połączyć się z brokerem jako urządzenie: wysyłać fałszywe dane i czytać komendy.
  - Kotły mają zwykle sprzętowy ogranicznik temperatury (STB), niezależny od oprogramowania (do potwierdzenia dla konkretnego kotła).
- Opcja **AES** w EW11A nie jest lepszą alternatywą. Klucz jest stały, IV równe kluczowi (CBC) i nie ma kontroli integralności.
- Niewiadome:
  - czy TLS działa przy protokole MQTT, bo instrukcja wspomina tylko „TCP client mode”,
  - jakie zestawy szyfrów obsługuje EW11A (ma 352 KB RAM, więc pewnie starsze szyfry TLS 1.2),
  - czy wysyła SNI. Jeśli nie, broker musi mieć własny IP i port, bez Cloudflare i wspólnych proxy.

### Środki zaradcze w serwisie

- Domyślnie **tylko podgląd**. Sterowanie użytkownik włącza świadomie, po informacji o ograniczeniu.
- Dla chcących pełnego bezpieczeństwa: **wariant A** (agent z weryfikacją certyfikatu).
- Adres brokera wpisany w EW11A jako IP.
- Osobne dane logowania dla każdego urządzenia, unieważniane jednym kliknięciem.
- Wykrywanie anomalii: dwa równoczesne połączenia na tych samych danych logowania, nagła zmiana kraju adresu IP.
- Powiadomienie użytkownika o każdej zmianie nastaw wykonanej przez serwis.
- Zmiana hasła `admin/admin` EW11A jako obowiązkowy krok instrukcji.
- Śledzenie nowych wersji firmware Elfina (ewentualne dodanie weryfikacji certyfikatu).

## 6. Opóźnienie odpowiedzi do sterownika: kluczowe ryzyko

Sterownik wysyła ramkę `0x30` (CHECK_DEVICE), a aplikacja odpowiada natychmiast ramką `0xB0` ([econet/connection.py](../econet/connection.py), obsługa `FRAME_CHECK_DEVICE`). W wariancie B odpowiedź przechodzi trasę dom → serwer → dom, co daje typowo 50–300 ms zamiast kilku milisekund.

Nie znamy limitu czasu ecoMAX. Możliwe skutki za późnej odpowiedzi:

- sterownik uzna moduł za nieobecny: przestanie wysyłać część ramek albo odrzuci zapisy,
- spóźniona ramka zderzy się na magistrali RS485 z inną transmisją, np. z panelem.

Zapisy parametrów są mniej wrażliwe: aplikacja czeka na potwierdzenie (`0xB3`/`0xB4`/`0xDD`) do 2 s i ten limit można wydłużyć.

**Ten test rozstrzyga o wykonalności wariantu B.** Jeśli wypadnie źle, zostaje wariant A.

## 7. Strona serwerowa

- **Broker MQTT** (EMQX albo Mosquitto z wtyczką auth) z kontrolą dostępu z bazy danych:
  - urządzenie widzi wyłącznie `d/<id>/up`, `d/<id>/down`, `d/<id>/status`,
  - na `down` pisze wyłącznie backend.
- **Gateway:** subskrybuje `d/+/up` i dla każdego urządzenia składa strumień bajtów w ramki.
  - W `EW11AConnection` trzeba wydzielić **warstwę transportu**: dziś socket TCP, docelowo także MQTT.
  - Parser, dekoder (`decoder.py`, `regdata.py`, `alarms.py`) i `boiler_control.py` zostają bez zmian.
- **Komendy:**
  - na `down` trafiają wyłącznie ramki zbudowane przez `FrameBuilder`, po walidacji zakresów,
  - biała lista operacji, nigdy surowe bajty od użytkownika,
  - limit częstotliwości zmian, dziennik zmian (kto, kiedy, skąd).
- **Baza:** PostgreSQL (najlepiej z TimescaleDB) zamiast SQLite. Każde zapytanie dotyczy zalogowanego użytkownika i wybranego `device_id`.
- **Frontend:** obecne strony i JS z niewielkimi zmianami (wybór urządzenia). Powiadomienia także przez push, e-mail lub Telegram.
- **Konta:** logowanie z 2FA, udostępnianie sterownika innym osobom z rolami „podgląd” i „sterowanie”, przełącznik „tylko podgląd” dla każdego urządzenia.
- **Hosting na start:** VPS (ok. 20–40 zł/mies.), Docker: broker, gateway, backend, baza i Caddy (TLS).

### Parowanie (dodawanie sterownika)

1. Użytkownik w serwisie wybiera „Dodaj sterownik” i podaje UID sterownika odczytany z panelu kotła.
2. Serwis generuje gotowy zestaw ustawień do wpisania w EW11A: IP, port, Client ID, login, hasło, tematy.
3. Po pierwszym połączeniu serwer odpytuje sterownik o UID (`request_uid`, ramka `0xB9`) i porównuje go z podanym. Zgodność kończy parowanie.
4. Do czasu pozytywnej weryfikacji urządzenie nie przyjmuje żadnych komend.

## 8. Zmiany w obecnym kodzie (przygotowanie)

1. Wydzielenie warstwy transportu z `econet/connection.py` (TCP i MQTT jako implementacje).
2. Rozdzielenie warstw „edge” (połączenie i protokół) oraz „app” (www, baza, analityka). Dziś obie są połączone w `run.py` i `controller_sync.py`.
3. Tryb bez interfejsu (np. `run.py --agent`) jako baza dla wariantu A.

## 9. Plan testów (następny krok)

Na serwerze domowym (10.0.0.114):

1. Mosquitto z TLS (certyfikat self-signed jest tu wystarczający, bo EW11A i tak go nie weryfikuje).
2. Drugi socket w EW11A w trybie MQTT, obok obecnego TCP-SERVER, jeśli firmware na to pozwala.
3. Skrypt testowy (osobny, bez zmian w głównej aplikacji):
   - subskrybuje `up` i przekazuje bajty do obecnego dekodera; sprawdza poprawność ramek, przepływność (B/s) i sposób, w jaki EW11A dzieli strumień na wiadomości,
   - odpowiada na `0x30` z rosnącym opóźnieniem (0 / 100 / 300 / 1000 ms) i obserwuje reakcję sterownika,
   - testowy zapis parametru przez `down` i pomiar czasu do potwierdzenia.
4. Sprawdzenie:
   - czy TLS działa przy protokole MQTT,
   - czy EW11A łączy się z brokerem o dowolnym certyfikacie (potwierdzenie braku weryfikacji),
   - jakie wersje TLS i zestawy szyfrów negocjuje, czy wysyła SNI.

## 10. Etapy wdrożenia (po pozytywnych testach)

1. Gateway i broker dla jednego urządzenia; tylko podgląd.
2. Sterowanie przez `down` z walidacją, potwierdzeniami i dziennikiem zmian.
3. Wielu użytkowników: konta, parowanie, kontrola dostępu w brokerze, PostgreSQL.
4. Wariant A (agent z pełnym TLS) jako opcja.
5. Ewentualnie wariant C (ESP32).

## 11. Kwestie poza techniką

- Odpowiedzialność za zdalne sterowanie cudzymi kotłami: regulamin, jasne komunikaty o ryzyku.
- RODO: dane o obecności domowników da się wyczytać z harmonogramów i trybów pracy. Potrzebne polityka prywatności i umowa powierzenia, jeśli są podwykonawcy.
- Nazwa serwisu bez „ecoNET” i „econet24” (znaki towarowe Plum).
- Protokół jest rozpracowany nieoficjalnie. Zmiana firmware sterownika może wymagać szybkiej poprawki gatewaya.
