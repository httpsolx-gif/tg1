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
    "2dehands.be","acs.gr","alkupiac.hu","allegro.pl","anibis.fr.ch","aramex.au","aukro.cz","balikovna.cz","bazar.bg","bazos.cz","bazos.sk","bgpost.bg","blablacar.cz","blablacar.fr","blocket.se","bolha.sl","booking.eu","box.gr","carousell.au","carousell.ca","carousell.my","carousell.sg","ceskaposta.cz","correos.es","ctt.pt","dal.nl","dao.dk","dba.dk","depop.au","depop.co.uk","depop.de","dhl.co.uk","dhl.cz","dhl.de","dhl.hr","dpd.at","dpd.co.uk","dpd.cz","dpd.fr","dpd.hr","dpd.hu","dpd.lt","dpd.pl","dpd.sk","dpd.sl","ebay.de","econt.bg","facebook.at","facebook.cz","facebook.lt","facebook.sk","facebook.tw","fedex.ca","fedex.co.uk","fedex.ie","fedex.tw","finn.no","foxpost.hu","gls.hu","gumtree.au","gumtree.co.uk","helthjem.no","hermes.de","inpost.pl","inpostpl.api","jofogas.hu","kaidee.tw","kijiji.ca","laposte.fr","leboncoin.fr","locanto.de","lot.bg","marketplaats.nl","matkahuolto.fi","mbway.pt","milanuncios.es","mpl.hu","olx.ar","olx.bg","olx.pl","olx.pt","olxpl.api","olxpl2.api","omniva.ee","omniva.lt","packeta.hu","packeta.sk","paypal.de","paypal.eu","pls.hr","poczta.pl","poshmark.au","poshmark.ca","poshmark.usa","post.at","post.au","post.ca","post.ch","post.ch.fr","post.de","post.nl","post.sk","posten.no","posti.fi","postnord.dk","postnord.no","postnord.se","ppl.cz","quoka.de","royal.co.uk","sendle.au","shpock.at","speedire.it","subito.it","tise.fi","tori.fi","tori02.fi","tori2.fi","trendsales.dk","tutti.ch","vatera.hu","vendora.gr","vinted.at","vinted.be","vinted.ca","vinted.co.uk","vinted.cz","vinted.de","vinted.dk","vinted.es","vinted.fi","vinted.fr","vinted.gr","vinted.hu","vinted.it","vinted.lt","vinted.lu","vinted.nl","vinted.pl","vinted.pt","vinted.se","vinted.sk","vinted.usa","wallapop.co.uk","wallapop.es","wallapop.fr","wallapop.it","wallapop.pt","willhaben.at","wu.au","zasilkovna.cz"
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