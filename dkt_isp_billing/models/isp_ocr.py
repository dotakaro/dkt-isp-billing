"""Ekstraksi teks bukti transfer. Tidak mengklaim akurasi 100%."""

import io
import logging
import re
import unicodedata
from datetime import date, datetime
from difflib import SequenceMatcher

_logger = logging.getLogger(__name__)

AMOUNT_RE = re.compile(
    r'(?:rp\.?|idr)?\s*([0-9]{1,3}(?:[.\s][0-9]{3})+(?:,[0-9]{1,2})?|[0-9]{4,9}(?:[.,][0-9]{2})?)',
    re.IGNORECASE,
)
DATE_RE = re.compile(r'(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4})')
REF_RE = re.compile(
    r'(?:ref|no\.?|trx|id|berita)[:\s#]*([A-Z0-9][A-Z0-9\-]{5,})',
    re.IGNORECASE,
)
BRIMO_REF_RE = re.compile(
    r'(?:no\.?\s*ref|nomer\s*ref|nomor\s*ref|reference)\s*[:.]?\s*([0-9]{8,20})',
    re.IGNORECASE,
)
BRIMO_STATUS_OK_RE = re.compile(r'transaksi\s+berhasil', re.IGNORECASE)
BRIMO_STATUS_FAIL_RE = re.compile(r'transaksi\s+gagal|transaksi\s+dibatalkan', re.IGNORECASE)
BRIMO_TOTAL_RE = re.compile(
    r'(?:total\s+transaksi|nominal)\s*:?\s*rp\.?\s*([0-9]{1,3}(?:[.\s][0-9]{3})+(?:,[0-9]{1,2})?)',
    re.IGNORECASE,
)
BRIMO_ADMIN_FEE_RE = re.compile(
    r'biaya\s+admin\s*:?\s*rp\.?\s*([0-9.]+)',
    re.IGNORECASE,
)
BRIMO_TYPE_RE = re.compile(
    r'jenis\s+transaksi\s*:?\s*([^\n]{3,40})',
    re.IGNORECASE,
)
ID_MONTHS = {
    'januari': 1, 'jan': 1,
    'februari': 2, 'feb': 2,
    'maret': 3, 'mar': 3,
    'april': 4, 'apr': 4,
    'mei': 5,
    'juni': 6, 'jun': 6,
    'juli': 7, 'jul': 7,
    'agustus': 8, 'agu': 8, 'ags': 8, 'aug': 8,
    'september': 9, 'sep': 9,
    'oktober': 10, 'okt': 10, 'oct': 10,
    'november': 11, 'nov': 11,
    'desember': 12, 'des': 12, 'dec': 12,
}
DATE_ID_RE = re.compile(
    r'(\d{1,2})\s+'
    r'(januari|februari|maret|april|mei|juni|juli|agustus|september|oktober|november|desember|'
    r'jan|feb|mar|apr|jun|jul|agu|ags|aug|sep|okt|oct|nov|des|dec)\.?\s+'
    r'(\d{4})',
    re.IGNORECASE,
)
ACCOUNT_SPACED_RE = re.compile(r'\b(\d{4}(?:[\s\-]+\d{4}){2}[\s\-]+\d{3,4})\b')
ACCOUNT_DIGITS_RE = re.compile(r'\b(\d{10,16})\b')
MASKED_ACCOUNT_RE = re.compile(r'(\d{3,4}\s*\*{2,4}(?:\s*\*{2,4})+\s*\d{2,4})')
BANK_RE = re.compile(r'\b(BANK\s+[A-Z]{2,12}|BRI|BCA|BNI|MANDIRI|BTN|BSI|CIMB)\b', re.IGNORECASE)
PERSON_NAME_RE = re.compile(
    r'\b([A-Z][A-Z]+(?:\s+(?:BR|BIN|BINTI|BINT|S\.?|H\.?|HJ\.?|[A-Z][A-Z]+)){1,6})\b',
)
AUTO_POST_ENGINES = ('pytesseract', 'pypdf', 'odoo_ai')


def parse_amount_id(raw):
    """Ubah string nominal Indonesia/internasional menjadi float, atau None."""
    if not raw:
        return None
    raw = re.sub(r'\s+', '', str(raw)).strip()
    if re.match(r'^\d{1,3}(\.\d{3})+(,\d{1,2})?$', raw):
        raw = raw.replace('.', '').replace(',', '.')
    elif re.match(r'^\d{1,3}(,\d{3})+(\.\d{1,2})?$', raw):
        raw = raw.replace(',', '')
    else:
        raw = raw.replace(',', '.')
    try:
        value = float(raw)
    except ValueError:
        return None
    if value < 1000 or value > 50000000:
        return None
    return value


def normalize_account_number(raw):
    """Hanya digit rekening, tanpa spasi/tanda."""
    if not raw:
        return ''
    return re.sub(r'\D', '', str(raw))


def normalize_person_name(name):
    """Huruf besar, tanpa aksen, partikel umum dibuang untuk perbandingan."""
    if not name:
        return ''
    text = unicodedata.normalize('NFKD', str(name))
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r'[^A-Za-z0-9\s]', ' ', text).upper()
    skip = {'BR', 'BIN', 'BINTI', 'BINT', 'S', 'H', 'HJ', 'IR', 'DR', 'SE', 'SH'}
    tokens = [tok for tok in text.split() if tok and tok not in skip]
    return ' '.join(tokens)


def names_similar(left, right, threshold=0.72):
    """True jika nama orang cukup mirip (token overlap atau rasio)."""
    a = normalize_person_name(left)
    b = normalize_person_name(right)
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / float(max(len(ta | tb), 1))
    seq = SequenceMatcher(None, a, b).ratio()
    return max(overlap, seq) >= threshold


def name_similarity_score(left, right):
    """Skor 0..1 untuk pencocokan pengirim vs pelanggan."""
    a = normalize_person_name(left)
    b = normalize_person_name(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    overlap = len(ta & tb) / float(max(len(ta | tb), 1)) if ta and tb else 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    return max(overlap, seq)


def accounts_match(ocr_account, configured):
    """Bandingkan nomor rekening setelah dinormalisasi."""
    left = normalize_account_number(ocr_account)
    right = normalize_account_number(configured)
    if not left or not right:
        return False
    return left == right


def empty_parse_result():
    return {
        'amount': False,
        'date': False,
        'ref': False,
        'confidence': 0.0,
        'status': False,
        'sender_name': False,
        'sender_bank': False,
        'sender_account': False,
        'dest_name': False,
        'dest_bank': False,
        'dest_account': False,
        'admin_fee': False,
        'trx_type': False,
        'is_brimo': False,
    }


RECEIPT_HINTS = (
    'transaksi berhasil', 'transaksi gagal', 'bukti transfer',
    'transfer berhasil', 'brimo', 'no. ref', 'nomor ref', 'nomer ref',
    'total transaksi', 'sumber dana', 'rekening tujuan',
    'jenis transaksi', 'transfer', 'pembayaran',
)


def looks_like_payment_receipt(text, parsed=None):
    """True jika teks OCR cukup mirip struk transfer. Bukan klaim 100%."""
    blob = (text or '').strip()
    if not blob:
        return False
    parsed = parsed if parsed is not None else parse_ocr_text(blob)
    if parsed.get('is_brimo'):
        return True
    signals = 0
    if parsed.get('amount'):
        signals += 1
    if parsed.get('ref'):
        signals += 1
    if parsed.get('dest_account') or parsed.get('dest_name'):
        signals += 1
    if parsed.get('sender_name') or parsed.get('sender_bank'):
        signals += 1
    if parsed.get('status') in ('success', 'failed'):
        signals += 1
    compact = re.sub(r'\s+', ' ', blob.lower())
    hints = sum(1 for key in RECEIPT_HINTS if key in compact)
    if hints >= 2:
        signals += 1
    elif hints == 1 and parsed.get('amount'):
        signals += 1
    return signals >= 2


def parse_ocr_text(text):
    """Ambil field struk. Parser BRImo dulu, lalu fallback umum."""
    result = empty_parse_result()
    if not text or not str(text).strip():
        return result
    blob = str(text)
    brimo = parse_brimo_text(blob)
    generic = _parse_generic_text(blob)
    merged = _merge_parse_results(generic, brimo)
    if brimo.get('is_brimo') or _brimo_field_count(brimo) >= 3:
        merged['is_brimo'] = True
        merged['confidence'] = _brimo_confidence(merged)
    return merged


def parse_brimo_text(text):
    """Regex khusus layout BRImo (setelah teks OCR)."""
    result = empty_parse_result()
    if not text or not str(text).strip():
        return result
    blob = str(text)
    compact = _normalize_ocr_blob(blob)
    result['is_brimo'] = _looks_like_brimo(compact)

    if BRIMO_STATUS_OK_RE.search(compact):
        result['status'] = 'success'
    elif BRIMO_STATUS_FAIL_RE.search(compact):
        result['status'] = 'failed'

    result['date'] = parse_indonesian_date(compact) or _parse_date_slash(compact)

    total_match = BRIMO_TOTAL_RE.search(compact)
    if total_match:
        result['amount'] = parse_amount_id(total_match.group(1))
    if not result['amount']:
        rp_match = re.search(
            r'rp\.?\s*([0-9]{1,3}(?:[.\s][0-9]{3})+)',
            compact,
            re.IGNORECASE,
        )
        if rp_match:
            result['amount'] = parse_amount_id(rp_match.group(1))

    ref_match = BRIMO_REF_RE.search(compact)
    if ref_match:
        result['ref'] = ref_match.group(1)
    else:
        lone = re.search(r'\b(\d{10,16})\b', compact)
        if lone and not accounts_match(lone.group(1), result.get('dest_account')):
            # 12 digit khas No. Ref BRImo, bukan 15 digit rekening.
            if 10 <= len(lone.group(1)) <= 14:
                result['ref'] = lone.group(1)

    fee_match = BRIMO_ADMIN_FEE_RE.search(compact)
    if fee_match:
        raw_fee = re.sub(r'\D', '', fee_match.group(1) or '') or '0'
        try:
            result['admin_fee'] = float(raw_fee)
        except ValueError:
            result['admin_fee'] = 0.0

    type_match = BRIMO_TYPE_RE.search(compact)
    if type_match:
        result['trx_type'] = type_match.group(1).strip()[:64]

    sender = _parse_brimo_party(compact, start='sumber dana', end='tujuan')
    dest = _parse_brimo_party(compact, start='tujuan', end='jenis transaksi')
    if not dest.get('name') and not dest.get('account'):
        dest = _parse_brimo_party(compact, start='tujuan', end='catatan')
    if not dest.get('name'):
        dest_alt = _parse_labeled_party(compact, ('tujuan', 'penerima'))
        dest = _merge_party(dest, dest_alt)
    if not sender.get('name'):
        sender_alt = _parse_labeled_party(compact, ('sumber dana', 'pengirim'))
        sender = _merge_party(sender, sender_alt)

    result['sender_name'] = sender.get('name') or False
    result['sender_bank'] = sender.get('bank') or False
    result['sender_account'] = sender.get('account') or sender.get('masked') or False
    result['dest_name'] = dest.get('name') or False
    result['dest_bank'] = dest.get('bank') or False
    result['dest_account'] = dest.get('account') or False

    if result['dest_account']:
        result['dest_account'] = normalize_account_number(result['dest_account'])
    if result['sender_account'] and '*' not in str(result['sender_account']):
        result['sender_account'] = _mask_account(result['sender_account'])

    if _brimo_field_count(result) >= 3:
        result['is_brimo'] = True
    result['confidence'] = _brimo_confidence(result)
    return result


def extract_text_from_bytes(data, mimetype=None, filename=None):
    """
    Kembalikan (text, engine).
    engine: pytesseract / pypdf / pdf_strings / plain / odoo_ai / none
    """
    if not data:
        return '', 'none'
    mimetype = (mimetype or '').lower()
    filename = (filename or '').lower()

    if mimetype.startswith('text/') or filename.endswith(('.txt', '.csv')):
        try:
            return data.decode('utf-8', errors='replace'), 'plain'
        except Exception:
            return '', 'none'

    if mimetype == 'application/pdf' or filename.endswith('.pdf'):
        text, engine = _extract_pdf_text(data)
        if text.strip():
            return text, engine
        return '', engine or 'none'

    if mimetype.startswith('image/') or filename.endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp')):
        return _extract_image_text(data)

    try:
        decoded = data.decode('utf-8', errors='replace')
        if decoded and sum(ch.isprintable() for ch in decoded[:200]) > 80:
            return decoded, 'plain'
    except Exception:
        pass
    return '', 'none'


def _extract_pdf_text(data):
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        parts = [(page.extract_text() or '') for page in reader.pages]
        text = '\n'.join(parts).strip()
        if text:
            return text, 'pypdf'
    except Exception as exc:
        _logger.info('OCR PDF pypdf tidak dipakai: %s', exc)

    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(io.BytesIO(data))
        parts = [(page.extract_text() or '') for page in reader.pages]
        text = '\n'.join(parts).strip()
        if text:
            return text, 'pypdf'
    except Exception as exc:
        _logger.info('OCR PDF PyPDF2 tidak dipakai: %s', exc)

    try:
        raw = data.decode('latin-1', errors='ignore')
        strings = re.findall(r'\(([^)]{4,})\)', raw)
        text = ' '.join(strings).strip()
        if len(text) >= 8:
            return text, 'pdf_strings'
    except Exception:
        pass
    return '', 'none'


def _extract_image_text(data):
    try:
        import pytesseract
        from PIL import Image, ImageEnhance, ImageOps
    except Exception as exc:
        _logger.info('OCR gambar tidak tersedia di kontainer: %s', exc)
        return '', 'none'

    try:
        image = Image.open(io.BytesIO(data))
    except Exception as exc:
        _logger.info('OCR tidak bisa membuka gambar: %s', exc)
        return '', 'none'

    if image.mode not in ('RGB', 'L'):
        image = image.convert('RGB')
    width, height = image.size
    if width < 900:
        scale = max(2, int(900 / float(width or 1)))
        image = image.resize((width * scale, height * scale), Image.Resampling.LANCZOS)

    variants = [image]
    try:
        gray = ImageOps.grayscale(image)
        gray = ImageEnhance.Contrast(gray).enhance(2.0)
        gray = ImageEnhance.Sharpness(gray).enhance(1.4)
        variants.append(gray)
    except Exception:
        pass

    configs = [
        ('ind+eng', '--psm 6'),
        ('ind+eng', '--psm 4'),
        ('eng', '--psm 6'),
    ]
    best_text = ''
    best_score = -1
    used_engine = 'none'
    for variant in variants:
        for lang, cfg in configs:
            try:
                text = pytesseract.image_to_string(variant, lang=lang, config=cfg) or ''
            except Exception:
                try:
                    text = pytesseract.image_to_string(variant, lang='eng', config=cfg) or ''
                except Exception as exc:
                    _logger.info('OCR tesseract gagal (%s %s): %s', lang, cfg, exc)
                    continue
            text = text.strip()
            if not text:
                continue
            parsed = parse_ocr_text(text)
            score = _brimo_field_count(parsed)
            if parsed.get('amount'):
                score += 1
            if score > best_score or (score == best_score and len(text) > len(best_text)):
                best_text = text
                best_score = score
                used_engine = 'pytesseract'
            if score >= 5:
                return best_text, used_engine
    if best_text:
        return best_text, used_engine
    return '', 'pytesseract'


def parse_indonesian_date(text):
    """14 Agustus 2026 → date."""
    if not text:
        return False
    match = DATE_ID_RE.search(str(text))
    if not match:
        return False
    day = int(match.group(1))
    month = ID_MONTHS.get(match.group(2).lower())
    year = int(match.group(3))
    if not month:
        return False
    try:
        return date(year, month, day)
    except ValueError:
        return False


def _parse_date_slash(text):
    match = DATE_RE.search(text or '')
    if not match:
        return False
    return _parse_date(match.group(1))


def _parse_date(raw):
    raw = raw.replace('.', '/').replace('-', '/')
    for fmt in ('%d/%m/%Y', '%d/%m/%y', '%m/%d/%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return False


def _parse_generic_text(blob):
    result = empty_parse_result()
    amounts = []
    for match in AMOUNT_RE.finditer(blob):
        value = parse_amount_id(match.group(1))
        if value:
            amounts.append(value)
    if amounts:
        result['amount'] = max(amounts)
        result['confidence'] = 0.55
        lower = blob.lower()
        if 'rp' in lower or 'transfer' in lower or 'jumlah' in lower:
            result['confidence'] = 0.65
    result['date'] = parse_indonesian_date(blob) or _parse_date_slash(blob)
    ref_match = BRIMO_REF_RE.search(blob) or REF_RE.search(blob)
    if ref_match:
        result['ref'] = ref_match.group(1)[:64]
    if BRIMO_STATUS_OK_RE.search(blob):
        result['status'] = 'success'
    dest = _parse_labeled_party(blob, ('tujuan', 'penerima'))
    sender = _parse_labeled_party(blob, ('sumber dana', 'pengirim'))
    result['dest_name'] = dest.get('name') or False
    result['dest_bank'] = dest.get('bank') or False
    result['dest_account'] = normalize_account_number(dest.get('account') or '') or False
    result['sender_name'] = sender.get('name') or False
    result['sender_bank'] = sender.get('bank') or False
    result['sender_account'] = sender.get('account') or sender.get('masked') or False
    return result


def _merge_parse_results(generic, brimo):
    merged = empty_parse_result()
    for key in merged:
        merged[key] = brimo.get(key) or generic.get(key) or False
    if brimo.get('confidence') or generic.get('confidence'):
        merged['confidence'] = max(brimo.get('confidence') or 0.0, generic.get('confidence') or 0.0)
    merged['is_brimo'] = bool(brimo.get('is_brimo'))
    return merged


def _looks_like_brimo(text):
    lower = (text or '').lower()
    markers = 0
    for token in ('transaksi berhasil', 'no. ref', 'no ref', 'sumber dana', 'tujuan', 'brimo', 'bank bri'):
        if token in lower:
            markers += 1
    return markers >= 2


def _brimo_field_count(parsed):
    keys = ('amount', 'ref', 'dest_name', 'dest_account', 'date', 'status', 'sender_name')
    return sum(1 for key in keys if parsed.get(key))


def _brimo_confidence(parsed):
    score = 0.50
    if parsed.get('status') == 'success':
        score += 0.08
    if parsed.get('amount'):
        score += 0.10
    if parsed.get('ref'):
        score += 0.10
    if parsed.get('dest_name'):
        score += 0.08
    if parsed.get('dest_account'):
        score += 0.08
    if parsed.get('date'):
        score += 0.04
    if parsed.get('sender_name'):
        score += 0.04
    return min(0.92, score)


def _normalize_ocr_blob(text):
    blob = str(text).replace('\r', '\n')
    blob = blob.replace('—', '-').replace('–', '-')
    blob = re.sub(r'[ \t]+', ' ', blob)
    blob = re.sub(r'\n{2,}', '\n', blob)
    return blob


def _section_text(text, start, end=None):
    pattern = re.compile(
        r'%s\s*:?\s*(.+?)(?:%s|$)' % (
            re.escape(start),
            re.escape(end) if end else r'(?!)',
        ),
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else ''


def _parse_brimo_party(text, start, end):
    block = _section_text(text, start, end)
    if not block:
        return {}
    return _extract_party_fields(block)


def _parse_labeled_party(text, labels):
    for label in labels:
        block = _section_text(text, label, None)
        if block:
            parsed = _extract_party_fields(block.split('\n')[0] + '\n' + block)
            if parsed.get('name') or parsed.get('account'):
                return parsed
    return {}


def _extract_party_fields(block):
    result = {'name': False, 'bank': False, 'account': False, 'masked': False}
    if not block:
        return result
    clean = re.sub(r'\b[A-Z]{1,2}\b\n', '\n', block)
    bank_match = BANK_RE.search(block)
    if bank_match:
        result['bank'] = bank_match.group(1).upper().replace('  ', ' ')
        if result['bank'] == 'BRI':
            result['bank'] = 'BANK BRI'
    masked = MASKED_ACCOUNT_RE.search(block)
    if masked:
        result['masked'] = re.sub(r'\s+', ' ', masked.group(1)).strip()
    spaced = ACCOUNT_SPACED_RE.search(block)
    if spaced:
        result['account'] = normalize_account_number(spaced.group(1))
    if not result['account']:
        for digits in ACCOUNT_DIGITS_RE.findall(block):
            if 13 <= len(digits) <= 16:
                result['account'] = digits
                break
    name = _first_person_name(clean)
    if name:
        result['name'] = _strip_bank_suffix(name)
    return result


def _strip_bank_suffix(name):
    cleaned = re.sub(r'\s+BANK\s*[A-Z]{2,12}$', '', name or '', flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+\b(BANKBRI|BANKBCA|BRI|BCA|BNI|MANDIRI|BTN|BSI|CIMB)\b$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+\b[A-Z]{1,2}\b$', '', cleaned)
    return cleaned.strip()


def _first_person_name(text):
    skip = {
        'BANK BRI', 'BANK BCA', 'BANK BNI', 'BANK MANDIRI', 'SUMBER DANA',
        'JENIS TRANSAKSI', 'TRANSFER BANK', 'TOTAL TRANSAKSI', 'BIAYA ADMIN',
        'INFORMASI', 'KANTOR PUSAT',
    }
    for match in PERSON_NAME_RE.finditer(text.upper()):
        name = re.sub(r'\s+', ' ', match.group(1)).strip()
        if name in skip or name.startswith('BANK '):
            continue
        if len(name.split()) < 2:
            continue
        return name
    return False


def _merge_party(primary, secondary):
    merged = dict(primary or {})
    for key, value in (secondary or {}).items():
        if not merged.get(key) and value:
            merged[key] = value
    return merged


def _mask_account(raw):
    digits = normalize_account_number(raw)
    if len(digits) < 8:
        return raw
    return '%s **** **** %s' % (digits[:4], digits[-3:])
