"""De webapp: `weekstaat web` start een server op 127.0.0.1 waarin je een dossier kiest (een map op deze computer of
bestanden uit de browser), de aansluiting draait en de uitkomst bekijkt en downloadt. Alles blijft op deze computer;
de server luistert alleen op het eigen adres en weigert verzoeken van andere sites."""

import csv
import io
import json
import logging
import re
import shutil
import signal
import sys
import tempfile
import threading
import uuid
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__

STATISCH = Path(__file__).parent / "webapp"
VOORBEELD = Path(__file__).parent.parent / "voorbeeld"
MAX_BESTAND = 200 * 1024 * 1024
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
ID = re.compile(r"[0-9a-f]{32}")
slot = threading.Lock()  # aansluiten leidt stdout om; maar één run tegelijk


class Fout(Exception):
    def __init__(self, status: int, tekst: str):
        super().__init__(tekst)
        self.status, self.tekst = status, tekst


def _veilig(basis: Path, pad: str) -> Path:
    """Een relatief pad uit de browser, binnen `basis`: geen `..`, geen absoluut pad."""
    delen = [d for d in pad.replace("\\", "/").split("/") if d not in ("", ".")]
    if not delen or ".." in delen:
        raise Fout(400, f"ongeldig pad: {pad}")
    doel = basis.joinpath(*delen)
    if not doel.resolve().is_relative_to(basis.resolve()):
        raise Fout(400, f"ongeldig pad: {pad}")
    return doel


def _bevindingen(pad: Path) -> list[dict]:
    with open(pad, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def aansluiten(map_: Path, uit: Path) -> dict:
    """Draait `weekstaat aansluiten` op `map_` en geeft de uitkomst als gegevens voor de pagina."""
    from . import cli

    logboek = io.StringIO()
    meld = logging.StreamHandler(logboek)
    meld.setFormatter(cli._Melding("let op: %(message)s"))
    terminal = io.StringIO()
    with slot:
        cli.log.addHandler(meld)
        cli.log.setLevel(logging.INFO)
        try:
            with redirect_stdout(terminal):
                samenvattingen = cli._aansluiten(_Args(map=str(map_), uit=str(uit), medewerker=None))
        except (ValueError, OSError) as fout:
            tekst = cli._oserror(fout) if isinstance(fout, OSError) else str(fout)
            raise Fout(400, "\n".join(cli._toon(r) for r in tekst.split("\n"))) from None
        finally:
            cli.log.removeHandler(meld)
    medewerkers = [
        {
            "naam": naam,
            "bestand": f"Overzicht {cli._in_naam(naam)}.xlsx",
            "samenvatting": [
                {
                    "status": status,
                    "dagen": int(r.dagen),
                    "factuurkant": float(r.factuurkant),
                    "orderkant": float(r.orderkant),
                    "verschil": float(r.verschil),
                }
                for status, r in s.iterrows()
            ],
        }
        for naam, s in samenvattingen.items()
    ]
    return {
        "log": (terminal.getvalue() + logboek.getvalue()).replace(f"{uit}/", ""),
        "medewerkers": medewerkers,
        "bevindingen": _bevindingen(uit / "bevindingen.csv"),
        "bestanden": sorted(p.name for p in uit.iterdir() if p.is_file()),
    }


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Handler(BaseHTTPRequestHandler):
    werk: Path  # tijdelijke map voor uploads en uitkomsten
    poort: int

    def log_message(self, *a):  # geen verzoeklog in de terminal
        pass

    # --- bewaking: alleen dit adres, geen andere site ---
    def _controleer(self) -> None:
        if self.headers.get("Host") not in (f"127.0.0.1:{self.poort}", f"localhost:{self.poort}"):
            raise Fout(403, "ander adres dan dit programma")
        herkomst = self.headers.get("Origin")
        if herkomst is not None and herkomst != f"http://{self.headers['Host']}":
            raise Fout(403, "verzoek van een andere site")

    def _stuur(self, status: int, inhoud: bytes, soort: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", soort)
        self.send_header("Content-Length", str(len(inhoud)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(inhoud)

    def _json(self, gegevens, status: int = 200) -> None:
        self._stuur(status, json.dumps(gegevens, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _lees_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0:
            raise Fout(400, "ongeldige lengte")
        if n > MAX_BESTAND:
            raise Fout(413, "bestand te groot")
        return self.rfile.read(n)

    def _doe(self, methode: str) -> None:
        try:
            self._controleer()
            pad = urlparse(self.path)
            getattr(self, methode)(pad.path, parse_qs(pad.query, keep_blank_values=True))
        except Fout as f:
            self._json({"fout": f.tekst}, f.status)
        except Exception:  # noqa: BLE001 - de server mag niet omvallen op één verzoek
            import traceback

            traceback.print_exc()
            self._json({"fout": "er ging iets mis in het programma; zie de terminal"}, 500)

    def do_GET(self):
        self._doe("_get")

    def do_POST(self):
        self._doe("_post")

    def do_PUT(self):
        self._doe("_put")

    # --- routes ---
    def _get(self, pad: str, q: dict) -> None:
        if pad == "/api/info":
            return self._json({"versie": __version__, "voorbeeld": str(VOORBEELD) if VOORBEELD.is_dir() else None})
        m = re.fullmatch(r"/api/run/([0-9a-f]{32})/bestand/(.+)", pad)
        if m:
            uit = self.werk / "runs" / m[1]
            naam = unquote(m[2])
            bestand = uit / naam
            if not uit.is_dir() or naam not in {p.name for p in uit.iterdir()}:
                raise Fout(404, "bestand niet gevonden")
            return self._stuur(
                200,
                bestand.read_bytes(),
                "application/octet-stream",
                {"Content-Disposition": "attachment; filename*=UTF-8''" + _quote(naam)},
            )
        if pad.startswith("/api/"):
            raise Fout(404, "onbekend")
        naam = "index.html" if pad == "/" else pad.lstrip("/")
        bestand = STATISCH / naam
        if naam not in {p.name for p in STATISCH.iterdir()}:
            raise Fout(404, "niet gevonden")
        self._stuur(200, bestand.read_bytes(), TYPES.get(bestand.suffix, "application/octet-stream"))

    def _post(self, pad: str, q: dict) -> None:
        if pad == "/api/sessie":
            sid = uuid.uuid4().hex
            (self.werk / "sessies" / sid).mkdir(parents=True)
            return self._json({"id": sid})
        if pad == "/api/aansluiten":
            try:
                body = json.loads(self._lees_body() or b"{}")
            except ValueError:
                raise Fout(400, "geen geldige aanvraag") from None
            if "sessie" in body:
                if not ID.fullmatch(str(body["sessie"])):
                    raise Fout(400, "ongeldige sessie")
                map_ = self.werk / "sessies" / body["sessie"]
                if not map_.is_dir():
                    raise Fout(404, "sessie niet gevonden")
            else:
                map_ = Path(str(body.get("map") or "")).expanduser()
                if not map_.is_absolute() or not map_.is_dir():
                    raise Fout(400, f"{map_} is geen map")
            rid = uuid.uuid4().hex
            uit = self.werk / "runs" / rid
            try:
                return self._json({"run": rid, **aansluiten(map_, uit)})
            except Fout as f:  # de tijdelijke map hoort niet in de melding
                if "sessie" not in body:
                    raise
                raise Fout(f.status, f.tekst.replace(f"{map_}/", "").replace(str(map_), "het dossier")) from None
            finally:
                if "sessie" in body:  # de geüploade bestanden zijn na de run niet meer nodig
                    shutil.rmtree(map_, ignore_errors=True)
        raise Fout(404, "onbekend")

    def _put(self, pad: str, q: dict) -> None:
        m = re.fullmatch(r"/api/sessie/([0-9a-f]{32})/bestand", pad)
        if not m or "pad" not in q:
            raise Fout(404, "onbekend")
        map_ = self.werk / "sessies" / m[1]
        if not map_.is_dir():
            raise Fout(404, "sessie niet gevonden")
        doel = _veilig(map_, q["pad"][0])
        doel.parent.mkdir(parents=True, exist_ok=True)
        doel.write_bytes(self._lees_body())
        self._json({"ok": True})


def _quote(naam: str) -> str:
    from urllib.parse import quote

    return quote(naam)


def serveer(poort: int) -> None:
    with tempfile.TemporaryDirectory(prefix="weekstaat-web-") as werk:
        Handler.werk, Handler.poort = Path(werk), poort
        (Handler.werk / "sessies").mkdir()
        (Handler.werk / "runs").mkdir()
        try:
            server = ThreadingHTTPServer(("127.0.0.1", poort), Handler)
        except OSError as fout:
            raise ValueError(f"poort {poort} is niet beschikbaar: {fout.strerror}") from None
        print(f"Weekstaat draait op http://127.0.0.1:{poort}  (stoppen met Ctrl-C)", file=sys.stderr)
        for signaal in (signal.SIGTERM, signal.SIGHUP):  # ook dan ruimen we de uploads op
            signal.signal(signaal, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            shutil.rmtree(werk, ignore_errors=True)
