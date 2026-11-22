# Koncepcja: publiczny serwis chmurowy dla sterowników ecoMAX

Status: **koncepcja; po testach EW11A kierunek zmieniony na moduł ESP32 DIY** (2026-11-22, szczegóły w pkt 12)

## 1. Cel

Serwis www podobny w działaniu do econet24:

- użytkownik zakłada konto, dodaje własny sterownik ecoMAX i zarządza nim z przeglądarki,
- nie loguje się do domowego serwera, nie przekierowuje portów, nie potrzebuje publicznego IP,
- w domu nie ma lokalnego interfejsu www,
- serwis jest dostępny dla wszystkich, więc **bezpieczeństwo danych i sterowania jest priorytetem nr 1**.

Pierwotna preferencja: bez dodatkowego sprzętu, tylko EW11A. **Zmieniona po testach (pkt 9a):** EW11A nie obsługuje TLS, więc w domu będzie moduł ESP32 z własnym firmware (pkt 12).

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
| **C. Własny moduł ESP32 + RS485** | moduł ESP32 zamiast EW11A (DIY) | pełny TLS z weryfikacją certyfikatu, lokalna odpowiedź na `0x30`, filtr komend, tani | własny firmware; użytkownik składa i programuje moduł sam |

**Obecny kierunek (po testach z pkt 9a): wariant C**, moduł ESP32 w modelu DIY (pkt 12). Wariant B odpada, bo EW11A z firmware 1.50.0 nie obsługuje TLS. Wariant A pozostaje opcją dla osób, które już mają EW11A i serwer w domu.

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
| Server Address | domena `mqtt.<domena>` (DNS only) albo adres IP | patrz pkt 5a; obsługa domeny do sprawdzenia w teście |
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

### Inne tryby EW11A: HTTP i WebSocket

EW11A ma też tryby **HTTP** (rozdz. 1.4.4, opis socketu s. 51–52) i **WebSocket** (rozdz. 1.4.6):

- **HTTP:**
  - każda porcja danych z RS485 to osobne zapytanie POST lub GET,
  - odpowiedź serwera, po usunięciu nagłówków, trafia **prosto na RS485**,
  - można dodać własne nagłówki do 250 B, np. z tokenem urządzenia.
- **WebSocket:** stałe połączenie w obie strony.
- **TLS:** instrukcja nie mówi, czy TLS działa w tych trybach. Pisze tylko „Only support in TCP client mode”.

| | MQTT | HTTP | WebSocket |
|---|---|---|---|
| Połączenie | stałe, dwukierunkowe | zapytanie na każdą porcję (~3,3/s) | stałe, dwukierunkowe |
| Komendy do kotła | w dowolnej chwili | tylko w odpowiedzi na zapytanie | w dowolnej chwili |
| Narzut | ~85 B/wiadomość | ~150–250 B nagłówków/zapytanie | mały |
| Status online/offline | Last Will | po braku zapytań | po zerwaniu połączenia |
| Kontrola dostępu | ACL tematów w brokerze | własna w aplikacji | własna w aplikacji |
| Przez proxy Cloudflare | nie (darmowy plan) | tak, jeśli HTTPS z SNI | tak, jeśli wss z SNI |

**Ryzyko trybu HTTP:** każda niepusta odpowiedź trafia na magistralę. Strona błędu serwera lub proxy, odpowiedź 404 itp. poszłaby do sterownika jako bajty. Sterownik prawdopodobnie odrzuci ją przez zły CRC, ale nie należy na tym polegać.

**Wniosek:** podstawą pozostaje MQTT. HTTPS i wss mają sens tylko wtedy, gdy EW11A przejdzie z nimi przez Cloudflare. Daje to darmową ochronę przed DDoS i ukrycie IP serwera, ale wymaga TLS w tych trybach, wysyłania SNI i zgodnych zestawów szyfrów, co przy 352 KB RAM jest wątpliwe. Sprawdzamy przy okazji testów (pkt 9).

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
- Ochrona adresu brokera wpisanego w EW11A: patrz pkt 5a.
- Osobne dane logowania dla każdego urządzenia, unieważniane jednym kliknięciem.
- Wykrywanie anomalii: dwa równoczesne połączenia na tych samych danych logowania, nagła zmiana kraju adresu IP.
- Powiadomienie użytkownika o każdej zmianie nastaw wykonanej przez serwis.
- Zmiana hasła `admin/admin` EW11A jako obowiązkowy krok instrukcji.
- Śledzenie nowych wersji firmware Elfina (ewentualne dodanie weryfikacji certyfikatu).

## 5a. Adres brokera w EW11A: IP czy domena

Według instrukcji EW11A przyjmuje adres serwera jako IPv4 lub nazwę domeny („Input Server Address: Set server IPv4 address or domain name”, `IOT_Device_Series_Software_Funtion_20250328.pdf`, s. 52). Pomoc w panelu EW11A mówi to samo.

| | Adres IP | Domena (`mqtt.<domena>`) |
|---|---|---|
| Zmiana serwera lub dostawcy | wymaga przenoszalnego IP albo rekonfiguracji wszystkich modułów | wystarczy zmiana rekordu A |
| Podmiana DNS (zatruty resolver, przejęty router) | nie dotyczy | możliwa. Ponieważ EW11A nie weryfikuje certyfikatu, podmiana kieruje moduł do fałszywego brokera. To jednak ta sama klasa ataku co MITM na trasie (pkt 5), przed którym IP też nie chroni. |
| Przejęcie konta u rejestratora lub w Cloudflare | nie dotyczy | **przekierowanie wszystkich modułów naraz**: największe nowe ryzyko |
| Wygaśnięcie domeny | nie dotyczy | utrata łączności ze wszystkimi modułami |

**Rekomendacja:** domena, bo przy setkach modułów elastyczność jest bardzo cenna, ale pod warunkami:

- osobna subdomena tylko dla MQTT, rekord **DNS only** (bez proxy Cloudflare, które nie przepuszcza dowolnego TCP w darmowym planie),
- włączony DNSSEC (EW11A go nie sprawdza, ale sprawdza go wiele resolverów operatorów),
- konto rejestratora i Cloudflare z 2FA (najlepiej klucz sprzętowy), blokada transferu domeny, automatyczne odnawianie na kilka lat,
- przenoszalny IP mimo wszystko, jako zabezpieczenie migracji.

Jeśli testy wykażą problemy z obsługą domeny (pkt 9), wracamy do IP.

## 6. Opóźnienie odpowiedzi do sterownika: kluczowe ryzyko

Sterownik wysyła ramkę `0x30` (CHECK_DEVICE), a aplikacja odpowiada natychmiast ramką `0xB0` ([econet/connection.py](../econet/connection.py), obsługa `FRAME_CHECK_DEVICE`). W wariancie B odpowiedź przechodzi trasę dom → serwer → dom, co daje typowo 50–300 ms zamiast kilku milisekund.

Nie znamy limitu czasu ecoMAX. Możliwe skutki za późnej odpowiedzi:

- sterownik uzna moduł za nieobecny: przestanie wysyłać część ramek albo odrzuci zapisy,
- spóźniona ramka zderzy się na magistrali RS485 z inną transmisją, np. z panelem.

Zapisy parametrów są mniej wrażliwe: aplikacja czeka na potwierdzenie (`0xB3`/`0xB4`/`0xDD`) do 2 s i ten limit można wydłużyć.

**Ten test rozstrzyga o wykonalności wariantu B.** Jeśli wypadnie źle, zostaje wariant A.

Pomiar z 2026-11-22 (pkt 6a) częściowo łagodzi to ryzyko. Ramki danych `0x08` i `0x35` są rozgłaszane (adres `0x00`) **niezależnie od tego, czy ktoś odpowiada na `0x30`**. Podgląd działa więc nawet przy opóźnionej odpowiedzi. Otwarte pozostaje, czy sterownik przyjmuje zapisy parametrów od modułu, który odpowiada z opóźnieniem.

## 6a. Pomiar ruchu z magistrali (2026-11-22)

Pomiar wykonany skryptem [tools/measure_bus.py](../tools/measure_bus.py) bezpośrednio na EW11A (TCP 8899), przy wyłączonej aplikacji. Pierwsza faza trwała 120 s i była tylko nasłuchem. Druga trwała 180 s i odpowiadała ramką `0xB0` na każde `0x30`, tak jak aplikacja. Obie fazy dały praktycznie identyczny wynik.

| Wielkość | Wynik |
|---|---|
| Surowy ruch z RS485 | **~250 B/s** (~21,6 MB/dobę) |
| Ramki z błędem CRC | 0 (wszystkie bajty należały do poprawnych ramek) |
| Porcje danych wysyłane przez EW11A | ~3,3 na sekundę, mediana 10 B, maks. 324 B (≈ jedna porcja na ramkę) |
| Szacowany ruch jako MQTT + TLS | **~530 B/s** (narzut ~85 B na wiadomość: MQTT + rekord TLS + TCP/IP) |
| Zapytanie `0x30` do ecoNET | co ~2,15 s, także bez odpowiedzi |

Ramki na magistrali (na minutę):

| Ramka | Od → do | /min | Udział w bajtach |
|---|---|---|---|
| `0x08` dane regulatora | ecoMAX → wszyscy | 26 | ~56% |
| `0x35` dane bieżące | ecoMAX → wszyscy | 26 | ~29% |
| `0x89` czas | panel `0x50` → wszyscy | 24 | ~6% |
| `0x40`, `0x0A`, `0x30` | ecoMAX → moduły `0x50`/`0x51`/`0x55`–`0x59` | ~26 każda (część 6,5) | po ~2% |
| `0x6A` | panel → `0x59` | 2,5 | <1% |

Wnioski:

- Ruch jest mały i regularny (cykl sterownika ~2,3 s), praktycznie niezależny od stanu kotła.
- Dla 500 sterowników:
  - ~125 KB/s surowych danych, ~265 KB/s (~2,1 Mbit/s) z narzutem MQTT i TLS,
  - **~0,7 TB miesięcznie** ruchu przychodzącego,
  - ~1650 wiadomości MQTT na sekundę. To niewiele: Mosquitto obsługuje dziesiątki tysięcy.
- Instalacje z większą liczbą modułów (mieszacze, termostaty) mogą generować więcej ruchu, więc w obliczeniach przyjęto zapas ×2.
- Pomiar dotyczy trybu TCP. W trybie MQTT EW11A może dzielić strumień na wiadomości inaczej (zależnie od ustawień bufora), co trzeba potwierdzić w teście z brokerem.

## 6b. Wymagania serwera (500 użytkowników)

| Zasób | Minimum | Zalecane na start |
|---|---|---|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 4 GB | 8 GB |
| Dysk | 80 GB SSD | 160 GB NVMe + backup poza serwerem |
| Transfer | ~1,5 TB/mies. (pomiar ×2) | standardowy pakiet (np. 20 TB) |
| IPv4 | stały | **stały i przenoszalny** (Hetzner Primary/Floating IP, OVH Additional IP) |
| System | Debian 12 / Ubuntu 24.04 + Docker, pełny root | |
| Lokalizacja | UE (RODO) | |

Uzasadnienie:

- **CPU:** gateway parsuje ok. 125 KB/s, czyli niewiele nawet dla Pythona. Broker z 500 połączeniami TLS: Mosquitto poniżej 100 MB RAM, EMQX ok. 0,5 GB.
- **RAM:**
  - PostgreSQL/TimescaleDB 2–3 GB,
  - gateway 0,5–1 GB,
  - backend 0,5 GB,
  - broker 0,1–0,5 GB.
- **Dysk:**
  - zapis pomiaru co 60 s (`db_save_interval`) daje 500 × 1440 = ~720 tys. wierszy/dobę, czyli ~65 GB rocznie bez kompresji i ~5–15 GB po kompresji TimescaleDB,
  - polityka: pełna rozdzielczość np. 90 dni, potem średnie 5-minutowe,
  - surowego strumienia nie zapisujemy (najwyżej krótki bufor diagnostyczny).
- **Użytkownicy www:** jednocześnie online realnie 20–100 osób.

Wymagania ważniejsze niż moc obliczeniowa:

1. **Stały, przenoszalny IP dla MQTT.** Jeśli EW11A łączą się po IP, zmiana adresu oznacza ręczną rekonfigurację wszystkich modułów. Przy połączeniu po domenie (pkt 5a) to wymaganie słabnie, ale przenoszalny IP nadal ułatwia migrację.
2. **Własny port MQTT (np. 8883) bezpośrednio na serwerze.** Bez Cloudflare i wspólnych proxy, bo EW11A prawdopodobnie nie wysyła SNI i ma ograniczony wybór szyfrów.
3. **Backup poza serwerem:** codzienny zrzut bazy w innej lokalizacji, snapshoty i sprawdzona procedura odtworzenia.
4. **Zewnętrzna usługa e-mail** (Postmark, Mailgun, SES) do weryfikacji kont, resetu haseł i alertów.
5. **Domena** z certyfikatem Let's Encrypt (Caddy) dla www i API.

Przykład: VPS klasy Hetzner CPX31 lub odpowiednik w OVH, ok. 15–25 €/mies. (ceny do sprawdzenia), plus kilka euro za backup.

**Dostępność:** awaria serwera odbiera zdalny dostęp, ale **kotły pracują normalnie**, bo EW11A nie jest potrzebny sterownikowi do pracy. Przy kilku tysiącach użytkowników kolejne kroki to: osobny serwer bazy, drugi broker z gatewayem, przełączanie stałego IP na zapasową maszynę.

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
- **Hosting na start:** VPS z Dockerem (broker, gateway, backend, baza i Caddy); parametry w pkt 6b.

### Parowanie (dodawanie sterownika)

1. Użytkownik w serwisie wybiera „Dodaj sterownik” i podaje UID sterownika odczytany z panelu kotła.
2. Serwis generuje gotowy zestaw ustawień do wpisania w EW11A: IP, port, Client ID, login, hasło, tematy.
3. Po pierwszym połączeniu serwer odpytuje sterownik o UID (`request_uid`, ramka `0xB9`) i porównuje go z podanym. Zgodność kończy parowanie.
4. Do czasu pozytywnej weryfikacji urządzenie nie przyjmuje żadnych komend.

## 8. Zmiany w obecnym kodzie (przygotowanie)

1. Wydzielenie warstwy transportu z `econet/connection.py` (TCP i MQTT jako implementacje).
2. Rozdzielenie warstw „edge” (połączenie i protokół) oraz „app” (www, baza, analityka). Dziś obie są połączone w `run.py` i `controller_sync.py`.
3. Tryb bez interfejsu (np. `run.py --agent`) jako baza dla wariantu A.

## 9. Plan testów EW11A

Wykonany 2026-11-22 w zakresie TLS i strumienia MQTT; wyniki w pkt 9a. Testy domeny, HTTP i WebSocket stały się zbędne po zmianie kierunku (pkt 12).

Na serwerze domowym (10.0.0.114):

1. Mosquitto z TLS (certyfikat self-signed jest tu wystarczający, bo EW11A i tak go nie weryfikuje).
2. Drugi socket w EW11A w trybie MQTT, obok obecnego TCP-SERVER, jeśli firmware na to pozwala.
3. Skrypt testowy (osobny, bez zmian w głównej aplikacji):
   - subskrybuje `up` i przekazuje bajty do obecnego dekodera; sprawdza poprawność ramek i sposób, w jaki EW11A dzieli strumień na wiadomości MQTT (przepływność w trybie TCP już zmierzona, pkt 6a),
   - odpowiada na `0x30` z rosnącym opóźnieniem (0 / 100 / 300 / 1000 ms) i obserwuje reakcję sterownika,
   - testowy zapis parametru przez `down` i pomiar czasu do potwierdzenia.
4. Sprawdzenie:
   - czy TLS działa przy protokole MQTT,
   - czy EW11A łączy się z brokerem o dowolnym certyfikacie (potwierdzenie braku weryfikacji),
   - jakie wersje TLS i zestawy szyfrów negocjuje, czy wysyła SNI,
   - obsługę domeny (pkt 5a):
     - czy EW11A łączy się po nazwie domeny,
     - którego serwera DNS używa (z DHCP routera czy ustawionego ręcznie),
     - czy po zmianie rekordu A przełącza się na nowy adres przy ponownym połączeniu, czy dopiero po restarcie modułu, i czy respektuje TTL,
     - jak się zachowuje, gdy DNS chwilowo nie odpowiada: czy wraca sam, czy wymaga restartu,
   - tryby HTTP i WebSocket (pkt 4, „Inne tryby EW11A”):
     - czy da się w nich włączyć TLS (HTTPS / wss),
     - czy EW11A łączy się przez proxy Cloudflare, co wymaga SNI i zgodnych szyfrów,
     - w trybie HTTP: czy binarne dane (bajty `0x00`) przechodzą w treści POST bez zmian i czy działa keep-alive (HTTP/1.1).

## 9a. Wyniki testów EW11A (2026-11-22, firmware 1.50.0)

Zestaw testowy: [tools/mqtt_test/](../tools/mqtt_test/). Broker Mosquitto 2.0.18 na serwerze domowym 10.0.0.114.

| Test | Wynik |
|---|---|
| TLS w trybie MQTT | ❌ **ustawienie ignorowane.** EW11A wysyła jawny pakiet MQTT CONNECT (`10 2f 00 04 "MQTT"`), mimo `Security = TLS`. Login i hasło idą otwartym tekstem. |
| TLS w trybie TCP client | ❌ **nie działa.** EW11A nawiązuje połączenie TCP, przez 1 s nic nie wysyła i zamyka (FIN), ponawia co ~2 s. ClientHello nigdy nie wychodzi. Wynik ten sam przy jednym dodatkowym sockecie, więc to nie brak pamięci. Gdy serwer sam zaczyna handshake TLS, EW11A natychmiast zamyka połączenie. |
| MQTT bez TLS | ✅ logowanie działa (MQTT 3.1.1, keepalive 30 s) |
| Strumień przez MQTT | ✅ 253 B/s, 3,28 wiadomości/s (mediana 10 B, maks. 324 B, ≈ 1 ramka na wiadomość), 0 błędnych bajtów; identycznie jak przez TCP (pkt 6a) |
| Kilka socketów naraz | ✅ EW11A wysyła strumień z RS485 jednocześnie do `netp` (TCP-SERVER) i do socketu MQTT |
| Last Will | ⏸ niesprawdzone; po wyłączeniu socketu EW11A rozłącza się poprawnie (DISCONNECT), więc LWT nie jest wysyłany |
| Komendy przez MQTT (B0, UID) | ⏸ niesprawdzone |

**Wniosek:** z firmware 1.50.0 **nie da się bezpiecznie podłączyć EW11A bezpośrednio do publicznego serwisu**, bo nie ma żadnego działającego szyfrowania TLS. AES/DES3 nie są alternatywą (pkt 5): stały klucz i IV, brak integralności, a do tego trywialne odtworzenie przechwyconej komendy (replay).

Możliwe dalsze kroki:

1. Sprawdzić nowszy firmware EW11A i zapytać producenta (Hi-Flying) o działanie TLS w trybie MQTT i TCP client.
2. Sprawdzić, czy w panelu EW11A jest miejsce na wgranie certyfikatu lub CA. Może TLS wymaga go mimo opisu „no certificate method”.
3. W przeciwnym razie podstawą serwisu musi być **wariant A**: agent w domu, łączący się z EW11A w LAN i z chmurą przez TLS z weryfikacją certyfikatu.

**Uwaga przy okazji (błąd aplikacji, istniał wcześniej):** po restarcie EW11A aplikacja potrafi utrzymywać „zawieszone” połączenie TCP. Status pokazuje „połączono”, ale dane nie przychodzą (~6 min, do restartu usługi). Do poprawienia: ponowne łączenie po N sekundach bez żadnej ramki i/lub keepalive TCP.

## 10. Etapy wdrożenia

Zaktualizowane po zmianie kierunku na moduł ESP32 (pkt 12).

1. Prototyp modułu ESP32 (firmware etap 1) i broker testowy z certyfikatem Let's Encrypt.
2. Gateway: MQTT → istniejący dekoder; podgląd jednego urządzenia.
3. Sterowanie przez `down` z białą listą w module, walidacją na serwerze, potwierdzeniami i dziennikiem zmian.
4. Wielu użytkowników: konta, parowanie, tokeny urządzeń, kontrola dostępu w brokerze, PostgreSQL.
5. Instalacja dla użytkowników: strona z ESP Web Tools, instrukcja montażu, OTA.
6. Opcjonalnie wariant A (agent dla posiadaczy EW11A i serwera domowego).

## 11. Kwestie poza techniką

- Odpowiedzialność za zdalne sterowanie cudzymi kotłami: regulamin, jasne komunikaty o ryzyku.
- RODO: dane o obecności domowników da się wyczytać z harmonogramów i trybów pracy. Potrzebne polityka prywatności i umowa powierzenia, jeśli są podwykonawcy.
- Nazwa serwisu bez „ecoNET” i „econet24” (znaki towarowe Plum).
- Protokół jest rozpracowany nieoficjalnie. Zmiana firmware sterownika może wymagać szybkiej poprawki gatewaya.

## 12. Nowy kierunek: moduł ESP32 (DIY)

Ustalenia z 2026-11-22.

### Założenia

- Projekt **DIY**: użytkownik sam kupuje podzespoły i wgrywa firmware. Nie ma produkcji ani sprzedaży gotowych urządzeń.
- **Bez Secure Boot i szyfrowania flash.** Token urządzenia da się odczytać tylko przy fizycznym dostępie do modułu w kotłowni, co uznajemy za akceptowalne.
- **ESP32 weryfikuje certyfikat serwera:**
  - w firmware wbudowane główne certyfikaty CA (ISRG Root X1/X2 lub `esp_crt_bundle`),
  - broker ma certyfikat Let's Encrypt,
  - przed połączeniem TLS wymagana synchronizacja czasu (NTP), bo bez niej nie da się sprawdzić ważności certyfikatu.
- **Zasilanie z gniazda sterownika: 12 V** (wg instrukcji sterownika). Do sprawdzenia: maksymalny prąd gniazda. ESP32 z Wi-Fi to ok. 100–150 mA przy 12 V po przetwornicy.

### Podział zadań

**ESP32 („inteligentny most”):**
- składa ramki z RS485 i wysyła każdą poprawną ramkę jako wiadomość MQTT przez TLS,
- **sam odpowiada na `0x30` ramką `0xB0`** (usuwa problem opóźnienia z pkt 6),
- przepuszcza z chmury tylko poprawne ramki z białej listy typów,
- przełącznik „tylko odczyt”,
- buforuje dane przy braku internetu,
- opcjonalnie udostępnia surowy strumień na TCP 8899, jak EW11A. To daje zgodność z obecną aplikacją lokalną.

**Serwer:** pełne dekodowanie istniejącym kodem Pythona (`econet/`). Firmware ESP32 nie dekoduje danych, więc zmiany protokołu nie wymagają jego aktualizacji.

### Sprzęt (prototyp)

| Element | Przykład |
|---|---|
| ESP32 | ESP32-C3 SuperMini (wystarcza) lub ESP32-S3 DevKit (więcej RAM) |
| Przetwornica 12 V → 5 V | mini buck (MP1584, mini-360); napięcie ustawić przed podłączeniem ESP32 |
| RS485 | moduł TTL↔RS485 3,3 V z automatycznym kierunkiem (MAX3485/SP3485) |

Alternatywa: gotowa płytka ESP32 + RS485 z wejściem 12 V.

**Wybrana płytka do prototypu (2026-11-22): [Waveshare ESP32-S3-RS485-CAN](https://www.waveshare.com/wiki/ESP32-S3-RS485-CAN)**
- zasilanie 7–36 V DC z zacisków śrubowych, więc 12 V z gniazda sterownika podłącza się wprost,
- ESP32-S3,
- USB-C do zasilania i wgrywania firmware,
- wersja `-U` z zewnętrzną anteną SMA.

Inne rozważane płytki:

| Płytka | Zasilanie | Uwagi |
|---|---|---|
| LilyGO T-RSC3 (ESP32-C3) | 5–24 V | RS485 z izolacją galwaniczną |
| LilyGO T-CAN485 / T-Connect | 5–12 V / 7–12 V | 12 V na granicy zakresu, odrzucone |
| M5Stack Station-485 | 9–24 V | obudowa i szyna DIN, droższa, możliwe wycofanie |

Przed podłączeniem do sterownika:
1. **Pinout gniazda.** Moduł ecoNET łączy się z portem G2, wtyczką RJ11. Z instrukcji sterownika ustalić, które styki to A, B, GND i +12 V.
2. **Dopuszczalny prąd gniazda.** ESP32 z Wi-Fi potrzebuje ok. 100–150 mA przy 12 V.
3. **Rezystor terminujący 120 Ω na płytce.** Jeśli jest, odłączyć (zworka lub mostek), bo magistrala w sterowniku jest już zakończona.

### Firmware i instalacja

- **PlatformIO + Arduino-ESP32** (TLS, MQTT, portal konfiguracji, OTA). Alternatywa na później: ESPHome z własnym komponentem.
- **Wgrywanie z przeglądarki** przez ESP Web Tools (Chrome/Edge, kabel USB), konfiguracja Wi-Fi przez Improv.
- **Portal konfiguracji:** przy pierwszym starcie lub bez połączenia z Wi-Fi moduł tworzy własną sieć (AP) ze stroną do wpisania Wi-Fi i tokenu z serwisu.
- **Token urządzenia** (login i hasło MQTT) generuje serwis przy dodawaniu urządzenia; można go unieważnić i wygenerować nowy.
- **Aktualizacje:** OTA z adresu wydań (np. GitHub Releases) przez HTTPS albo ponowne wgranie z przeglądarki.

### Lokalna strona statusu

Prosta strona na ESP32 w sieci domowej, dostępna pod `http://ecomax-XXXX.local` (mDNS) albo pod adresem IP. Dane odświeżają się co kilka sekund z endpointu `/status.json`, który przyda się też do diagnostyki.

| Sekcja | Zawartość |
|---|---|
| Urządzenie | ID, wersja firmware, czas pracy, przyczyna ostatniego restartu, wolna pamięć (aktualna i minimalna), MAC |
| Wi-Fi | sieć (SSID), siła sygnału (dBm, ocena słownie), IP, liczba ponownych połączeń |
| Czas | stan synchronizacji NTP |
| Serwer (MQTT) | adres; stan: połączono / łączenie / błąd logowania / błąd TLS / błąd certyfikatu; od kiedy połączono; ostatni błąd z godziną; licznik ponownych połączeń; wiadomości wysłane i odebrane; zapełnienie bufora |
| TLS | wersja i szyfr, wynik weryfikacji certyfikatu (dla kogo wystawiony, ważny do) |
| Sterownik (RS485) | B/s, poprawne ramki, błędy CRC, wiek ostatniej ramki, liczba zapytań `0x30` i odpowiedzi `0xB0`, UID i model sterownika |
| Komendy z chmury | wykonane / odrzucone (spoza białej listy lub w trybie „tylko odczyt”) |
| Tryb | „tylko odczyt” włączony/wyłączony; klienci lokalnego TCP 8899 (jeśli włączony) |
| Dziennik | ostatnie ~50 zdarzeń w pamięci RAM: połączenia, rozłączenia, błędy, komendy |

Bezpieczeństwo strony:
- podgląd statusu nie pokazuje tokenu ani haseł,
- akcje wymagają hasła ustawionego przy konfiguracji: restart, zmiana Wi-Fi lub tokenu, przełącznik „tylko odczyt”, aktualizacja, pobranie pliku diagnostycznego.

Opcjonalnie dioda LED z prostymi sygnałami: brak Wi-Fi / brak serwera / brak danych ze sterownika / wszystko OK.

### Kolejne kroki

1. Ustalić pinout gniazda i dopuszczalny prąd, kupić podzespoły do prototypu.
2. **Firmware, etap 1:**
   - odbiór RS485 i składanie ramek,
   - lokalna odpowiedź `0xB0`,
   - MQTT przez TLS do brokera testowego (serwer domowy z certyfikatem Let's Encrypt albo VPS),
   - strona statusu.
3. **Firmware, etap 2:** komendy z białą listą i potwierdzeniami, tryb „tylko odczyt”, portal konfiguracji, OTA.
4. Strona serwerowa: gateway MQTT → istniejący dekoder (pkt 7).
