"""Kleine hulpfuncties om getallen in gewone tekst te zetten; de lezers en de rekenmodules gebruiken ze allebei."""


def nl_bedrag(x: float) -> str:
    """Bedrag in Nederlandse schrijfwijze: 1.234,56."""
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def percentage(x: float) -> str:
    """Een fractie als percentage in Nederlandse schrijfwijze, zonder overbodige nullen: 0.21 wordt '21%' en
    0.065 wordt '6,5%'."""
    return f"{round(x * 100, 6):g}".replace(".", ",") + "%"


def zin(tekst: str) -> str:
    """Een zin met een hoofdletter, ook als hij begint met de naam uit de instellingen ('de opdrachtgever')."""
    return tekst[:1].upper() + tekst[1:]
