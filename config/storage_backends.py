import os
from storages.backends.s3boto3 import S3Boto3Storage

class CloudflareR2ResumeStorage(S3Boto3Storage):
    """
    Cloudflare R2 Object Storage for user resume uploads.
    Cloudflare R2 provides S3-compatible API with zero egress fees.
    Resumes are isolated under the 'resume/' directory prefix with
    temporary pre-signed URLs generated for anytime downloads.
    """
    bucket_name = os.getenv('R2_BUCKET_NAME', 'capstone-talentmatch')
    endpoint_url = os.getenv('R2_ENDPOINT_URL', 'https://a02d5e95285f8869b569479eafaabd2e.r2.cloudflarestorage.com')
    access_key = os.getenv('R2_ACCESS_KEY_ID')
    secret_key = os.getenv('R2_SECRET_ACCESS_KEY')
    region_name = os.getenv('R2_REGION_NAME', 'auto')

    location = ''
    file_overwrite = False
    default_acl = None  # Cloudflare R2 does not use S3 ACLs
    signature_version = 's3v4'
    querystring_auth = True
    querystring_expire = 3600  # Generated signed URLs are valid for 1 hour, auto-refreshed by API
    object_parameters = {
        'CacheControl': 'max-age=86400',
    }
