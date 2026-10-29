"""Real-world tender wording that must (or must not) reach Climatech Charger."""
import pytest

from utils.keywords import match_tender

CLIMATECH = [
    "توريد وتركيب شاحن سياره كهربائي",
    "توريد شواحن سيارات كهربائية لمقر الوزارة",
    "توريد وتركيب محطات شحن للسيارات الكهربائية",
    "إنشاء محطات الشحن الكهربائي بمواقف المستشفى",
    "تأمين شواحن للمركبات الكهربائية",
    "توريد وتركيب وتشغيل نقاط شحن المركبات الكهربائية",
    "مشروع الشحن الكهربائي للمركبات",
    "توريد شاحن للسيارات الكهربائيه",
    "توريد سيارات كهربائية",
    "تأمين مركبات كهربائيه مع الشواحن",
    "Supply and installation of EV chargers",
    "Supply of electric vehicle charging stations at King Fahd hospital",
    "EV-charging infrastructure for staff parking",
    "Installation of DC fast chargers",
    "Procurement of electric vehicles for the municipality",
    "Supply of chargers for electric vehicles",
    "EV charging station including 1500 kVA transformer and substation works",
    "ﺗﻮرﻳﺪ ﺷﻮاﺣﻦ ﺳﻴﺎرات ﻛﻬﺮﺑﺎﺋﻴﺔ",  # typed in Arabic presentation forms, as some Etimad titles are
]

NOT_CLIMATECH = [
    "توريد مواد كهربائية",
    "توريد شواحن أجهزة لاسلكي",
    "توريد شاحن جوال",
    "شحن البضائع ونقلها إلى مستودعات الوزارة",
    "خدمات الشحن والتخليص الجمركي",
    "Supply of mobile phone chargers",
    "Battery energy storage system with substation and transformer upgrade",
    "Electric Motor Repair",
    "تركيب مولدات كهربائية",
    "Freight charges and shipping services",
]


@pytest.mark.parametrize("title", CLIMATECH)
def test_ev_charging_wording_is_found(title):
    result = match_tender(title)
    assert result.matched, (title, result)
    assert result.company in ("Climatech", "Both"), (title, result)


@pytest.mark.parametrize("title", NOT_CLIMATECH)
def test_other_wording_is_left_out(title):
    result = match_tender(title)
    assert result.company not in ("Climatech", "Both"), (title, result)
