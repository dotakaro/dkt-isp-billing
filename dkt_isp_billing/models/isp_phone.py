"""Parser nomor HP/WA dari username PPPoE secret.

Pola: ``{nama_depan}-{nomor_hp}`` contoh ``dul-081376756102``.
Bukan parser comment ``Nama - Area: harga``.
"""
import re

# Utamakan suffix 08… / 62… di ujung username.
_RE_LOCAL = re.compile(r'-0[0-9]{8,14}$')
_RE_INTL = re.compile(r'-62[0-9]{8,14}$')
_RE_LAST_DIGITS = re.compile(r'-([0-9]{9,15})$')
_RE_PHONE_STRIP = re.compile(r'-(0|62|8)')
_RE_UNICODE_DASH = re.compile(r'[–—‑‒−]')
_RE_PHONE_TAIL = re.compile(r'^[0-9.\s\-]+$')


def _normalize_dashes(value):
    return _RE_UNICODE_DASH.sub('-', str(value or ''))


def digits_only(value):
    return re.sub(r'\D', '', str(value or ''))


def looks_like_id_mobile(raw):
    """True jika potongan terlihat nomor HP Indonesia (08 / 628 / 62 / 8)."""
    digits = digits_only(raw)
    if not (9 <= len(digits) <= 15):
        return False
    return (
        digits.startswith('08')
        or digits.startswith('628')
        or digits.startswith('62')
        or digits.startswith('8')
    )


def pack_id_mobile_token(raw):
    """Pack token HP (digit + pemisah) jadi digit. Username berhuruf: False.

    ``081-4859-384`` / ``081 4859 384`` → ``0814859384``.
    ``dkt-uat-08116343031`` tetap False (ada huruf).
    Username + HP ber-minus: ``pack_username_phone_suffix``.
    """
    text = str(raw or '').strip()
    if not text or re.search(r'[A-Za-z]', text):
        return False
    if not looks_like_id_mobile(text):
        return False
    return digits_only(text) or False


def pack_username_phone_suffix(raw):
    """Pack suffix HP di username, nama berhuruf tetap utuh.

    ``username-0812-12384-9485`` → ``username-0812123849485``.
    ``dkt-uat-081-1634-3031`` → ``dkt-uat-08116343031``.
    ``lama-daftar-08116343031`` tetap sama. Token HP murni: False.
    """
    text = _normalize_dashes(str(raw or '').strip())
    if not text or not re.search(r'[A-Za-z]', text):
        return False
    parsed = parse_phone_from_username(text)
    if not parsed or not parsed.get('first_name') or not parsed.get('digits'):
        return False
    return '%s-%s' % (parsed['first_name'], parsed['digits'])


def username_phone_match(left, right):
    """True jika username sama, atau nama + nomor HP setara (08/62, minus)."""
    a = _normalize_dashes((left or '').strip())
    b = _normalize_dashes((right or '').strip())
    if not a or not b:
        return False
    if a.lower() == b.lower():
        return True
    pa = parse_phone_from_username(a)
    pb = parse_phone_from_username(b)
    if not pa or not pb:
        return False
    return (
        pa['first_name'].lower() == pb['first_name'].lower()
        and pa['phone_wa'] == pb['phone_wa']
    )


def normalize_id_phone(raw):
    """Kembalikan phone lokal 08… dan WA 62…. False jika bukan nomor HP."""
    digits = digits_only(raw)
    if not looks_like_id_mobile(digits):
        return False
    if digits.startswith('08'):
        local = digits
        wa = '62' + digits[1:]
    elif digits.startswith('62'):
        wa = digits
        local = '0' + digits[2:]
    elif digits.startswith('8'):
        local = '0' + digits
        wa = '62' + digits
    else:
        return False
    if not local.startswith('0') or not wa.startswith('62'):
        return False
    if not (10 <= len(local) <= 16 and 11 <= len(wa) <= 16):
        return False
    return {
        'phone': local,
        'phone_wa': wa,
        'digits': digits,
    }


def phones_equivalent(left, right):
    """Bandingkan dua nomor setelah normalisasi WA 62…."""
    a = normalize_id_phone(left)
    b = normalize_id_phone(right)
    if not a or not b:
        return False
    return a['phone_wa'] == b['phone_wa']


def _from_last_phone_strip(username):
    """Potong dari hyphen terakhir yang memulai nomor (08 / 62 / 8)."""
    matches = list(_RE_PHONE_STRIP.finditer(username))
    for match in reversed(matches):
        tail = username[match.start() + 1:]
        if not _RE_PHONE_TAIL.fullmatch(tail):
            continue
        digits = digits_only(tail)
        if looks_like_id_mobile(digits):
            return username[:match.start()], digits
    return '', ''


def parse_phone_from_username(username):
    """Ambil nama depan + nomor dari username secret.

    Return dict atau False. Tidak mengarang nomor jika pola tidak cocok.
    """
    raw = _normalize_dashes((username or '').strip())
    if not raw:
        return False

    if _RE_LOCAL.search(raw) or _RE_INTL.search(raw):
        name, tail = raw.rsplit('-', 1)
        normalized = normalize_id_phone(tail)
        if normalized:
            return _result(raw, name, normalized)

    match = _RE_LAST_DIGITS.search(raw)
    if match and looks_like_id_mobile(match.group(1)):
        normalized = normalize_id_phone(match.group(1))
        if normalized:
            return _result(raw, raw[:match.start()], normalized)

    name, digits = _from_last_phone_strip(raw)
    if digits:
        normalized = normalize_id_phone(digits)
        if normalized:
            return _result(raw, name, normalized)
    return False


def _result(username, first_name, normalized):
    return {
        'username': username,
        'first_name': (first_name or '').strip(),
        'phone': normalized['phone'],
        'phone_wa': normalized['phone_wa'],
        'digits': normalized['digits'],
    }


def mask_username(username):
    """Samarkan username yang mengandung nomor. Tanpa nomor: potong pendek."""
    raw = (username or '').strip()
    if not raw:
        return '-'
    parsed = parse_phone_from_username(raw)
    if parsed:
        local = parsed['phone']
        name = parsed['first_name'] or raw[:3]
        if len(local) >= 8:
            return '%s-%s***%s' % (name, local[:3], local[-3:])
        return '%s-***' % name
    if len(raw) <= 6:
        return raw
    return raw[:6] + '***'


def mask_phone(phone):
    digits = digits_only(phone)
    if len(digits) < 8:
        return '***'
    return '%s***%s' % (digits[:3], digits[-3:])
