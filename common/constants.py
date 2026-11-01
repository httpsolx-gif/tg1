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

# --- Часовой пояс страны (IANA-зона) для плейсхолдера *DATE* ---
# Берём ДАТУ в поясе страны получателя; переход на летнее/зимнее время (DST)
# учитывается автоматически через zoneinfo (база tz), поэтому дата не сбивается.
# Для стран с несколькими зонами (US/RU/BR/CA/AU) — основная бизнес-зона.
COUNTRY_TZ = {
    "PL": "Europe/Warsaw", "DE": "Europe/Berlin", "FR": "Europe/Paris",
    "CZ": "Europe/Prague", "SK": "Europe/Bratislava", "HU": "Europe/Budapest",
    "BG": "Europe/Sofia", "AT": "Europe/Vienna", "BE": "Europe/Brussels",
    "CH": "Europe/Zurich", "NL": "Europe/Amsterdam", "SE": "Europe/Stockholm",
    "FI": "Europe/Helsinki", "DK": "Europe/Copenhagen", "PT": "Europe/Lisbon",
    "ES": "Europe/Madrid", "IT": "Europe/Rome", "NO": "Europe/Oslo",
    "IE": "Europe/Dublin", "LT": "Europe/Vilnius", "LU": "Europe/Luxembourg",
    "GR": "Europe/Athens", "EE": "Europe/Tallinn", "SI": "Europe/Ljubljana",
    "HR": "Europe/Zagreb", "GB": "Europe/London", "RU": "Europe/Moscow",
    "CN": "Asia/Shanghai", "IN": "Asia/Kolkata", "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul", "TW": "Asia/Taipei", "TH": "Asia/Bangkok",
    "SG": "Asia/Singapore", "SA": "Asia/Riyadh", "AE": "Asia/Dubai",
    "ZA": "Africa/Johannesburg", "NZ": "Pacific/Auckland",
    "US": "America/New_York", "CA": "America/Toronto", "MX": "America/Mexico_City",
    "BR": "America/Sao_Paulo", "AR": "America/Argentina/Buenos_Aires",
    "AU": "Australia/Sydney",
}

# --- Suppression: категории, на которые НЕ слать всегда (п.50) ---
# Роль-адреса (на ЛЮБОМ домене): почтить abuse@/postmaster@/spamtrap@ = репортят
# отправителя в блеклисты. Сверяется local-part письма.
SUPPRESS_ROLES = {
    "abuse", "postmaster", "hostmaster", "webmaster", "mailer-daemon", "noc",
    "security", "complaints", "complaint", "spam", "spamtrap", "spam-trap",
    "antispam", "anti-spam", "nospam", "no-spam", "uce", "fraud", "phishing",
    "phish", "dmarc", "dkim", "blackhole", "devnull", "bounce", "bounces",
}
# Домены (и их ПОДДОМЕНЫ) площадок/мониторинга/ловушек. Пополняется в боте
# (🛡 API → Suppression, можно добавить `@domain`). Пример: kleinanzeigen.de ловит
# и mail.kleinanzeigen.de.
SUPPRESS_DOMAINS = {
    # Площадки-компании (внутренние/мониторинговые адреса)
    "kleinanzeigen.de", "kleinanzeigen.com", "ebay-kleinanzeigen.de",
    # Антиспам-организации и известные ловушки
    "spamhaus.org", "spamcop.net", "uceprotect.net", "abuse.net",
    "barracudacentral.org", "spam.la", "spamtrap.com", "projecthoneypot.org",
    "mailabuse.org", "cloudmark.com",
}

# --- Автоисправление опечаток домена получателя (п.51) ---
# Набор КРУПНЫХ потребительских провайдеров — цель фуззи-исправления (расстояние ≤1
# с учётом перестановки соседних букв). Опечатка вроде wrb.de→web.de, gamil.com→gmail.com.
CANONICAL_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com",
    "yahoo.com", "yahoo.de", "yahoo.co.uk", "yahoo.fr",
    "hotmail.com", "hotmail.de", "hotmail.co.uk", "hotmail.fr",
    "outlook.com", "outlook.de", "live.com", "live.de", "msn.com",
    "web.de", "gmx.de", "gmx.net", "gmx.com", "t-online.de",
    "aol.com", "icloud.com", "me.com",
    "mail.ru", "yandex.ru",
    "wp.pl", "o2.pl", "interia.pl", "onet.pl", "op.pl",
    "seznam.cz", "free.fr", "orange.fr", "laposte.net",
    "proton.me", "protonmail.com",
}
# Явные опечатки (в т.ч. с расстоянием >1) → корректный домен. Имеют приоритет над фуззи.
TYPO_DOMAIN_MAP = {
    "gamil.com": "gmail.com", "gmial.com": "gmail.com", "gmil.com": "gmail.com",
    "gmai.com": "gmail.com", "gmaill.com": "gmail.com", "gnail.com": "gmail.com",
    "gmail.con": "gmail.com", "gmail.co": "gmail.com", "gmail.cm": "gmail.com",
    "gmail.om": "gmail.com", "gmail.ocm": "gmail.com", "ggmail.com": "gmail.com",
    "hotmial.com": "hotmail.com", "hotmal.com": "hotmail.com", "hotmai.com": "hotmail.com",
    "hotmil.com": "hotmail.com", "hotmail.con": "hotmail.com",
    "outlok.com": "outlook.com", "outllok.com": "outlook.com", "outlook.con": "outlook.com",
    "yaho.com": "yahoo.com", "yahooo.com": "yahoo.com", "yhoo.com": "yahoo.com",
    "wrb.de": "web.de", "wen.de": "web.de", "web.d": "web.de",
    "gmx.ne": "gmx.net", "t-onlie.de": "t-online.de",
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


# малоизвестные/локальные бесплатные почты (публичные домены провайдеров) → main
LOCAL_FREEMAIL = set("""
web.de gmx.de gmx.net gmx.at gmx.ch gmx.com gmx.fr gmx.es gmx.it gmx.us mail.com email.com
t-online.de magenta.de freenet.de arcor.de online.de email.de posteo.de posteo.net mailbox.org
vodafone.de vodafonemail.de kabelmail.de unitybox.de ewe.net osnanet.de htp-tel.de mnet-online.de
1und1.de onlinehome.de tutanota.com tutanota.de tuta.io proton.me protonmail.com protonmail.ch pm.me
aon.at chello.at a1.net drei.at bluewin.ch sunrise.ch hispeed.ch gmail.ch
wp.pl onet.pl onet.eu op.pl o2.pl interia.pl interia.eu poczta.fm tlen.pl vp.pl gazeta.pl buziaczek.pl
seznam.cz email.cz centrum.cz atlas.cz post.cz volny.cz azet.sk zoznam.sk centrum.sk
freemail.hu citromail.hu abv.bg mail.bg dir.bg yahoo.ro
libero.it virgilio.it alice.it tiscali.it tim.it email.it fastwebnet.it
orange.fr free.fr laposte.net sfr.fr wanadoo.fr neuf.fr bbox.fr numericable.fr
telefonica.net terra.es ono.com sapo.pt
ziggo.nl kpnmail.nl home.nl planet.nl hetnet.nl chello.nl xs4all.nl telenet.be skynet.be proximus.be
mail.ru inbox.ru list.ru bk.ru internet.ru yandex.ru yandex.com ya.ru rambler.ru ukr.net i.ua meta.ua
inbox.lv inbox.lt mail.ee
btinternet.com sky.com virginmedia.com talktalk.net ntlworld.com blueyonder.co.uk
comcast.net att.net sbcglobal.net cox.net charter.net earthlink.net
zoho.com zohomail.com zohomail.eu gmx.co.uk mail.de
netcologne.de o2online.de alice.de alice-dsl.de alice-dsl.net ok.de wtnet.de nexgo.de ish.de
dg-email.de myquix.de teleos-web.de eclipso.de eclipso.eu dokom.net rz-online.de superkabel.de online.ms
gelsennet.de snafu.de skymail.de firemail.de riseup.net pt.lu ik.me duck.com passmail.net my.com
freenetmail.de freakmail.de bossmail.de justmail.de betrug.de leckmich.de
gmx-topmail.de go4more.de wolke7.net abwesend.de
berlin.de hamburg.de koeln.de muenster.de wolfsburg.de paderborn.com
naver.com hanmail.net daum.net qq.com foxmail.com 163.com 126.com yeah.net sina.com sohu.com
""".split())
