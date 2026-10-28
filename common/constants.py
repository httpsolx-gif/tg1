# Словарь соответствия ISO-кодов стран их полным названиям (можно расширять)
COUNTRY_NAMES = {
    "US": "United States",
    "RU": "Russia",
    "CN": "China",
    "DE": "Germany",
    "FR": "France",
    "IN": "India",
    "BR": "Brazil",
    "JP": "Japan",
    "GB": "United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "IT": "Italy",
    "ES": "Spain",
    "MX": "Mexico",
    "KR": "South Korea",
    "NL": "Netherlands",
    "SE": "Sweden",
    "CH": "Switzerland",
    "ZA": "South Africa",
    "SA": "Saudi Arabia",
    "AE": "United Arab Emirates",
    "SG": "Singapore",
    "NZ": "New Zealand",
    "AR": "Argentina",
    "NO": "Norway",
    "PL": "Poland",
    "BE": "Belgium",
    "AT": "Austria",
    "BG": "Bulgaria",
    "CZ": "Czechia",
    "SK": "Slovakia",
    "HU": "Hungary",
    "PT": "Portugal",
    "IE": "Ireland",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "DK": "Denmark",
    "FI": "Finland",
    "GR": "Greece",
    "EE": "Estonia",
    "SI": "Slovenia",
    "HR": "Croatia",
    "TW": "Taiwan",
    "TH": "Thailand",
    # Добавляйте новые страны здесь
}

# common/constants.py

# Список сервисов
ALL_SERVICES = [
    # Тестовые сервисы (реальные сервисы удалены). Имена содержат суффикс страны,
    # чтобы COUNTRY_SERVICES раскладывал их по странам как раньше.
    "myemail.pl", "testshop.pl",
    "myemail.de", "testshop.de",
    "myemail.cz", "testshop.cz",
    "myemail.fr",
    "myemail.co.uk", "testshop.co.uk",
    "myemail.usa",
]

# Список стран (ISO-коды)
COUNTRIES = [
    "US",  # United States
    "RU",  # Russia
    "CN",  # China
    "DE",  # Germany
    "FR",  # France
    "IN",  # India
    "BR",  # Brazil
    "JP",  # Japan
    "GB",  # United Kingdom
    "CA",  # Canada
    "AU",  # Australia
    "IT",  # Italy
    "ES",  # Spain
    "MX",  # Mexico
    "KR",  # South Korea
    "NL",  # Netherlands
    "SE",  # Sweden
    "CH",  # Switzerland
    "ZA",  # South Africa
    "SA",  # Saudi Arabia
    "AE",  # United Arab Emirates
    "SG",  # Singapore
    "NZ",  # New Zealand
    "AR",  # Argentina
    "NO",  # Norway
    "PL",  # Poland
    "BE",  # Belgium
    "AT",  # Austria
    "BG",  # Bulgaria
    "CZ",  # Czechia
    "SK",  # Slovakia
    "HU",  # Hungary
    "PT",  # Portugal
    "IE",  # Ireland
    "LT",  # Lithuania
    "LU",  # Luxembourg
    "DK",  # Denmark
    "FI",  # Finland
    "GR",  # Greece
    "EE",  # Estonia
    "SI",  # Slovenia
    "HR",  # Croatia
    "TW",  # Taiwan
    "TH",  # Thailand
    # Добавляйте новые страны здесь
]

# Словарь соответствия ISO-кодов стран их эмодзи-флагам
COUNTRY_FLAGS = {
    "US": "🇺🇸",  # United States
    "RU": "🇷🇺",  # Russia
    "CN": "🇨🇳",  # China
    "DE": "🇩🇪",  # Germany
    "FR": "🇫🇷",  # France
    "IN": "🇮🇳",  # India
    "BR": "🇧🇷",  # Brazil
    "JP": "🇯🇵",  # Japan
    "GB": "🇬🇧",  # United Kingdom
    "CA": "🇨🇦",  # Canada
    "AU": "🇦🇺",  # Australia
    "IT": "🇮🇹",  # Italy
    "ES": "🇪🇸",  # Spain
    "MX": "🇲🇽",  # Mexico
    "KR": "🇰🇷",  # South Korea
    "NL": "🇳🇱",  # Netherlands
    "SE": "🇸🇪",  # Sweden
    "CH": "🇨🇭",  # Switzerland
    "ZA": "🇿🇦",  # South Africa
    "SA": "🇸🇦",  # Saudi Arabia
    "AE": "🇦🇪",  # United Arab Emirates
    "SG": "🇸🇬",  # Singapore
    "NZ": "🇳🇿",  # New Zealand
    "AR": "🇦🇷",  # Argentina
    "NO": "🇳🇴",  # Norway
    "PL": "🇵🇱",  # Poland
    "BE": "🇧🇪",  # Belgium
    "AT": "🇦🇹",  # Austria
    "BG": "🇧🇬",  # Bulgaria
    "CZ": "🇨🇿",  # Czechia
    "SK": "🇸🇰",  # Slovakia
    "HU": "🇭🇺",  # Hungary
    "PT": "🇵🇹",  # Portugal
    "IE": "🇮🇪",  # Ireland
    "LT": "🇱🇹",  # Lithuania
    "LU": "🇱🇺",  # Luxembourg
    "DK": "🇩🇰",  # Denmark
    "FI": "🇫🇮",  # Finland
    "GR": "🇬🇷",  # Greece
    "EE": "🇪🇪",  # Estonia
    "SI": "🇸🇮",  # Slovenia
    "HR": "🇭🇷",  # Croatia
    "TW": "🇹🇼",  # Taiwan
    "TH": "🇹🇭",  # Thailand
}

# Словарь соответствия ISO-кодов стран их сервисам (по суффиксам доменов/аббревиатуре)
# Можно дополнять правилами для новых стран/сервисов
COUNTRY_SERVICES = {
    "PL": [s for s in ALL_SERVICES if ".pl" in s or "pl." in s or s.endswith("pl")],
    "DE": [s for s in ALL_SERVICES if ".de" in s or "de." in s or s.endswith("de")],
    "FR": [s for s in ALL_SERVICES if ".fr" in s or "fr." in s or s.endswith("fr")],
    "CZ": [s for s in ALL_SERVICES if ".cz" in s or "cz." in s or s.endswith("cz")],
    "SK": [s for s in ALL_SERVICES if ".sk" in s or "sk." in s or s.endswith("sk")],
    "HU": [s for s in ALL_SERVICES if ".hu" in s or "hu." in s or s.endswith("hu")],
    "BG": [s for s in ALL_SERVICES if ".bg" in s or "bg." in s or s.endswith("bg")],
    "AT": [s for s in ALL_SERVICES if ".at" in s or "at." in s or s.endswith("at")],
    "BE": [s for s in ALL_SERVICES if ".be" in s or "be." in s or s.endswith("be")],
    "CH": [s for s in ALL_SERVICES if ".ch" in s or "ch." in s or s.endswith("ch")],
    "NL": [s for s in ALL_SERVICES if ".nl" in s or "nl." in s or s.endswith("nl")],
    "SE": [s for s in ALL_SERVICES if ".se" in s or "se." in s or s.endswith("se")],
    "FI": [s for s in ALL_SERVICES if ".fi" in s or "fi." in s or s.endswith("fi")],
    "DK": [s for s in ALL_SERVICES if ".dk" in s or "dk." in s or s.endswith("dk")],
    "PT": [s for s in ALL_SERVICES if ".pt" in s or "pt." in s or s.endswith("pt")],
    "ES": [s for s in ALL_SERVICES if ".es" in s or "es." in s or s.endswith("es")],
    "IT": [s for s in ALL_SERVICES if ".it" in s or "it." in s or s.endswith("it")],
    "NO": [s for s in ALL_SERVICES if ".no" in s or "no." in s or s.endswith("no")],
    "IE": [s for s in ALL_SERVICES if ".ie" in s or "ie." in s or s.endswith("ie")],
    "LT": [s for s in ALL_SERVICES if ".lt" in s or "lt." in s or s.endswith("lt")],
    "LU": [s for s in ALL_SERVICES if ".lu" in s or "lu." in s or s.endswith("lu")],
    "GR": [s for s in ALL_SERVICES if ".gr" in s or "gr." in s or s.endswith("gr")],
    "EE": [s for s in ALL_SERVICES if ".ee" in s or "ee." in s or s.endswith("ee")],
    "SI": [s for s in ALL_SERVICES if ".sl" in s or "sl." in s or ".si" in s or "si." in s or s.endswith("si")],  # sl/si
    "HR": [s for s in ALL_SERVICES if ".hr" in s or "hr." in s or s.endswith("hr")],
    "TW": [s for s in ALL_SERVICES if ".tw" in s or "tw." in s or s.endswith("tw")],
    "TH": [s for s in ALL_SERVICES if ".th" in s or "th." in s or s.endswith("th")],
    "CA": [s for s in ALL_SERVICES if ".ca" in s or "ca." in s or s.endswith("ca")],
    "AU": [s for s in ALL_SERVICES if ".au" in s or "au." in s or s.endswith("au")],
    "GB": [s for s in ALL_SERVICES if ".co.uk" in s or ".uk" in s or "co.uk" in s or "uk." in s or s.endswith("uk")],
    "US": [s for s in ALL_SERVICES if ".usa" in s or "usa" in s or s.endswith("usa")],
    # Пример для стран без сервисов:
    "RU": [],
    "CN": [],
    "IN": [],
    "BR": [],
    "JP": [],
    "MX": [],
    "KR": [],
    "ZA": [],
    "SA": [],
    "AE": [],
    "SG": [],
    "NZ": [],
    "AR": [],
    # Добавляйте новые страны и правила фильтрации здесь
}
# --- Почтовые провайдеры (mailbox-типы) и коды для имён файлов шаблонов ---
# Имя файла шаблона: <service>-<код>.html  (напр. olx.pl-g.html → olx.pl / gmail)
MAILBOXES = [
    ("m", "main"),
    ("g", "gmail"),
    ("h", "hotmail"),
    ("o", "outlook"),
    ("y", "yahoo"),
]
MAILBOX_NAME_BY_CODE = {code: name for code, name in MAILBOXES}
MAILBOX_CODE_BY_NAME = {name: code for code, name in MAILBOXES}
