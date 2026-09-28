"""Bundled static gazetteer: Iranian provinces and major cities.

APPROXIMATE CENTROIDS ONLY.  Coordinates are rounded public reference points
(city centre / rough province centre) good to a few kilometres for cities and
tens of kilometres for provinces.  They exist for coarse nearest-branch
fallback and must never be presented as a customer's precise location.

Each record: key, kind (``province`` | ``city``), Persian name, English name,
province key (cities), lat, lon, extra aliases (Persian and English).
"""

from __future__ import annotations

# (key, name_fa, name_en, lat, lon, capital_city_key, aliases)
PROVINCES: tuple[tuple[str, str, str, float, float, str, tuple[str, ...]], ...] = (
    ("tehran_p", "تهران", "Tehran", 35.60, 51.40, "tehran", ()),
    ("alborz", "البرز", "Alborz", 35.95, 50.85, "karaj", ()),
    ("isfahan_p", "اصفهان", "Isfahan", 32.70, 51.95, "isfahan", ("اصفهان", "Esfahan")),
    ("razavi_khorasan", "خراسان رضوی", "Razavi Khorasan", 35.60, 59.00, "mashhad", ()),
    ("north_khorasan", "خراسان شمالی", "North Khorasan", 37.40, 57.00, "bojnurd", ()),
    ("south_khorasan", "خراسان جنوبی", "South Khorasan", 32.50, 59.00, "birjand", ()),
    ("east_azerbaijan", "آذربایجان شرقی", "East Azerbaijan", 37.90, 46.60, "tabriz", ()),
    ("west_azerbaijan", "آذربایجان غربی", "West Azerbaijan", 37.45, 45.30, "urmia", ()),
    ("fars", "فارس", "Fars", 29.30, 53.00, "shiraz", ()),
    ("khuzestan", "خوزستان", "Khuzestan", 31.40, 49.00, "ahvaz", ()),
    ("qom_p", "قم", "Qom", 34.65, 50.90, "qom", ()),
    ("kermanshah_p", "کرمانشاه", "Kermanshah", 34.30, 46.90, "kermanshah", ()),
    ("gilan", "گیلان", "Gilan", 37.20, 49.60, "rasht", ("Guilan",)),
    ("mazandaran", "مازندران", "Mazandaran", 36.30, 52.30, "sari", ()),
    ("golestan", "گلستان", "Golestan", 37.30, 55.10, "gorgan", ()),
    (
        "sistan_baluchestan",
        "سیستان و بلوچستان",
        "Sistan and Baluchestan",
        27.50,
        60.50,
        "zahedan",
        ("سیستان و بلوچستان",),
    ),
    ("kerman_p", "کرمان", "Kerman", 29.60, 57.30, "kerman", ()),
    ("hamadan_p", "همدان", "Hamadan", 34.80, 48.60, "hamadan", ()),
    ("yazd_p", "یزد", "Yazd", 32.10, 54.50, "yazd", ()),
    ("ardabil_p", "اردبیل", "Ardabil", 38.40, 48.00, "ardabil", ()),
    ("hormozgan", "هرمزگان", "Hormozgan", 27.10, 56.40, "bandar_abbas", ()),
    ("markazi", "مرکزی", "Markazi", 34.40, 49.80, "arak", ()),
    ("zanjan_p", "زنجان", "Zanjan", 36.50, 48.50, "zanjan", ()),
    ("kurdistan", "کردستان", "Kurdistan", 35.60, 47.00, "sanandaj", ("Kordestan",)),
    ("qazvin_p", "قزوین", "Qazvin", 36.10, 49.80, "qazvin", ()),
    ("lorestan", "لرستان", "Lorestan", 33.50, 48.30, "khorramabad", ()),
    ("bushehr_p", "بوشهر", "Bushehr", 28.70, 51.40, "bushehr", ()),
    ("ilam_p", "ایلام", "Ilam", 33.30, 46.80, "ilam", ()),
    (
        "chaharmahal",
        "چهارمحال و بختیاری",
        "Chaharmahal and Bakhtiari",
        32.10,
        50.50,
        "shahrekord",
        ("چهارمحال",),
    ),
    (
        "kohgiluyeh",
        "کهگیلویه و بویراحمد",
        "Kohgiluyeh and Boyer-Ahmad",
        30.70,
        50.90,
        "yasuj",
        ("کهگیلویه",),
    ),
    ("semnan_p", "سمنان", "Semnan", 35.30, 54.40, "semnan", ()),
)

# (key, name_fa, name_en, province_key, lat, lon, aliases)
CITIES: tuple[tuple[str, str, str, str, float, float, tuple[str, ...]], ...] = (
    ("tehran", "تهران", "Tehran", "tehran_p", 35.6892, 51.3890, ("Teheran",)),
    ("shahriar", "شهریار", "Shahriar", "tehran_p", 35.6597, 51.0592, ()),
    ("eslamshahr", "اسلامشهر", "Eslamshahr", "tehran_p", 35.5522, 51.2350, ("اسلام شهر",)),
    ("karaj", "کرج", "Karaj", "alborz", 35.8400, 50.9391, ()),
    ("isfahan", "اصفهان", "Isfahan", "isfahan_p", 32.6546, 51.6680, ("Esfahan",)),
    ("kashan", "کاشان", "Kashan", "isfahan_p", 33.9850, 51.4100, ()),
    ("mashhad", "مشهد", "Mashhad", "razavi_khorasan", 36.2605, 59.6168, ()),
    ("neyshabur", "نیشابور", "Neyshabur", "razavi_khorasan", 36.2133, 58.7958, ("Nishapur",)),
    ("bojnurd", "بجنورد", "Bojnurd", "north_khorasan", 37.4747, 57.3290, ()),
    ("birjand", "بیرجند", "Birjand", "south_khorasan", 32.8663, 59.2211, ()),
    ("tabriz", "تبریز", "Tabriz", "east_azerbaijan", 38.0800, 46.2919, ()),
    ("urmia", "ارومیه", "Urmia", "west_azerbaijan", 37.5527, 45.0761, ("Orumiyeh",)),
    ("shiraz", "شیراز", "Shiraz", "fars", 29.5918, 52.5837, ()),
    ("nurabad_mamasani", "نورآباد", "Nurabad", "fars", 30.1142, 51.5217, ("نورآباد ممسنی",)),
    ("ahvaz", "اهواز", "Ahvaz", "khuzestan", 31.3183, 48.6706, ("Ahwaz",)),
    ("dezful", "دزفول", "Dezful", "khuzestan", 32.3811, 48.4058, ()),
    ("abadan", "آبادان", "Abadan", "khuzestan", 30.3392, 48.3043, ()),
    ("qom", "قم", "Qom", "qom_p", 34.6399, 50.8759, ("Ghom",)),
    ("kermanshah", "کرمانشاه", "Kermanshah", "kermanshah_p", 34.3142, 47.0650, ()),
    ("rasht", "رشت", "Rasht", "gilan", 37.2808, 49.5832, ()),
    (
        "astaneh_ashrafiyeh",
        "آستانه اشرفیه",
        "Astaneh-ye Ashrafiyeh",
        "gilan",
        37.2598,
        49.9437,
        ("آستانه",),
    ),
    ("sari", "ساری", "Sari", "mazandaran", 36.5633, 53.0601, ()),
    ("gorgan", "گرگان", "Gorgan", "golestan", 36.8427, 54.4439, ()),
    ("zahedan", "زاهدان", "Zahedan", "sistan_baluchestan", 29.4963, 60.8629, ()),
    ("kerman", "کرمان", "Kerman", "kerman_p", 30.2839, 57.0834, ()),
    ("hamadan", "همدان", "Hamadan", "hamadan_p", 34.7983, 48.5146, ()),
    ("yazd", "یزد", "Yazd", "yazd_p", 31.8974, 54.3569, ()),
    ("ardabil", "اردبیل", "Ardabil", "ardabil_p", 38.2498, 48.2933, ()),
    ("bandar_abbas", "بندرعباس", "Bandar Abbas", "hormozgan", 27.1832, 56.2666, ("بندر عباس",)),
    ("kish", "کیش", "Kish", "hormozgan", 26.5578, 54.0194, ()),
    ("arak", "اراک", "Arak", "markazi", 34.0917, 49.6892, ()),
    ("astaneh_markazi", "آستانه", "Astaneh", "markazi", 33.8860, 49.3530, ()),
    ("zanjan", "زنجان", "Zanjan", "zanjan_p", 36.6736, 48.4787, ()),
    ("sanandaj", "سنندج", "Sanandaj", "kurdistan", 35.3219, 46.9862, ()),
    ("qazvin", "قزوین", "Qazvin", "qazvin_p", 36.2688, 50.0041, ()),
    (
        "khorramabad",
        "خرم آباد",
        "Khorramabad",
        "lorestan",
        33.4878,
        48.3558,
        ("خرم‌آباد", "خرمآباد"),
    ),
    ("nurabad_delfan", "نورآباد", "Nurabad", "lorestan", 34.0734, 47.9725, ("نورآباد دلفان",)),
    ("bushehr", "بوشهر", "Bushehr", "bushehr_p", 28.9234, 50.8203, ()),
    ("ilam", "ایلام", "Ilam", "ilam_p", 33.6374, 46.4227, ()),
    ("shahrekord", "شهرکرد", "Shahrekord", "chaharmahal", 32.3256, 50.8644, ()),
    ("yasuj", "یاسوج", "Yasuj", "kohgiluyeh", 30.6682, 51.5880, ()),
    ("semnan", "سمنان", "Semnan", "semnan_p", 35.5769, 53.3953, ()),
)

# Bare names that are genuinely ambiguous between several provinces and not a
# place in their own right ("Azerbaijan" = East or West).
AMBIGUOUS_REGION_ALIASES: dict[str, tuple[str, ...]] = {
    "آذربایجان": ("east_azerbaijan", "west_azerbaijan"),
    "Azerbaijan": ("east_azerbaijan", "west_azerbaijan"),
    "خراسان": ("razavi_khorasan", "north_khorasan", "south_khorasan"),
}

# Iran bounding box (approximate), used for a warning, never a hard error.
IRAN_BBOX = {"min_lat": 25.0, "max_lat": 39.8, "min_lon": 44.0, "max_lon": 63.4}
