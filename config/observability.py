import contextvars
import json
import logging
from datetime import datetime, timezone


request_id_context = contextvars.ContextVar('request_id', default='-')


class RequestContextFilter(logging.Filter):
    def filter(self, record):
        request = getattr(record, 'request', None)
        record.request_id = getattr(request, 'request_id', None) or request_id_context.get()
        return True


class JsonFormatter(logging.Formatter):
    """Emit one machine-readable JSON object per log record."""

    def format(self, record):
        payload = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'request_id': getattr(record, 'request_id', '-'),
        }
        event = getattr(record, 'event', None)
        if isinstance(event, dict):
            payload['event'] = event
        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)
