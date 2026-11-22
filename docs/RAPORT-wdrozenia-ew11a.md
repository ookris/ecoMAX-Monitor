# Raport z wdrożenia: nowe funkcje na podstawie danych z EW11A

Data: 2026-11-22 · Gałąź: `dev` · Plan: [PLAN-wdrozenia-ew11a.md](PLAN-wdrozenia-ew11a.md)

## Podsumowanie

Zrealizowano 6 faz planu oraz przegląd całości. Każda faza miała testy, przegląd kodu,
poprawki po przeglądzie i osobny commit. Zmiany objęły 46 plików (+5,7 tys. linii, w tym testy).
Dodano 112 testów automatycznych (pytest) — wszystkie przechodzą. Nowe odczyty
sprawdzono też na żywym sterowniku (ecoMAX920P1-EN przez EW11A 10.0.0.226), wyłącznie zapytaniami
tylko do odczytu.

| Faza | Commit | Zakres |
|---|---|---|
| 1 | `f420f74` | Poprawki: tryby, katalog parametrów, poziom paliwa, moc %, uptime, restart, dekoder |
| 2 | `c4f3f33` | Pełny dekoder 0x35, zawór mieszacza, wykrywanie zmian z panelu |
| 3 | `8099418` | Historia alarmów sterownika |
| 4 | `ef74d03` | Mieszacze, parametry sterownika, UID, wł./wył. kotła |
| 5 | `6aae19b` | Dane regulatora (ramka 0x08) |
| 6 | `a90f199` | Analityka cykli pracy |
| przegląd całości | ten commit | Poprawki z końcowego przeglądu, testy stron, raport |

## Co się zmieniło dla użytkownika

**Panel główny**
- Wskaźniki wentylatora, podajnika i zapalarki obok pomp.
- Kafle mieszaczy: temperatura, zadana, pompa, otwarcie zaworu i kierunek (otwiera/zamyka).
- Aktywne alarmy sterownika pod trybem pracy (link do historii).
- Kafel mocy: % ze sterownika albo wyliczony z mocy nominalnej; bez niej — sama moc w kW,
  opisana jako szacowana, z kalorycznością paliwa odczytaną ze sterownika.
- Poziom paliwa „nie ustawiono”, gdy zaznaczono to w ustawieniach.

**Ustawienia** — sekcja „Kocioł i paliwo”: checkbox „Poziom paliwa nie jest ustawiony w sterowniku”
(wyłącza alert o niskim poziomie), moc nominalna kotła (opcjonalna).

**Sterowanie**
- „Kocioł i urządzenie”: model, UID w formacie ecoNET, wersje modułów, przyciski Włącz/Wyłącz kocioł
  (z potwierdzeniem, wpis w historii).
- „Mieszacze” (profil instalatora): 14 parametrów mieszacza z polskimi nazwami i zapisem
  w zakresach podanych przez sterownik.
- „Parametry pracy sterownika (odczyt)”: wszystkie 53 dostępne parametry (rozpalanie, nadzór,
  wygaszanie, podawanie, obiegi…) z polskimi nazwami i filtrem grup.
- „Parametry serwisowe (surowe)” dla profilu serwisowego.
- Historia zmian obejmuje też zmiany zrobione na panelu kotła („zmiana na panelu”).
- Poprawione nastawy: Fuzzy Logic (było błędnie „Tryb regulacji 0–2”), krzywa grzewcza ×0,1
  (było „Program tygodniowy”), tryb CWU 0–2; nowe: sterowanie pogodowe, przesunięcie krzywej,
  kaloryczność paliwa (serwis).

**Alerty** — sekcja „Alarmy sterownika”: pełna historia z pamięci sterownika (kod, nazwa, od–do,
czas trwania), także z okresów, gdy aplikacja nie działała; nowy alarm generuje alert aplikacji.

**Parametry** — wszystkie wyjścia, statusy obiegów, styk termostatu, sonda lambda, wersje ramek;
nowa sekcja „Dane regulatora (0x08)” z 258 polami i możliwością nadawania im nazw.

**Analiza** — wykres czasu w trybach na dzień, tabela dziennych cykli (praca, cykle, rozpalenia,
nieudane rozpalenia i skuteczność, podajnik, wentylator, energia, paliwo, koszt, zł/kWh),
wykres mieszacza (temperatura, zadana, zawór).

**Integracje** — nowe pola w `/api/ha`, `/metrics` i eksporcie CSV.

## Ważniejsze ustalenia techniczne

- **Pozycja zaworu mieszacza** to bajt 7 wpisu mieszacza w ramce 0x35 (zweryfikowane z polem 140
  ramki 0x08 na 10‑minutowym nagraniu); bity 1/2 bajtu wyjść mieszacza to otwieranie/zamykanie.
- **Historia alarmów** jest w panelu (adres 0x50), maks. 10 wpisów na ramkę, od najnowszego.
  Sterownik ma duplikaty (ten sam kod i początek) oraz wpis z uszkodzoną datą (rok 2133) —
  obsłużone (tożsamość = kod + początek + koniec, uszkodzone daty pomijane).
- **Moc w kW** sterownik wylicza ze strumienia paliwa i kaloryczności (parametr 88 = 5,2 kWh/kg),
  nie mierzy jej — dlatego oznaczona jako szacowana; kaloryczność jest odczytywana, nie wpisywana ręcznie.
- **Wersje ramek** z 0x35 sterują synchronizacją w tle (`ControllerSync`): 0x38 → parametry,
  0x3D → alarmy, 0x55 → schemat danych regulatora; z opóźnieniem 10 s i zadaniami okresowymi.
- Odpowiedzi sterownika są przyjmowane tylko od 0x45 do ecoNET/rozgłoszeniowe i dopasowywane
  do zapytania — na magistrali są odpowiedzi dla innych urządzeń (panel).

## Odstępstwa od planu

- **Wersje firmware (0x40 → 0xC0)** — pominięte: odpowiedź trafiła do innego adresata (0x55),
  format nieopisany. Wersje modułów są w ramce 0x35.
- **Pozycja zaworu** zrealizowana już w fazie 2 (zamiast 5), bo nagranie potwierdziło źródło.
- **Kaloryczność paliwa** (uwaga z planu) — da się ją odczytać ze sterownika, więc nie usuwano
  mocy kW, tylko opisano ją jako szacowaną i pokazano kaloryczność.
- Dodatkowo (poza planem): logowanie zmian parametrów z panelu, odrzucanie zapisu do parametrów
  niedostępnych w sterowniku (wcześniej błąd 500), naprawa endpointu `/api/restart`.

## Weryfikacja

- `pytest` — 112 testów: dekodery na prawdziwej ramce 0x35, wektor UID z testów PyPlumIO,
  synchronizacja (debounce, okresowe, stronicowanie alarmów, ponawianie), API, migracja bazy,
  renderowanie wszystkich stron.
- Odtworzenie 10‑minutowego nagrania przez lokalny serwer TCP udający EW11A (2224 ramki, 0 błędów).
- Na żywym sterowniku (tylko odczyty, bez odpowiedzi B0, żeby nie kolidować z instancją
  produkcyjną): parametry 0x31, mieszacze 0x32, serwis 0x61, UID 0x39, alarmy 0x3D (81 wpisów
  w 1,3 s), schemat 0x55 (258 pól) i dekodowanie ramek 0x08 (0 błędów).
- **Nie testowano na żywo** zapisów: wł./wył. kotła (0x3B) i zapisu parametrów mieszacza (0x34) —
  sprawdzone tylko testami z atrapą połączenia.
- **Nie sprawdzono wizualnie** interfejsu w przeglądarce — tylko składnia JS (`node --check`)
  i renderowanie szablonów. Lokalnie nie da się uruchomić całej aplikacji: eventlet 0.37 nie działa
  z Pythonem 3.14, a innego działającego interpretera nie ma.

## Wdrożenie

Kod nie został wdrożony na serwer (10.0.0.114). Po wdrożeniu:
- baza zostanie zmigrowana automatycznie (nowe kolumny i tabele, bez utraty danych);
- przy pierwszym połączeniu aplikacja zaimportuje historię alarmów (bez alertów) i pobierze schemat 0x08;
- analityka dzienna zbiera dane od chwili uruchomienia nowej wersji;
- warto zaznaczyć w Ustawieniach „Poziom paliwa nie jest ustawiony” i ewentualnie wpisać moc nominalną kotła.

## Do rozważenia później

- Zweryfikować pola regdata oznaczone „?” (wejścia analogowe czujników 7–16, informacje sieciowe 178–190, stan 1792).
- Opisać parametry serwisowe (0x61), gdy znajdzie się źródło nazw.
- Częste „nieudane próby rozpalania” (8 z ostatnich 11 alarmów, ostatnia 22.11 o 13:08) — teraz można
  je śledzić w Analizie i porównać z parametrami rozpalania na stronie Sterowanie.
