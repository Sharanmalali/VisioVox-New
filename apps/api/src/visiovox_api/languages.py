"""The languages a caption can be requested in.

Exactly the languages the deployed recognizer (Whisper large-v3) can transcribe,
so a choice here is always honoured by S7; tests/test_languages.py checks every
code against faster-whisper's own list. A language Whisper does not support is
not offered: forcing an unsupported language yields captions in some other
language, which is worse than detecting automatically.

Why a choice at all: Whisper's automatic detection misjudges accented speech
(7 of 117 English AMI-AV tracks were captioned in Dutch, Estonian, Icelandic or
Welsh; docs/29 §7). A person who knows the language should be able to say so.

Indian languages are listed first, with their names in their own scripts. Of the
22 scheduled languages of India, Whisper covers Assamese, Bengali, Gujarati,
Hindi, Kannada, Malayalam, Marathi, Nepali, Punjabi, Sanskrit, Sindhi, Tamil,
Telugu and Urdu. It does not cover Bodo, Dogri, Kashmiri, Konkani, Maithili,
Manipuri, Odia or Santali, and those are not offered. Accuracy is uneven across
the ones it covers and has not been measured on this system.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Language:
    code: str  # Whisper's code
    name: str  # in English
    native: str | None  # in its own script, where we show it
    group: Literal["india", "world"]


_INDIA: tuple[Language, ...] = (
    Language("hi", "Hindi", "हिन्दी", "india"),
    Language("bn", "Bengali", "বাংলা", "india"),
    Language("te", "Telugu", "తెలుగు", "india"),
    Language("mr", "Marathi", "मराठी", "india"),
    Language("ta", "Tamil", "தமிழ்", "india"),
    Language("ur", "Urdu", "اردو", "india"),
    Language("gu", "Gujarati", "ગુજરાતી", "india"),
    Language("kn", "Kannada", "ಕನ್ನಡ", "india"),
    Language("ml", "Malayalam", "മലയാളം", "india"),
    Language("pa", "Punjabi", "ਪੰਜਾਬੀ", "india"),
    Language("as", "Assamese", "অসমীয়া", "india"),
    Language("ne", "Nepali", "नेपाली", "india"),
    Language("sd", "Sindhi", "سنڌي", "india"),
    Language("sa", "Sanskrit", "संस्कृतम्", "india"),
)

_WORLD_NAMES: dict[str, str] = {
    "en": "English",
    "af": "Afrikaans",
    "am": "Amharic",
    "ar": "Arabic",
    "az": "Azerbaijani",
    "ba": "Bashkir",
    "be": "Belarusian",
    "bg": "Bulgarian",
    "bo": "Tibetan",
    "br": "Breton",
    "bs": "Bosnian",
    "ca": "Catalan",
    "cs": "Czech",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "es": "Spanish",
    "et": "Estonian",
    "eu": "Basque",
    "fa": "Persian",
    "fi": "Finnish",
    "fo": "Faroese",
    "fr": "French",
    "gl": "Galician",
    "ha": "Hausa",
    "haw": "Hawaiian",
    "he": "Hebrew",
    "hr": "Croatian",
    "ht": "Haitian Creole",
    "hu": "Hungarian",
    "hy": "Armenian",
    "id": "Indonesian",
    "is": "Icelandic",
    "it": "Italian",
    "ja": "Japanese",
    "jw": "Javanese",
    "ka": "Georgian",
    "kk": "Kazakh",
    "km": "Khmer",
    "ko": "Korean",
    "la": "Latin",
    "lb": "Luxembourgish",
    "ln": "Lingala",
    "lo": "Lao",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mg": "Malagasy",
    "mi": "Maori",
    "mk": "Macedonian",
    "mn": "Mongolian",
    "ms": "Malay",
    "mt": "Maltese",
    "my": "Burmese",
    "nl": "Dutch",
    "nn": "Norwegian Nynorsk",
    "no": "Norwegian",
    "oc": "Occitan",
    "pl": "Polish",
    "ps": "Pashto",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "si": "Sinhala",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sn": "Shona",
    "so": "Somali",
    "sq": "Albanian",
    "sr": "Serbian",
    "su": "Sundanese",
    "sv": "Swedish",
    "sw": "Swahili",
    "tg": "Tajik",
    "th": "Thai",
    "tk": "Turkmen",
    "tl": "Tagalog",
    "tr": "Turkish",
    "tt": "Tatar",
    "uk": "Ukrainian",
    "uz": "Uzbek",
    "vi": "Vietnamese",
    "yi": "Yiddish",
    "yo": "Yoruba",
    "yue": "Cantonese",
    "zh": "Chinese",
}

# English first (most uploads), then India, then the world alphabetically.
CAPTION_LANGUAGES: tuple[Language, ...] = (
    Language("en", "English", None, "world"),
    *_INDIA,
    *sorted(
        (
            Language(code, name, None, "world")
            for code, name in _WORLD_NAMES.items()
            if code != "en"
        ),
        key=lambda lang: lang.name,
    ),
)

CODES: frozenset[str] = frozenset(lang.code for lang in CAPTION_LANGUAGES)


def is_supported(code: str) -> bool:
    return code in CODES
