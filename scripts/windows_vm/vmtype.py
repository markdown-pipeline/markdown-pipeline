"""Type text into a libvirt VM's keyboard (US layout) with `virsh send-key`, for the rare case no network channel exists yet:  vmtype.py DOMAIN "text" [--enter]"""
import subprocess
import sys

PLAIN = {c: f"KEY_{c.upper()}" for c in "abcdefghijklmnopqrstuvwxyz0123456789"}
PLAIN.update({" ": "KEY_SPACE", "-": "KEY_MINUS", "=": "KEY_EQUAL", "[": "KEY_LEFTBRACE", "]": "KEY_RIGHTBRACE", ";": "KEY_SEMICOLON", "'": "KEY_APOSTROPHE",
              "`": "KEY_GRAVE", "\\": "KEY_BACKSLASH", ",": "KEY_COMMA", ".": "KEY_DOT", "/": "KEY_SLASH", "\n": "KEY_ENTER"})
SHIFTED = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]", ":": ";",
           '"': "'", "~": "`", "|": "\\", "<": ",", ">": ".", "?": "/"}


def keys_for(ch: str) -> list[str]:
    if ch in PLAIN:
        return [PLAIN[ch]]
    if ch.isupper() and ch.lower() in PLAIN:
        return ["KEY_LEFTSHIFT", PLAIN[ch.lower()]]
    if ch in SHIFTED:
        return ["KEY_LEFTSHIFT", PLAIN[SHIFTED[ch]]]
    raise ValueError(f"cannot type {ch!r}")


def type_text(domain: str, text: str, enter: bool = False, uri: str = "qemu:///system") -> None:
    for ch in text + ("\n" if enter else ""):
        subprocess.run(["virsh", "-c", uri, "send-key", domain, "--holdtime", "100", *keys_for(ch)], check=True, capture_output=True)


if __name__ == "__main__":
    type_text(sys.argv[1], sys.argv[2], enter="--enter" in sys.argv[3:])
