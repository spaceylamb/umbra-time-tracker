from . import (butler, chrome, claude_code, git, intent, iphone, knowledgec,
               location, media, plaud, weread, whoop, zsh)

ALL = {
    "chrome": chrome,
    "claude": claude_code,
    "butler": butler,
    "zsh": zsh,
    "git": git,
    "knowledgec": knowledgec,
    "iphone": iphone,
    "media": media,
    "intent": intent,
    "location": location,
    "whoop": whoop,
    "weread": weread,
    "plaud": plaud,
}

# Importer name -> the name it writes into events.source.
# Mostly the same, but knowledgec writes "app". Without this mapping the
# freshness report splits one source into two rows: one forever "never
# imported", the other forever without an import record.
SOURCE_OF = {name: getattr(mod, "SOURCE", name) for name, mod in ALL.items()}
