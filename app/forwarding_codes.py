"""Per-country call-forwarding star codes.

The carrier "unconditional forward" code varies by country and by whether the
line is a landline or a GSM mobile. Two common families:

  - North American (NANP) style:  *72 <number>   /  *73 to cancel
  - GSM / GSM-codes (Europe, Asia, LatAm):  **21*<number>#  /  ##21# to cancel
  - Some carriers use a UK/BT-style alt:  *21*<number>#  /  #21#

For each country we store the most widely supported code. If a carrier blocks
star codes the dashboard already shows a fallback ("call your carrier and ask
for unconditional forwarding to <Twilio number>").
"""
from __future__ import annotations


# style helpers
def _gsm_on(num: str) -> str: return f"**21*{num}#"
def _gsm_off() -> str: return "##21#"
def _nanp_on(num: str) -> str: return f"*72{num}"
def _nanp_off() -> str: return "*73"
def _uk_on(num: str) -> str: return f"*21*{num}#"
def _uk_off() -> str: return "#21#"


# Each entry: code, flag, on_template (function), off_code
# on_template receives the destination (Twilio) number and returns the dial string.
COUNTRIES: list[dict] = [
    # ---- North America (NANP, *72/*73) ----
    {"code": "US", "name": "United States",        "flag": "🇺🇸", "family": "nanp"},
    {"code": "CA", "name": "Canada",               "flag": "🇨🇦", "family": "nanp"},
    {"code": "MX", "name": "Mexico",               "flag": "🇲🇽", "family": "gsm"},
    {"code": "PR", "name": "Puerto Rico",          "flag": "🇵🇷", "family": "nanp"},

    # ---- Europe (GSM style on mobile; UK uses BT codes on landline) ----
    {"code": "GB", "name": "United Kingdom",       "flag": "🇬🇧", "family": "uk"},
    {"code": "IE", "name": "Ireland",              "flag": "🇮🇪", "family": "gsm"},
    {"code": "DE", "name": "Germany",              "flag": "🇩🇪", "family": "gsm"},
    {"code": "FR", "name": "France",               "flag": "🇫🇷", "family": "gsm"},
    {"code": "ES", "name": "Spain",                "flag": "🇪🇸", "family": "gsm"},
    {"code": "PT", "name": "Portugal",             "flag": "🇵🇹", "family": "gsm"},
    {"code": "IT", "name": "Italy",                "flag": "🇮🇹", "family": "gsm"},
    {"code": "NL", "name": "Netherlands",          "flag": "🇳🇱", "family": "gsm"},
    {"code": "BE", "name": "Belgium",              "flag": "🇧🇪", "family": "gsm"},
    {"code": "LU", "name": "Luxembourg",           "flag": "🇱🇺", "family": "gsm"},
    {"code": "CH", "name": "Switzerland",          "flag": "🇨🇭", "family": "gsm"},
    {"code": "AT", "name": "Austria",              "flag": "🇦🇹", "family": "gsm"},
    {"code": "SE", "name": "Sweden",               "flag": "🇸🇪", "family": "gsm"},
    {"code": "NO", "name": "Norway",               "flag": "🇳🇴", "family": "gsm"},
    {"code": "DK", "name": "Denmark",              "flag": "🇩🇰", "family": "gsm"},
    {"code": "FI", "name": "Finland",              "flag": "🇫🇮", "family": "gsm"},
    {"code": "IS", "name": "Iceland",              "flag": "🇮🇸", "family": "gsm"},
    {"code": "PL", "name": "Poland",               "flag": "🇵🇱", "family": "gsm"},
    {"code": "CZ", "name": "Czech Republic",       "flag": "🇨🇿", "family": "gsm"},
    {"code": "SK", "name": "Slovakia",             "flag": "🇸🇰", "family": "gsm"},
    {"code": "HU", "name": "Hungary",              "flag": "🇭🇺", "family": "gsm"},
    {"code": "RO", "name": "Romania",              "flag": "🇷🇴", "family": "gsm"},
    {"code": "BG", "name": "Bulgaria",             "flag": "🇧🇬", "family": "gsm"},
    {"code": "GR", "name": "Greece",               "flag": "🇬🇷", "family": "gsm"},
    {"code": "TR", "name": "Turkey",               "flag": "🇹🇷", "family": "gsm"},
    {"code": "HR", "name": "Croatia",              "flag": "🇭🇷", "family": "gsm"},
    {"code": "RS", "name": "Serbia",               "flag": "🇷🇸", "family": "gsm"},
    {"code": "SI", "name": "Slovenia",             "flag": "🇸🇮", "family": "gsm"},
    {"code": "EE", "name": "Estonia",              "flag": "🇪🇪", "family": "gsm"},
    {"code": "LV", "name": "Latvia",               "flag": "🇱🇻", "family": "gsm"},
    {"code": "LT", "name": "Lithuania",            "flag": "🇱🇹", "family": "gsm"},
    {"code": "UA", "name": "Ukraine",              "flag": "🇺🇦", "family": "gsm"},
    {"code": "RU", "name": "Russia",               "flag": "🇷🇺", "family": "gsm"},

    # ---- Middle East ----
    {"code": "AE", "name": "United Arab Emirates", "flag": "🇦🇪", "family": "gsm"},
    {"code": "SA", "name": "Saudi Arabia",         "flag": "🇸🇦", "family": "gsm"},
    {"code": "QA", "name": "Qatar",                "flag": "🇶🇦", "family": "gsm"},
    {"code": "KW", "name": "Kuwait",               "flag": "🇰🇼", "family": "gsm"},
    {"code": "BH", "name": "Bahrain",              "flag": "🇧🇭", "family": "gsm"},
    {"code": "OM", "name": "Oman",                 "flag": "🇴🇲", "family": "gsm"},
    {"code": "JO", "name": "Jordan",               "flag": "🇯🇴", "family": "gsm"},
    {"code": "LB", "name": "Lebanon",              "flag": "🇱🇧", "family": "gsm"},
    {"code": "IL", "name": "Israel",               "flag": "🇮🇱", "family": "gsm"},
    {"code": "EG", "name": "Egypt",                "flag": "🇪🇬", "family": "gsm"},

    # ---- Asia ----
    {"code": "IN", "name": "India",                "flag": "🇮🇳", "family": "gsm"},
    {"code": "PK", "name": "Pakistan",             "flag": "🇵🇰", "family": "gsm"},
    {"code": "BD", "name": "Bangladesh",           "flag": "🇧🇩", "family": "gsm"},
    {"code": "LK", "name": "Sri Lanka",            "flag": "🇱🇰", "family": "gsm"},
    {"code": "NP", "name": "Nepal",                "flag": "🇳🇵", "family": "gsm"},
    {"code": "CN", "name": "China",                "flag": "🇨🇳", "family": "gsm"},
    {"code": "JP", "name": "Japan",                "flag": "🇯🇵", "family": "gsm"},
    {"code": "KR", "name": "South Korea",          "flag": "🇰🇷", "family": "gsm"},
    {"code": "TW", "name": "Taiwan",               "flag": "🇹🇼", "family": "gsm"},
    {"code": "HK", "name": "Hong Kong",            "flag": "🇭🇰", "family": "gsm"},
    {"code": "SG", "name": "Singapore",            "flag": "🇸🇬", "family": "gsm"},
    {"code": "MY", "name": "Malaysia",             "flag": "🇲🇾", "family": "gsm"},
    {"code": "TH", "name": "Thailand",             "flag": "🇹🇭", "family": "gsm"},
    {"code": "VN", "name": "Vietnam",              "flag": "🇻🇳", "family": "gsm"},
    {"code": "PH", "name": "Philippines",          "flag": "🇵🇭", "family": "gsm"},
    {"code": "ID", "name": "Indonesia",            "flag": "🇮🇩", "family": "gsm"},

    # ---- Oceania ----
    {"code": "AU", "name": "Australia",            "flag": "🇦🇺", "family": "gsm"},
    {"code": "NZ", "name": "New Zealand",          "flag": "🇳🇿", "family": "gsm"},

    # ---- Latin America ----
    {"code": "BR", "name": "Brazil",               "flag": "🇧🇷", "family": "gsm"},
    {"code": "AR", "name": "Argentina",            "flag": "🇦🇷", "family": "gsm"},
    {"code": "CL", "name": "Chile",                "flag": "🇨🇱", "family": "gsm"},
    {"code": "CO", "name": "Colombia",             "flag": "🇨🇴", "family": "gsm"},
    {"code": "PE", "name": "Peru",                 "flag": "🇵🇪", "family": "gsm"},
    {"code": "VE", "name": "Venezuela",            "flag": "🇻🇪", "family": "gsm"},
    {"code": "UY", "name": "Uruguay",              "flag": "🇺🇾", "family": "gsm"},
    {"code": "EC", "name": "Ecuador",              "flag": "🇪🇨", "family": "gsm"},
    {"code": "BO", "name": "Bolivia",              "flag": "🇧🇴", "family": "gsm"},
    {"code": "PY", "name": "Paraguay",             "flag": "🇵🇾", "family": "gsm"},
    {"code": "CR", "name": "Costa Rica",           "flag": "🇨🇷", "family": "gsm"},
    {"code": "PA", "name": "Panama",               "flag": "🇵🇦", "family": "gsm"},
    {"code": "DO", "name": "Dominican Republic",   "flag": "🇩🇴", "family": "nanp"},
    {"code": "JM", "name": "Jamaica",              "flag": "🇯🇲", "family": "nanp"},

    # ---- Africa ----
    {"code": "ZA", "name": "South Africa",         "flag": "🇿🇦", "family": "gsm"},
    {"code": "NG", "name": "Nigeria",              "flag": "🇳🇬", "family": "gsm"},
    {"code": "KE", "name": "Kenya",                "flag": "🇰🇪", "family": "gsm"},
    {"code": "GH", "name": "Ghana",                "flag": "🇬🇭", "family": "gsm"},
    {"code": "ET", "name": "Ethiopia",             "flag": "🇪🇹", "family": "gsm"},
    {"code": "MA", "name": "Morocco",              "flag": "🇲🇦", "family": "gsm"},
    {"code": "DZ", "name": "Algeria",              "flag": "🇩🇿", "family": "gsm"},
    {"code": "TN", "name": "Tunisia",              "flag": "🇹🇳", "family": "gsm"},
    {"code": "UG", "name": "Uganda",               "flag": "🇺🇬", "family": "gsm"},
    {"code": "TZ", "name": "Tanzania",             "flag": "🇹🇿", "family": "gsm"},
]


def forwarding_for(country_code: str, twilio_number: str) -> dict:
    """Return dial strings for a given country + destination number."""
    country = next((c for c in COUNTRIES if c["code"] == country_code), None)
    if not country:
        return {"on": _gsm_on(twilio_number), "off": _gsm_off(), "family": "gsm"}
    fam = country["family"]
    if fam == "nanp":
        return {"on": _nanp_on(twilio_number), "off": _nanp_off(), "family": fam}
    if fam == "uk":
        return {"on": _uk_on(twilio_number), "off": _uk_off(), "family": fam}
    return {"on": _gsm_on(twilio_number), "off": _gsm_off(), "family": fam}


def all_for(twilio_number: str) -> list[dict]:
    """Return every country with its dial strings precomputed (for the template)."""
    out = []
    for c in COUNTRIES:
        fc = forwarding_for(c["code"], twilio_number)
        out.append({
            "code": c["code"],
            "name": c["name"],
            "flag": c["flag"],
            "family": c["family"],
            "on": fc["on"],
            "off": fc["off"],
        })
    return sorted(out, key=lambda x: x["name"])
