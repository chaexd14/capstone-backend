from pathlib import Path
from zipfile import BadZipFile, ZipFile

from django.core.exceptions import ValidationError

CONTENT_TYPES = {
    '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.txt': 'text/plain',
}
MAX_DOCX_ENTRIES = 512
MAX_DOCX_UNCOMPRESSED = 32 * 1024 * 1024


def validate_resume_format(file_obj, filename):
    extension = Path(filename).suffix.lower()
    if extension not in CONTENT_TYPES:
        raise ValidationError("Only .pdf, .docx, and .txt files are accepted.")

    try:
        file_obj.seek(0)
        if extension == '.pdf':
            if not file_obj.read(8).startswith(b'%PDF-'):
                raise ValidationError("The file does not have a valid PDF header.")
        elif extension == '.docx':
            if file_obj.read(4) != b'PK\x03\x04':
                raise ValidationError("The file is not a supported Word document.")
            file_obj.seek(0)
            with ZipFile(file_obj) as archive:
                entries = archive.infolist()
                if (len(entries) > MAX_DOCX_ENTRIES
                        or sum(entry.file_size for entry in entries) > MAX_DOCX_UNCOMPRESSED
                        or any(entry.flag_bits & 1 for entry in entries)):
                    raise ValidationError("The Word document is too complex or encrypted.")
                names = set(archive.namelist())
                if not {'[Content_Types].xml', 'word/document.xml'}.issubset(names):
                    raise ValidationError("The file is not a valid Word document.")
                if any(name.lower().endswith('vbaproject.bin') for name in names):
                    raise ValidationError("Macro-enabled Word documents are blocked.")
        else:
            text = file_obj.read().decode('utf-8-sig')
            if any(ord(char) < 32 and char not in '\t\r\n\f' for char in text):
                raise ValidationError("Upload a plain UTF-8 text file.")
    except (BadZipFile, UnicodeDecodeError) as exc:
        raise ValidationError("The file content does not match its extension.") from exc
    finally:
        file_obj.seek(0)
    return CONTENT_TYPES[extension]
