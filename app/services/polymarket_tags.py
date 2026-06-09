"""Polymarket tag ID reference — sourced from GET https://gamma-api.polymarket.com/tags.

Use tag_id (integer) when filtering /markets or /events endpoints.
tag_slug is silently ignored by the Gamma API.
"""

from typing import NamedTuple


class Tag(NamedTuple):
    id: int
    label: str
    slug: str


# ---------------------------------------------------------------------------
# Geopolitics & International Affairs — most relevant for CI-DESK
# ---------------------------------------------------------------------------
INTERNATIONAL_AFFAIRS   = Tag(1396, "international affairs",  "international-affairs")
FOREIGN_AFFAIRS         = Tag(842,  "foreign affairs",        "foreign-affairs")
MILITARY_INVASION       = Tag(1308, "military invasion",      "military-invasion")
HOSTAGE_CRISIS          = Tag(1542, "hostage crisis",         "hostage-crisis")
HANIYEH                 = Tag(733,  "haniyeh",                "haniyeh")
MARITIME_TRANSPORT      = Tag(777,  "maritime transport",     "maritime-transport")
INTELLIGENCE            = Tag(411,  "intelligence",           "intelligence")
KOREA                   = Tag(103269, "Korea",                "korea")
GEOPOLITICS             = Tag(100265, "Geopolitics",          "geopolitics")

# ---------------------------------------------------------------------------
# US Government & Policy
# ---------------------------------------------------------------------------
FEDERAL_GOVERNMENT      = Tag(933,  "federal government",     "federal-government")
HOUSE_RACES             = Tag(100344, "House Races",          "house-races")
CAUCUS                  = Tag(1558, "caucus",                 "caucus")
CONCESSION              = Tag(101172, "concession",           "concession")
DEMOCRATIC_ALLIANCE     = Tag(1588, "democratic alliance",    "democratic-alliance")
ASYLUM                  = Tag(100253, "asylum",               "asylum")
LEGAL_CASES             = Tag(757,  "legal cases",            "legal-cases")
CONTROVERSIES           = Tag(790,  "controversies",          "controversies")
HEALTHCARE              = Tag(101422, "healthcare",           "healthcare")
THAILAND_ELECTION       = Tag(103388, "Thailand Election",    "thailand-election")

# ---------------------------------------------------------------------------
# Macroeconomics & Finance
# ---------------------------------------------------------------------------
GDP                     = Tag(370,  "GDP",                    "gdp")
MACRO_INDICATORS        = Tag(102000, "Macro Indicators",     "macro-indicators")
MACRO_GRAPH             = Tag(101247, "Macro Graph",          "macro-graph")
MACRO_SINGLE            = Tag(101250, "Macro Single",         "macro-single")
ETF                     = Tag(833,  "ETF",                    "etf")
FDIC                    = Tag(101026, "FDIC",                 "fdic")
COP                     = Tag(103482, "COP",                  "cop")

# ---------------------------------------------------------------------------
# Technology
# ---------------------------------------------------------------------------
OPENAI                  = Tag(537,  "OpenAI",                 "openai")
CLAUDE                  = Tag(103303, "Claude",               "claude")
DEFI_APP                = Tag(102825, "Defi App",             "defi-app")

# ---------------------------------------------------------------------------
# Crypto
# ---------------------------------------------------------------------------
ALTCOINS                = Tag(101611, "Altcoins",             "altcoins")

# ---------------------------------------------------------------------------
# People — Political / Public Figures
# ---------------------------------------------------------------------------
MICHELLE_OBAMA          = Tag(945,  "michelle obama",         "michelle-obama")
TULSI_GABBARD           = Tag(101746, "Tulsi Gabbard",        "tulsi-gabbard")
AMY_KLOBUCHAR           = Tag(100548, "amy klobuchar",        "amy-klobuchar")
ALY_NGOUILLE_NDIAYE     = Tag(1623, "aly ngouille ndiaye",    "aly-ngouille-ndiaye")
KEITH_GILL              = Tag(100257, "keith gill",           "keith-gill")
SAM_BANKMAN_FRIED       = Tag(101901, "Sam Bankman-Fried",    "sam-bankman-fried")
BRIAN_KELLY             = Tag(1225, "brian kelly",            "brian-kelly")

# ---------------------------------------------------------------------------
# Sports (included for completeness, not relevant for CI-DESK)
# ---------------------------------------------------------------------------
CHAMPIONS_LEAGUE        = Tag(1234, "Champions League",       "champions-league")
EUROPA_LEAGUE           = Tag(100626, "Europa League",        "europa-league")
MAJOR_LEAGUE_CRICKET    = Tag(103813, "Major League Cricket", "major-league-cricket")
INVESTEC_CHAMPIONS_CUP  = Tag(103161, "Investec Champions Cup", "investec-champions-cup")
GREEN_BAY_PACKERS       = Tag(1188, "green bay packers",      "green-bay-packers")
CAITLIN_CLARK           = Tag(1512, "caitlin clark",          "caitlin-clark")
MAVERICKS               = Tag(77,   "mavericks",              "mavericks")
FLORIDA_PANTHERS        = Tag(100130, "Florida Panthers",     "florida-panthers")
SAN_JOSE_SHARKS         = Tag(100162, "San Jose Sharks",      "san-jose-sharks")
REDBULL                 = Tag(100392, "redbull",              "redbull")
OHTANI                  = Tag(100746, "Ohtani",               "ohtani")
TIGER_WOODS             = Tag(104248, "Tiger Woods",          "tiger-woods")
QB                      = Tag(102076, "QB",                   "qb")
KANSAS                  = Tag(101091, "Kansas",               "kansas")
VIKTOR_GYOKERES         = Tag(101286, "Victor Gyökeres",      "victor-gyokeres")
TOM_ASPINAL             = Tag(101592, "Tom Aspinal",          "tom-aspinal")
NL_CENTRAL              = Tag(101994, "NL Central",           "nl-central")
COLLEGE_FOOTBALL        = Tag(102934, "college Football Playoffs", "college-football-playoffs")
ECF_MVP                 = Tag(104581, "Ecf mvp",              "ecf-mvp")
VITALITY                = Tag(104393, "Vitality",             "vitality")
STOKE_CITY              = Tag(101104, "Stoke City",           "stoke-city")
SLOVAN_BRATISLAVA       = Tag(101003, "Slovan Bratislava",    "slovan-bratislava")
VIKTORIA_PLZEN          = Tag(101025, "Viktoria Plzen",       "viktoria-plzen")
TFF_SUPER_KUPA          = Tag(104333, "TFF Super Kupa",       "soccer-trsk")
EGYPT_PREMIER_LEAGUE    = Tag(104397, "egypt premier league", "egypt-premier-league")

# ---------------------------------------------------------------------------
# Entertainment / Misc
# ---------------------------------------------------------------------------
SEASON_FINALE           = Tag(330,  "season finale",          "season-finale")
CHRISTMAS               = Tag(794,  "Christmas",              "christmas")
POPULARITY              = Tag(1571, "popularity",             "popularity")
TRADITION               = Tag(713,  "tradition",              "tradition")
TRADITIONS              = Tag(979,  "traditions",             "traditions")
WILDFIRE                = Tag(101655, "Wildfire",             "wildfire")
LOS_ANGELES             = Tag(100743, "Los Angeles",          "los-angeles")
PHILLY                  = Tag(100735, "Philly",               "philly")
ONTARIO                 = Tag(101706, "Ontario",              "ontario")
INFLUENZA               = Tag(103357, "Influenza",            "influenza")
LANA_DEL_REY            = Tag(1054, "lana del rey",           "lana-del-rey")
TIMOTHEE_CHALAMET       = Tag(102136, "Timothée Chalamet",    "timothee-chalamet")
TALK_TUAH               = Tag(101196, "Talk Tuah",            "talk-tuah")
JERRY_AFTER_DARK        = Tag(101457, "Jerry After Dark",     "jerry-after-dark")
HUSTLER_CASINO_LIVE     = Tag(104156, "Hustler Casino Live",  "hustler-casino-live")
CAESARS_ENTERTAINMENT   = Tag(104208, "Caesars entertainment","caesars-entertainment")
WIRELESS_FESTIVAL       = Tag(104475, "Wireless festival",    "wireless-festival")
CYBERTRUCK              = Tag(101604, "Cybertruck",           "cybertruck")

# ---------------------------------------------------------------------------
# Curated sets for CI-DESK dashboard filtering
# ---------------------------------------------------------------------------
GEOPOLITICAL_TAGS: list[Tag] = [
    GEOPOLITICS,
    INTERNATIONAL_AFFAIRS,
    FOREIGN_AFFAIRS,
    MILITARY_INVASION,
    HOSTAGE_CRISIS,
    MARITIME_TRANSPORT,
]

MACRO_TAGS: list[Tag] = [
    GDP,
    MACRO_INDICATORS,
    MACRO_GRAPH,
    MACRO_SINGLE,
    FEDERAL_GOVERNMENT,
    ETF,
]
