from utils.keywords import TENDERSINFO_QUERIES, match_tender


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


def test_climatech_consultation_and_cpms_scope_matches():
    result = match_tender(
        "Consultation and site assessment for EV charging network rollout",
        "Includes CPMS SaaS, regulatory approvals, and CPO operations for public charging.",
    )

    assert result.matched is True
    assert result.company == "Climatech"
    assert any(keyword in result.matched_keywords for keyword in ["consultation", "site assessment", "cpms"])


def test_evs_firmware_and_hv_battery_scope_matches():
    result = match_tender(
        "EV firmware update and HV battery module repair for electric bus fleet",
        "Includes BMS diagnostics, inverter repair, and extended service packages.",
    )

    assert result.matched is True
    assert result.company == "EVS"
    assert any(keyword in result.matched_keywords for keyword in ["firmware", "battery module", "bms"])


def test_generic_software_development_does_not_match():
    result = match_tender(
        "Website development and mobile application development for customer portal",
        "Includes backend integration, analytics dashboards, and hosting.",
    )

    assert result.matched is False
    assert result.company == ""


def test_non_ev_energy_storage_does_not_match():
    result = match_tender(
        "Battery energy storage system with substation and transformer upgrade",
        "Includes cable laying and civil works for the utility site.",
    )

    assert result.matched is False
    assert result.company == ""


def test_tendersinfo_queries_are_high_signal():
    assert "Saudi Arabia" not in TENDERSINFO_QUERIES
    assert "ev charging station" in TENDERSINFO_QUERIES
