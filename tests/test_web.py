"""De webapp op de voorbeeldset: de server draait in deze test op een vrije poort van 127.0.0.1."""

import csv
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import quote

import pytest
from conftest import MEDEWERKERS, VOORBEELD

from weekstaat import web


@pytest.fixture(scope="module")
def basis(tmp_path_factory):
    werk = tmp_path_factory.mktemp("web")
    (werk / "sessies").mkdir()
    (werk / "runs").mkdir()
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    web.Handler.werk, web.Handler.poort = werk, server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def vraag(url, data=None, methode=None, headers=None):
    req = urllib.request.Request(url, data=data, method=methode, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as f:
        return f.code, f.read()


def post_json(url, body):
    return vraag(url, json.dumps(body).encode(), "POST")


@pytest.fixture(scope="module")
def run(basis):
    status, body = post_json(basis + "/api/aansluiten", {"map": str(VOORBEELD)})
    assert status == 200
    return json.loads(body)


def _verwacht():
    with open(VOORBEELD / "verwacht" / "bevindingen.csv", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def test_pagina_en_info(basis):
    status, html = vraag(basis + "/")
    assert status == 200 and b"<title>" in html
    assert json.loads(vraag(basis + "/api/info")[1])["voorbeeld"] == str(VOORBEELD)


def test_aansluiten_op_een_map_geeft_de_uitkomst(run):
    assert [m["naam"] for m in run["medewerkers"]] == MEDEWERKERS
    assert run["medewerkers"][0]["samenvatting"][0]["status"] == "Klopt"
    assert len(run["bevindingen"]) == len(_verwacht())
    assert {"Overzicht totaal.xlsx", "bevindingen.csv"} <= set(run["bestanden"])
    assert "5 orders, 154 urenregels" in run["log"] and "weekstaat-web" not in run["log"]


def test_download_van_een_uitvoerbestand(basis, run):
    naam = quote("Overzicht totaal.xlsx")
    status, inhoud = vraag(f"{basis}/api/run/{run['run']}/bestand/{naam}")
    assert status == 200 and inhoud[:2] == b"PK"
    assert vraag(f"{basis}/api/run/{run['run']}/bestand/..%2Fgeheim")[0] == 404


def test_upload_van_een_dossier_geeft_dezelfde_uitkomst(basis, run):
    sid = json.loads(post_json(basis + "/api/sessie", {})[1])["id"]
    for pad in VOORBEELD.rglob("*"):
        rel = pad.relative_to(VOORBEELD)
        if pad.is_file() and rel.parts[0] not in ("verwacht", "ruw"):
            status, _ = vraag(f"{basis}/api/sessie/{sid}/bestand?pad={quote(str(rel))}", pad.read_bytes(), "PUT")
            assert status == 200
    status, body = post_json(basis + "/api/aansluiten", {"sessie": sid})
    assert status == 200 and json.loads(body)["bevindingen"] == run["bevindingen"]


def test_een_pad_buiten_de_sessie_wordt_geweigerd(basis):
    sid = json.loads(post_json(basis + "/api/sessie", {})[1])["id"]
    for pad in ("../x.csv", "a/../../x.csv", ""):
        assert vraag(f"{basis}/api/sessie/{sid}/bestand?pad={quote(pad)}", b"x", "PUT")[0] == 400


def test_fouten_zijn_een_melding_met_status_400(basis, tmp_path):
    status, body = post_json(basis + "/api/aansluiten", {"map": str(tmp_path / "bestaat-niet")})
    assert status == 400 and "geen map" in json.loads(body)["fout"]
    status, body = post_json(basis + "/api/aansluiten", {"map": str(tmp_path)})
    assert status == 400 and json.loads(body)["fout"]


def test_andere_sites_en_andere_adressen_worden_geweigerd(basis):
    assert vraag(basis + "/api/info", headers={"Origin": "http://voorbeeld.example"})[0] == 403
    assert vraag(basis + "/api/info", headers={"Host": "voorbeeld.example"})[0] == 403


def test_een_fout_bij_een_upload_noemt_de_tijdelijke_map_niet(basis):
    sid = json.loads(post_json(basis + "/api/sessie", {})[1])["id"]
    status, body = post_json(basis + "/api/aansluiten", {"sessie": sid})
    fout = json.loads(body)["fout"]
    assert status == 400 and "instellingen.toml" in fout and sid not in fout


def test_een_leeg_of_relatief_pad_is_geen_dossier(basis):
    for map_ in ("", "voorbeeld"):
        status, body = post_json(basis + "/api/aansluiten", {"map": map_})
        assert status == 400 and "geen map" in json.loads(body)["fout"]


def test_een_upload_wordt_na_de_run_verwijderd(basis):
    sid = json.loads(post_json(basis + "/api/sessie", {})[1])["id"]
    post_json(basis + "/api/aansluiten", {"sessie": sid})
    assert not (web.Handler.werk / "sessies" / sid).exists()
