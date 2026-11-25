from math import ceil

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardResultsSetPagination(PageNumberPagination):
    """Stable, bounded pagination contract shared by collection endpoints."""

    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_paginated_response(self, data, *, facets=None, extra=None):
        count = self.page.paginator.count
        payload = {
            'count': count,
            'page': self.page.number,
            'page_size': self.get_page_size(self.request),
            'total_pages': ceil(count / self.get_page_size(self.request)) if count else 0,
            'next': self.get_next_link(),
            'previous': self.get_previous_link(),
            'results': data,
        }
        if facets is not None:
            payload['facets'] = facets
        if extra:
            payload.update(extra)
        return Response(payload)
