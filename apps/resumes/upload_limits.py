from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from rest_framework.exceptions import APIException


class ResumePayloadTooLarge(APIException):
    status_code = 413
    default_code = 'resume_payload_too_large'
    default_detail = 'The application upload exceeds the maximum allowed size.'


class ResumeUploadLimit(FileUploadHandler):
    """Abort oversized uploads before Django stores the rest of the file."""

    def __init__(self, request):
        super().__init__(request)
        self.bytes_received = 0
        self.exceeded = False

    def receive_data_chunk(self, raw_data, start):
        self.bytes_received += len(raw_data)
        if self.bytes_received > settings.MAX_RESUME_UPLOAD_SIZE:
            self.exceeded = True
            raise StopUpload(connection_reset=True)
        return raw_data

    def file_complete(self, file_size):
        return None
