"""The languages captions can be translated into, and from.

Translation runs NLLB-200 (Meta AI, "No Language Left Behind"; distilled 1.3B,
CC-BY-NC 4.0 - used here non-commercially) on the worker (ADR-0020). NLLB
names languages by FLORES-200 codes; the product uses short codes, so both
directions are mapped here.

Targets: English, 17 scheduled languages of India, and 22 of the world's
most-spoken languages. Four Indian languages that cannot be *captioned*
(Whisper does not recognise them) can be translated *into*: Odia, Maithili,
Kashmiri and Manipuri - a Kannada meeting can be read in Odia.

Not offered, each for a reason:
- Santali (sat_Olck) and Konkani (gom_Deva): NLLB lists them, but with the
  released SentencePiece model every test sentence came back as "⁇" pieces
  and untranslated English (checked 2026-10-09 on 40 targets).
- Bodo and Dogri: not in NLLB.
Output quality is NLLB's own and has not been measured on this system.

Sources: whatever Whisper wrote the captions in, where NLLB knows it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TranslationLanguage:
    code: str  # the product's code
    nllb: str  # FLORES-200
    name: str  # in English
    native: str  # in its own script
    group: Literal["india", "world"]


TRANSLATION_LANGUAGES: tuple[TranslationLanguage, ...] = (
    TranslationLanguage("en", "eng_Latn", "English", "English", "world"),
    TranslationLanguage("hi", "hin_Deva", "Hindi", "हिन्दी", "india"),
    TranslationLanguage("bn", "ben_Beng", "Bengali", "বাংলা", "india"),
    TranslationLanguage("te", "tel_Telu", "Telugu", "తెలుగు", "india"),
    TranslationLanguage("mr", "mar_Deva", "Marathi", "मराठी", "india"),
    TranslationLanguage("ta", "tam_Taml", "Tamil", "தமிழ்", "india"),
    TranslationLanguage("ur", "urd_Arab", "Urdu", "اردو", "india"),
    TranslationLanguage("gu", "guj_Gujr", "Gujarati", "ગુજરાતી", "india"),
    TranslationLanguage("kn", "kan_Knda", "Kannada", "ಕನ್ನಡ", "india"),
    TranslationLanguage("or", "ory_Orya", "Odia", "ଓଡ଼ିଆ", "india"),
    TranslationLanguage("ml", "mal_Mlym", "Malayalam", "മലയാളം", "india"),
    TranslationLanguage("pa", "pan_Guru", "Punjabi", "ਪੰਜਾਬੀ", "india"),
    TranslationLanguage("as", "asm_Beng", "Assamese", "অসমীয়া", "india"),
    TranslationLanguage("mai", "mai_Deva", "Maithili", "मैथिली", "india"),
    TranslationLanguage("ks", "kas_Arab", "Kashmiri", "كٲشُر", "india"),
    TranslationLanguage("ne", "npi_Deva", "Nepali", "नेपाली", "india"),
    TranslationLanguage("sd", "snd_Arab", "Sindhi", "سنڌي", "india"),
    TranslationLanguage("mni", "mni_Beng", "Manipuri", "মৈতৈলোন্", "india"),
    TranslationLanguage("sa", "san_Deva", "Sanskrit", "संस्कृतम्", "india"),
    TranslationLanguage("ar", "arb_Arab", "Arabic", "العربية", "world"),
    TranslationLanguage("zh", "zho_Hans", "Chinese (Simplified)", "简体中文", "world"),
    TranslationLanguage("nl", "nld_Latn", "Dutch", "Nederlands", "world"),
    TranslationLanguage("fr", "fra_Latn", "French", "Français", "world"),
    TranslationLanguage("de", "deu_Latn", "German", "Deutsch", "world"),
    TranslationLanguage("id", "ind_Latn", "Indonesian", "Bahasa Indonesia", "world"),
    TranslationLanguage("it", "ita_Latn", "Italian", "Italiano", "world"),
    TranslationLanguage("ja", "jpn_Jpan", "Japanese", "日本語", "world"),
    TranslationLanguage("ko", "kor_Hang", "Korean", "한국어", "world"),
    TranslationLanguage("ms", "zsm_Latn", "Malay", "Bahasa Melayu", "world"),
    TranslationLanguage("fa", "pes_Arab", "Persian", "فارسی", "world"),
    TranslationLanguage("pl", "pol_Latn", "Polish", "Polski", "world"),
    TranslationLanguage("pt", "por_Latn", "Portuguese", "Português", "world"),
    TranslationLanguage("ru", "rus_Cyrl", "Russian", "Русский", "world"),
    TranslationLanguage("si", "sin_Sinh", "Sinhala", "සිංහල", "world"),
    TranslationLanguage("es", "spa_Latn", "Spanish", "Español", "world"),
    TranslationLanguage("sw", "swh_Latn", "Swahili", "Kiswahili", "world"),
    TranslationLanguage("th", "tha_Thai", "Thai", "ไทย", "world"),
    TranslationLanguage("tr", "tur_Latn", "Turkish", "Türkçe", "world"),
    TranslationLanguage("uk", "ukr_Cyrl", "Ukrainian", "Українська", "world"),
    TranslationLanguage("vi", "vie_Latn", "Vietnamese", "Tiếng Việt", "world"),
)

BY_CODE: dict[str, TranslationLanguage] = {lang.code: lang for lang in TRANSLATION_LANGUAGES}

# What Whisper may have written captions in (its codes), as FLORES-200. Codes
# NLLB lacks (Latin, Breton, Hawaiian) are absent: captions in those cannot be
# translated, and the job says so rather than guessing.
SOURCE_FLORES: dict[str, str] = {
    **{lang.code: lang.nllb for lang in TRANSLATION_LANGUAGES},
    "af": "afr_Latn", "am": "amh_Ethi", "az": "azj_Latn", "ba": "bak_Cyrl",
    "be": "bel_Cyrl", "bg": "bul_Cyrl", "bo": "bod_Tibt", "bs": "bos_Latn",
    "ca": "cat_Latn", "cs": "ces_Latn", "cy": "cym_Latn", "da": "dan_Latn",
    "el": "ell_Grek", "et": "est_Latn", "eu": "eus_Latn", "fi": "fin_Latn",
    "fo": "fao_Latn", "gl": "glg_Latn", "ha": "hau_Latn", "he": "heb_Hebr",
    "hr": "hrv_Latn", "ht": "hat_Latn", "hu": "hun_Latn", "hy": "hye_Armn",
    "is": "isl_Latn", "jw": "jav_Latn", "ka": "kat_Geor", "kk": "kaz_Cyrl",
    "km": "khm_Khmr", "lb": "ltz_Latn", "ln": "lin_Latn", "lo": "lao_Laoo",
    "lt": "lit_Latn", "lv": "lvs_Latn", "mg": "plt_Latn", "mi": "mri_Latn",
    "mk": "mkd_Cyrl", "mn": "khk_Cyrl", "mt": "mlt_Latn", "my": "mya_Mymr",
    "nn": "nno_Latn", "no": "nob_Latn", "oc": "oci_Latn", "ps": "pbt_Arab",
    "ro": "ron_Latn", "sk": "slk_Latn", "sl": "slv_Latn", "sn": "sna_Latn",
    "so": "som_Latn", "sq": "als_Latn", "sr": "srp_Cyrl", "su": "sun_Latn",
    "sv": "swe_Latn", "tg": "tgk_Cyrl", "tk": "tuk_Latn", "tl": "tgl_Latn",
    "tt": "tat_Cyrl", "uz": "uzn_Latn", "yi": "ydd_Hebr", "yo": "yor_Latn",
    "yue": "yue_Hant",
}  # fmt: skip


# Languages a recording can be dubbed into: translation targets that the voice
# model (Indic Parler-TTS) has named voices for. Must equal the keys of
# ml/pipeline/s12_dub.VOICES (tests/test_dub_api.py checks).
DUB_LANGUAGES: tuple[str, ...] = (
    "hi", "bn", "te", "mr", "ta", "gu", "kn", "or", "ml", "pa", "as", "ne", "sa", "mni", "en",
)  # fmt: skip


def is_target(code: str) -> bool:
    return code in BY_CODE
