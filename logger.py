"""Zarządza logowaniem do pliku z automatyczną rotacją i czyszczeniem."""

import logging
import logging.handlers
import os
import glob
import time
from typing import Optional

__all__ = [
    'setup_file_logging',
    'get_app_logger',
    'cleanup_old_logs',
    'read_log_lines',
    'get_log_file_path',
]


def setup_file_logging(
    log_dir: str = 'data/logs',
    log_name: str = 'ecomax-monitor',
    max_bytes: int = 5 * 1024 * 1024,  # 5 MB
    backup_count: int = 10,
    enabled: bool = True,
) -> Optional[logging.Handler]:
    """Skonfiguruj logowanie do pliku z rotacją.

    Args:
        log_dir: katalog na logi
        log_name: nazwa pliku logu (bez rozszerzenia)
        max_bytes: maks. rozmiar pliku przed rotacją
        backup_count: ile kopii przechowywać
        enabled: czy włączyć logowanie do pliku

    Returns:
        Handler lub None jeśli disabled
    """
    if not enabled:
        return None

    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f'{log_name}.log')

    try:
        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8',
        )
        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )
        handler.setFormatter(formatter)
        return handler
    except Exception as e:
        print(f"Błąd przy tworzeniu logu do pliku: {e}")
        return None


def get_app_logger(name: str) -> logging.Logger:
    """Pobierz logger dla aplikacji (już skonfigurowany w main)."""
    return logging.getLogger(name)


def cleanup_old_logs(
    log_dir: str = 'data/logs',
    retention_days: int = 30,
    log_name: str = 'ecomax-monitor',
) -> int:
    """Usuń pliki logów starsze niż retention_days.

    Args:
        log_dir: katalog z logami
        retention_days: ile dni przechowywać
        log_name: nazwa pliku logu (bez rozszerzenia)

    Returns:
        Liczba usuniętych plików
    """
    if not os.path.isdir(log_dir):
        return 0

    deleted_count = 0
    now = time.time()
    retention_seconds = retention_days * 86400

    pattern = os.path.join(log_dir, f'{log_name}.log*')
    for log_file in glob.glob(pattern):
        try:
            mtime = os.path.getmtime(log_file)
            if now - mtime > retention_seconds:
                os.remove(log_file)
                deleted_count += 1
        except Exception as e:
            logging.getLogger(__name__).warning(
                "Nie można usunąć %s: %s", log_file, e
            )

    return deleted_count


def _tail_lines(log_path: str, count: int) -> list[str]:
    """Zwróć ostatnie `count` linii z pliku bez wczytywania całości."""
    if count <= 0:
        return []

    block_size = 8192
    data = bytearray()

    with open(log_path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        file_size = f.tell()
        pos = file_size

        while pos > 0 and data.count(b'\n') <= count:
            read_size = block_size if pos >= block_size else pos
            pos -= read_size
            f.seek(pos)
            data[:0] = f.read(read_size)

    text = data.decode('utf-8', errors='ignore')
    lines = text.splitlines(keepends=True)
    return lines[-count:]


def _count_lines(log_path: str) -> int:
    """Policz linie pliku w trybie strumieniowym."""
    with open(log_path, 'rb') as f:
        return sum(1 for _ in f)


def read_log_lines(
    log_dir: str = 'data/logs',
    lines: int = 100,
    offset: int = 0,
    log_name: str = 'ecomax-monitor',
) -> tuple[list[str], int]:
    """Odczytaj ostatnie linie z pliku logu.

    Args:
        log_dir: katalog z logami
        lines: ile linii pobrać
        offset: od której linii zacząć (od końca)
        log_name: nazwa pliku logu (bez rozszerzenia)

    Returns:
        (lista_linii, całkowita_liczba_linii)
    """
    log_path = os.path.join(log_dir, f'{log_name}.log')

    if not os.path.isfile(log_path):
        return [], 0

    try:
        total = _count_lines(log_path)
        wanted = min(lines + offset, total)
        if wanted <= 0:
            return [], total

        tail = _tail_lines(log_path, wanted)
        end_idx = max(0, len(tail) - offset)
        start_idx = max(0, end_idx - lines)

        # Zwróć od najnowszych
        result = tail[start_idx:end_idx]
        result.reverse()

        return result, total
    except Exception as e:
        logging.getLogger(__name__).error("Błąd odczytu logu: %s", e)
        return [], 0


def get_log_file_path(log_dir: str = 'data/logs') -> Optional[str]:
    """Zwróć ścieżkę do głównego pliku logu."""
    log_path = os.path.join(log_dir, 'ecomax-monitor.log')
    return log_path if os.path.isfile(log_path) else None
