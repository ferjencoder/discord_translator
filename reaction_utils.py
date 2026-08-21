from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReactionLanguage:
    lang: str
    label: str


# Multiple country flags may intentionally map to one language.
FLAG_LANGUAGES: dict[str, ReactionLanguage] = {
    "🇬🇧": ReactionLanguage("en", "English"),
    "🇺🇸": ReactionLanguage("en", "English"),
    "🇪🇸": ReactionLanguage("es", "Spanish"),
    "🇫🇷": ReactionLanguage("fr", "French"),
    "🇵🇹": ReactionLanguage("pt", "Portuguese"),
    "🇧🇷": ReactionLanguage("pt", "Portuguese"),
    "🇸🇪": ReactionLanguage("sv", "Swedish"),
    "🇩🇪": ReactionLanguage("de", "German"),
    "🇵🇭": ReactionLanguage("ceb", "Bisaya"),
    "🇷🇺": ReactionLanguage("ru", "Russian"),
    "🇸🇦": ReactionLanguage("ar", "Arabic"),
    "🇳🇴": ReactionLanguage("no", "Norwegian"),
}

# Canonical display flag for each target language.
LANG_FLAGS: dict[str, str] = {
    "en": "🇬🇧",
    "es": "🇪🇸",
    "fr": "🇫🇷",
    "pt": "🇵🇹",
    "sv": "🇸🇪",
    "de": "🇩🇪",
    "ceb": "🇵🇭",
    "ru": "🇷🇺",
    "ar": "🇸🇦",
    "no": "🇳🇴",
}


def language_for_emoji(emoji: str) -> ReactionLanguage | None:
    return FLAG_LANGUAGES.get(emoji)


def canonical_flag(lang: str) -> str:
    return LANG_FLAGS.get(lang, "🌐")


def label_for_language(lang: str) -> str:
    for value in FLAG_LANGUAGES.values():
        if value.lang == lang:
            return value.label
    return lang.upper()
