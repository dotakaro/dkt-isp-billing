"""Uji OCR sampel BRImo di kontainer, tanpa odoo-bin host."""

import importlib.util
import os
import sys

OCR_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'models', 'isp_ocr.py')
SAMPLE = os.path.join(os.path.dirname(__file__), 'samples', 'brimo_sample.png')


def main():
    spec = importlib.util.spec_from_file_location('isp_ocr', OCR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with open(SAMPLE, 'rb') as handle:
        data = handle.read()
    text, engine = module.extract_text_from_bytes(data, 'image/png', 'brimo_sample.png')
    parsed = module.parse_ocr_text(text)
    print('engine=%s' % engine)
    print('amount=%s' % parsed.get('amount'))
    print('ref=%s' % parsed.get('ref'))
    print('dest_name=%s' % parsed.get('dest_name'))
    print('date=%s' % parsed.get('date'))
    print('status=%s' % parsed.get('status'))
    print('dest_account=%s' % parsed.get('dest_account'))
    print('confidence=%s' % parsed.get('confidence'))
    print('--- teks OCR (potong) ---')
    print((text or '')[:1200])
    ok = (
        parsed.get('amount') == 200000.0
        and parsed.get('ref') == '194723845619'
        and parsed.get('dest_name') and 'WASPADA' in parsed['dest_name']
        and parsed.get('date') and str(parsed['date']) == '2026-08-14'
    )
    if not ok:
        raise SystemExit('OCR sampel BRImo belum lengkap')
    print('OCR sampel BRImo OK')


if __name__ == '__main__':
    main()
