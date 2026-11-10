"""Entry point for the isolated resume document parser."""

import io
import sys

MAX_INPUT = 10 * 1024 * 1024
MAX_TEXT = 100_000
MAX_PAGES = 100


def parse_document(extension, content):
    if extension == '.pdf':
        import pymupdf

        with pymupdf.open(stream=content, filetype='pdf') as document:
            if document.page_count > MAX_PAGES or document.needs_pass:
                raise ValueError('PDF is encrypted or exceeds the page limit')
            return '\n'.join(page.get_text()[:MAX_TEXT] for page in document)[:MAX_TEXT]

    if extension == '.docx':
        import docx

        document = docx.Document(io.BytesIO(content))
        chunks = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                chunks.extend(cell.text for cell in row.cells)
        return '\n'.join(chunks)[:MAX_TEXT]

    raise ValueError('Unsupported document format')


if __name__ == '__main__':
    if sys.platform.startswith('linux'):
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    payload = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(payload) > MAX_INPUT:
        sys.exit(1)
    sys.stdout.buffer.write(parse_document(sys.argv[1], payload).encode('utf-8'))
