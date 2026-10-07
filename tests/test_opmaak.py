"""Tests voor de hulpfuncties in `opmaak`."""

import pytest

from weekstaat.opmaak import nl_bedrag, percentage


@pytest.mark.parametrize(("bedrag", "tekst"), [(1234.5, "1.234,50"), (0, "0,00"), (-1234567.891, "-1.234.567,89")])
def test_nl_bedrag_schrijft_zoals_in_nederland(bedrag, tekst):
    assert nl_bedrag(bedrag) == tekst


@pytest.mark.parametrize(("fractie", "tekst"), [(0.21, "21%"), (0.09, "9%"), (0.065, "6,5%"), (0.07, "7%"), (0, "0%")])
def test_percentage_laat_overbodige_nullen_weg(fractie, tekst):
    assert percentage(fractie) == tekst
