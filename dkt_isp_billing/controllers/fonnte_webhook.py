import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class IspFonnteWebhookController(http.Controller):
    """Webhook inbound Fonnte (POST JSON). Token API tidak pernah dikembalikan."""

    def _payload(self):
        raw = request.httprequest.get_data(as_text=True) or ''
        ctype = (request.httprequest.content_type or '')
        payload = {}
        if 'json' in ctype or raw.lstrip().startswith('{'):
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {}
        if not payload:
            form = request.httprequest.form
            payload = dict(form) if form else dict(request.params or {})
        return payload or {}

    def _ok(self, data=None):
        body = {'status': True}
        if data:
            body.update(data)
        return request.make_json_response(body)

    @http.route(
        '/isp/whatsapp/fonnte',
        type='http',
        auth='public',
        methods=['GET'],
        csrf=False,
    )
    def fonnte_webhook_ping(self, **kwargs):
        return self._ok({'service': 'dkt_isp_billing', 'webhook': 'fonnte'})

    @http.route(
        '/isp/whatsapp/fonnte',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
    )
    def fonnte_webhook(self, **kwargs):
        payload = self._payload()
        _logger.info(
            'WA webhook masuk keys=%s sender=%s member=%s msg=%s',
            ','.join(sorted(str(k) for k in payload.keys())),
            str(payload.get('sender') or '')[:80],
            str(payload.get('member') or '-')[:40],
            str(payload.get('message') or payload.get('text') or '')[:60],
        )
        try:
            result = request.env['isp.whatsapp.message'].sudo().process_fonnte_inbound(payload)
        except Exception:
            _logger.exception('WA inbound webhook gagal diproses')
            return self._ok({'accepted': False})
        safe = {
            'accepted': bool(result.get('ok')),
            'ignored': result.get('ignored') or False,
        }
        if result.get('error') == 'secret':
            return request.make_json_response({'status': False}, status=403)
        return self._ok(safe)
