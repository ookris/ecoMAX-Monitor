"""Ramki przechwycone z EW11A (ecoMAX920P1, 2026-11-22).

Ramki zawieraja wylacznie pomiary i wersje modulow - bez UID i adresow IP.
"""

# Ramka 0x35 (FRAME_DATA_ANS): tryb 3 (Praca), wentylator + pompa CO,
# CO 74.14, CWU 51.1, powrot 49.86, spaliny 142.5, 1 mieszacz z 6.
FRAME_35_WORKING = bytes.fromhex(
    "68a6000045fc93350755198854397f563e913601003815003901003d02630305000000ff04"
    "000008007845944201ffffffff0260644c4203ffffffff046870474205f97e0e4307ffffff"
    "ff08ffffffff460037000000000000344200c8e8ab40d501833f01050b174501ffffffff05"
    "0b0fffff06085f4f423200092fffffffff28000800ffffffff28000800ffffffff28000800"
    "ffffffff28000800ffffffff28000800db16"
)
