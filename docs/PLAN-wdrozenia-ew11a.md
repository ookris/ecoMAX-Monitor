# Plan wdrożenia — nowe funkcje na podstawie analizy surowych danych z EW11A

Data: 2026-11-22
Sterownik: ecoMAX920P1-EN, moduł A 5.11.23.E1, panel 5.11.15 (adres 0x50), 1 z 6 mieszaczy podłączony.

Źródło wiedzy o protokole: [PyPlumIO](https://github.com/denpamusic/PyPlumIO) (biblioteka używana przez
[homeassistant-plum-ecomax](https://github.com/denpamusic/homeassistant-plum-ecomax)) + przechwycony ruch z EW11A.

## Zasady prowadzenia prac

- Każda faza kończy się: testami automatycznymi (`tests/`, pytest), przeglądem kodu, poprawkami z przeglądu i commitem.
- Nowe zapytania do sterownika są wyłącznie **odczytami**, chyba że faza wprost opisuje zapis (wł./wył. kotła).
- Testy nie wymagają sprzętu: fikstury to zanonimizowane ramki z przechwyconego ruchu.
  Nie uruchamiamy drugiej instancji aplikacji podłączonej do prawdziwego EW11A — dwie instancje
  odpowiadałyby jednocześnie na ramkę 0x30 jako ecoNET (0x56) i kolidowałyby na magistrali RS485.
- Zmiany schematu bazy wyłącznie addytywne (`ALTER TABLE ... ADD COLUMN`, nowe tabele) — istniejące bazy działają bez ręcznej migracji.

## Faza 1 — Poprawki

1. **Tryby pracy** — 1 = „Stabilizacja” (było „Zatrzymany”), 6 = „Czuwanie” (było „Czyszczenie”).
   Nazwa trybu w API wyliczana z kodu, więc stare wiersze w bazie też pokażą poprawną nazwę.
2. **Katalog parametrów** (`econet/boiler_control.py`):
   - idx 18 → „Logika rozmyta (Fuzzy Logic)”, przełącznik 0/1 (było „Tryb regulacji 0–2 Auto/Ręczny/Pogodowy”),
   - idx 107 → „Krzywa grzewcza”, wartość ×0,1 (było „Program tygodniowy CO”); dodanie obsługi skalowania wartości (`scale`) w API i UI,
   - idx 122 → tryb CWU 0 = wyłączony, 1 = priorytet, 2 = bez priorytetu (zakres 0–2),
   - nowe pozycje: idx 106 „Sterowanie pogodowe”, idx 108 „Przesunięcie krzywej grzewczej”, idx 88 „Kaloryczność paliwa” (×0,1 kWh/kg, profil serwisowy),
   - domyślne zakresy (używane, gdy sterownik nie odpowie) zgodne z tym, co zwraca sterownik.
3. **Poziom paliwa** — nowe ustawienie (checkbox) „Poziom paliwa nie jest ustawiony w sterowniku”.
   Gdy zaznaczone: brak alertu `fuel_low`, `fuel_level = null` w API/HA/Prometheus/bazie, dashboard pokazuje „nie ustawiono”.
   Dekoder: 0xFF → brak wartości, korekta offsetu 101 (jak w PyPlumIO).
4. **Moc kotła %** — gdy sterownik nie podaje obciążenia (`boiler_load = 0` przy mocy > 0), % liczony z kW i nowego ustawienia
   „Moc nominalna kotła (kW)”. Bez ustawienia kafel pokazuje tylko kW, a % jest pusty.
5. **Moc w kW** — oznaczona jako szacowana; obok kafla informacja o kaloryczności paliwa odczytanej ze sterownika (idx 88).
6. **`/api/status`** — `uptime` jako liczba sekund od startu aplikacji (było `time.time()`).
7. **`/api/restart`** — naprawa wcięcia: przy błędzie `systemctl` endpoint zwracał `None` (błąd 500 Flask).
8. **Dekoder 0x35** — prefiks ramki parsowany wprost jako tabela wersji ramek (zamiast heurystyki),
   heurystyka zostaje jako fallback; obsługa 17 indeksów temperatur (13–16: sprzęgło, wymiennik, powietrze wlot/wylot).

## Faza 2 — Pełny dekoder ramki 0x35

1. Wyjścia: wentylator, podajnik, zapalarka, wyjście alarmowe, pompy (CO/CWU/cyrkulacja/solar/kominek), styk GZC, wentylator spalin, podajnik 2/zewnętrzny.
2. Statusy obiegów CO/CWU, styk termostatu pokojowego, lista aktywnych alarmów (kody), sonda lambda (jeśli jest).
3. Tabela wersji ramek (`frame_versions`) — wystawiona w danych; zmiana wersji 0x38 (zmiany parametrów) unieważnia cache parametrów.
4. Mieszacze: bajty stanu wyjść i bajt 7 (kandydat na pozycję zaworu — weryfikacja na dłuższym nagraniu, ostatecznie w fazie 5).
5. Dashboard: wskaźniki wentylator/podajnik/zapalarka obok pomp, kafel mieszacza (temperatura, zadana, pompa); strona Parametry z nowymi polami.
6. Baza: nowe kolumny (wyjścia, termostat, mieszacz 1), CSV, `/api/ha`, `/metrics`.

## Faza 3 — Historia alarmów sterownika (0x3D → 0xBD)

1. `request_alerts()` w połączeniu (odpowiada panel 0x50, ramka rozgłoszeniowa 0xBD), dekodowanie kodów i czasu od–do.
2. Tabela `controller_alarms` (unikalność: kod + początek), synchronizacja: przy starcie, po zmianie wersji ramki 0x3D, co 15 min awaryjnie.
3. Polskie nazwy 56 kodów alarmów. Nowy alarm ze sterownika → alert aplikacji (`controller_alarm`) z nazwą; pierwszy import nie generuje alertów.
4. Strona Alerty: sekcja „Alarmy sterownika” (kod, nazwa, od, do, czas trwania) + przycisk odświeżenia. API: `GET /api/controller-alarms`, `POST /api/controller-alarms/refresh`.

## Faza 4 — Parametry mieszacza, spalania i informacje o urządzeniu

1. Odczyt parametrów mieszaczy (0x32 → 0xB2), polskie nazwy 14 parametrów, wyświetlanie bieżących wartości;
   walidacja zapisu parametrów mieszacza względem zakresów sterownika (dziś 0–255).
2. Parametry rozpalania/stabilizacji/nadzoru/wygaszania/podawania (idx 0–89) — panel tylko do odczytu z polskimi nazwami.
3. Parametry serwisowe (0x61 → 0xE1) — surowa tabela dostępna tylko z tokenem serwisowym (brak nazw w źródłach).
4. UID w formacie ecoNET (base32 + CRC jak w PyPlumIO) oraz wersje/daty firmware (0x40 → 0xC0).
5. Włączanie/wyłączanie kotła (0x3B → 0xBB): endpoint z uprawnieniami sterowania, potwierdzenie w UI, wpis do historii zmian.

## Faza 5 — Dane regulatora (ramka 0x08, regdata)

1. Pobranie schematu (0x55 → 0xD5) po połączeniu i po zmianie wersji 0x55; schemat zapamiętany w bazie.
2. Ogólny dekoder wszystkich typów (bit array, liczby, float, IPv4, string).
3. Mapa znanych pól (temperatury, zadane, wentylator %, podajnik, pozycja zaworu mieszacza, surowe ADC czujników, informacje sieciowe).
4. Strona Parametry: sekcja „Dane regulatora” (id, typ, wartość, nazwa) z możliwością nadania własnej nazwy polu.
5. Pozycja zaworu mieszacza trafia do danych mieszacza (po weryfikacji źródła).

## Faza 6 — Analityka

1. Śledzenie zdarzeń z każdej ramki (bez throttlingu `read_interval`): zmiany trybu, czas pracy podajnika, wentylatora, zapalarki, rozpalenia.
2. Tabela zdarzeń trybu i dzienne liczniki w bazie.
3. API `/api/analytics/daily?days=N`: czas w trybach, liczba cykli pracy, rozpalenia i ich skuteczność (z alarmami „nieudane rozpalanie”),
   czas pracy podajnika/wentylatora, energia (kWh, szacowana) i koszt kWh.
4. Strona Analiza: sekcja „Cykle pracy” (tabela + wykres czasu w trybach) i wykres pozycji zaworu mieszacza.

## Zakończenie

Przegląd kodu całości zmian, poprawki, raport `docs/RAPORT-wdrozenia-ew11a.md`, commit.
Wdrożenie na serwer domowy nie wchodzi w zakres — do decyzji użytkownika po raporcie.
