from utils.keywords import match_tender


def test_generic_electrical_spare_parts_does_not_match_evs():
    result = match_tender("توريد و مواد كهربائية قطع غيار و قواطع")

    assert result.matched is False
    assert result.company == ""


def test_explicit_electric_vehicle_spare_parts_still_match_evs():
    result = match_tender("توريد قطع غيار للمركبات الكهربائية")

    assert result.matched is True
    assert result.company == "EVS"
    assert "قطع غيار" in result.matched_keywords


def test_electric_fleet_still_matches_evs():
    result = match_tender("صيانة أسطول كهربائي")

    assert result.matched is True
    assert result.company == "EVS"
    assert "أسطول كهربائي" in result.matched_keywords


def test_climatech_charging_tender_still_matches():
    result = match_tender(
        "Supply and installation of EV charging stations",
        "Includes charger installation and commissioning for public parking.",
    )

    assert result.matched is True
    assert result.company == "Climatech"
    assert "charging station" in result.matched_keywords or "charger installation" in result.matched_keywords
