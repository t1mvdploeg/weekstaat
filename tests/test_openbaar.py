"""Controleert dat er niets in de repo staat dat niet naar buiten mag: paden van een computer, echte rekeningnummers,
e-mailadressen, sleutels. De voorbeeldset gebruikt alleen nummers die zichtbaar verzonnen zijn."""

import io
import re
import zipfile
from email import message_from_bytes, policy
from pathlib import Path

import pytest
from pypdf import PdfReader

REPO = Path(__file__).parent.parent
OVERSLAAN = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "uitvoer"}
BEELD = {".png", ".ico", ".jpg"}

VERBODEN = {
    "pad van een computer": re.compile(r"/U" r"sers/|/home/[a-z]|[A-Z]:\\\\U" r"sers"),
    "sleutel of token": re.compile(r"BEGIN [A-Z ]*PRIVATE KEY|gh[pous]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}"),
    "e-mailadres buiten .example": re.compile(
        r"[\w.+-]+@(?![\w.-]*\.example\b)(?!users\.noreply\.github\.com)[\w-]+(?:\.[\w-]+)+"
    ),
    "rekeningnummer dat niet met NL00 begint": re.compile(r"\bNL(?!00)\d{2} ?[A-Z]{4}(?: ?\d){10}\b"),
    "btw-nummer dat niet uit nullen bestaat": re.compile(r"\bNL(?!0{9})\d{9}B\d{2}\b"),
}


def pdf_tekst(inhoud: bytes) -> str:
    pdf = PdfReader(io.BytesIO(inhoud))
    eigenschappen = " ".join(str(v) for v in (pdf.metadata or {}).values())
    return "\n".join(p.extract_text() for p in pdf.pages) + "\n" + eigenschappen


def xlsx_tekst(pad: Path) -> str:
    """Alle onderdelen van het werkboek als tekst: ook de eigenschappen (`docProps`), opmerkingen in cellen,
    hyperlinks, verborgen bladen, gedefinieerde namen en de gedeelde tekst, die in de zip staan en niet in een cel."""
    with zipfile.ZipFile(pad) as z:
        return "\n".join(f"{n}\n{z.read(n).decode('utf-8', errors='replace')}" for n in z.namelist())


def tekst(pad: Path) -> str:
    """Alles wat een lezer in het bestand kan vinden: ook de eigenschappen van een pdf of werkboek en de bijlagen
    van een mail, die in het bestand zelf gecodeerd staan."""
    if pad.suffix == ".pdf":
        return pdf_tekst(pad.read_bytes())
    if pad.suffix == ".xlsx":
        return xlsx_tekst(pad)
    if pad.suffix == ".eml":
        delen = []
        for deel in message_from_bytes(pad.read_bytes(), policy=policy.default).walk():
            delen += [f"{k}: {v}" for k, v in deel.items()]
            if deel.get_content_type() == "application/pdf":
                delen.append(pdf_tekst(deel.get_payload(decode=True)))
            elif deel.get_content_maintype() == "text":
                delen.append(deel.get_content())
        return "\n".join(delen)
    return pad.read_text(encoding="utf-8", errors="replace")


BESTANDEN = sorted(
    p
    for p in REPO.rglob("*")
    if p.is_file() and not OVERSLAAN & set(p.relative_to(REPO).parts) and p.suffix not in BEELD
)


def test_er_is_iets_om_te_controleren():
    assert len(BESTANDEN) > 30


@pytest.mark.parametrize("pad", BESTANDEN, ids=lambda p: str(p.relative_to(REPO)))
def test_niets_dat_niet_naar_buiten_mag(pad):
    inhoud = pad.name + "\n" + tekst(pad)
    gevonden = {naam: m.group() for naam, patroon in VERBODEN.items() if (m := patroon.search(inhoud))}
    assert not gevonden


def test_de_lektest_ziet_wat_buiten_de_cellen_van_een_werkboek_staat(tmp_path):
    from openpyxl import Workbook
    from openpyxl.comments import Comment

    wb = Workbook()
    wb.properties.creator = "iemand" + chr(64) + "bureau.nl"  # geen letterlijk adres in dit bestand
    wb.active["A1"].comment = Comment("pad: " + "/".join(["", "Us" + "ers", "iemand"]), "iemand")
    wb.active["A2"].hyperlink = "https://voorbeeld.nl/x?sleutel=ghp_" + "a" * 24
    wb.save(tmp_path / "w.xlsx")
    inhoud = tekst(tmp_path / "w.xlsx")
    assert {naam for naam, patroon in VERBODEN.items() if patroon.search(inhoud)} == {
        "pad van een computer",
        "sleutel of token",
        "e-mailadres buiten .example",
    }
