import os
from django.core.management.base import BaseCommand
from django.conf import settings
from django.core.files.base import ContentFile
from apps.resumes.models import Resume

class Command(BaseCommand):
    help = "Migrates existing local resume files from media/ to Cloudflare R2 storage bucket"

    def add_arguments(self, parser):
        parser.add_argument(
            '--include-orphans',
            action='store_true',
            help='Also upload orphaned files in media/resumes/ that are not linked to DB records.',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starting migration of local resumes to Cloudflare R2..."))
        include_orphans = options.get('include_orphans', False)
        resumes = Resume.objects.select_related('application').all()
        total = resumes.count()
        self.stdout.write(f"Total Resume records found in DB: {total}")

        migrated = 0
        skipped = 0
        failed = 0

        for resume in resumes:
            if not resume.file:
                self.stdout.write(f"Resume ID {resume.id} has no file (purged or empty). Skipping.")
                skipped += 1
                continue

            raw_file_name = resume.file.name.replace('\\', '/')

            # Potential local paths
            candidate_paths = [
                os.path.join(settings.MEDIA_ROOT, raw_file_name),
                os.path.join(settings.MEDIA_ROOT, raw_file_name.replace('/', os.sep)),
                os.path.join(settings.MEDIA_ROOT, 'resumes', os.path.basename(raw_file_name)),
            ]
            if hasattr(resume, 'application') and resume.application:
                token = str(getattr(resume.application, 'tracking_token', resume.application.id))
                candidate_paths.append(os.path.join(settings.MEDIA_ROOT, 'resumes', token, resume.original_filename))
                candidate_paths.append(os.path.join(settings.MEDIA_ROOT, 'resume', token, resume.original_filename))

            local_path = None
            for p in candidate_paths:
                if os.path.exists(p) and os.path.isfile(p):
                    local_path = p
                    break

            if not local_path:
                # Check if it's already in R2 storage
                try:
                    if resume.file.storage.exists(raw_file_name):
                        self.stdout.write(f"Resume ID {resume.id} ({raw_file_name}) already exists in remote storage. Skipping.")
                        skipped += 1
                        continue
                except Exception:
                    pass

                self.stdout.write(self.style.WARNING(f"Local file not found for Resume ID {resume.id} ({raw_file_name})."))
                failed += 1
                continue

            try:
                token = "legacy"
                if hasattr(resume, 'application') and resume.application:
                    token = str(getattr(resume.application, 'tracking_token', resume.application.id))
                filename = resume.original_filename or os.path.basename(local_path)
                target_key = f"resume/{token}/{filename}"

                self.stdout.write(f"Uploading {local_path} -> Cloudflare R2 key: {target_key}...")
                with open(local_path, 'rb') as f:
                    file_content = f.read()

                # Save file to configured storage
                storage = resume.file.storage
                if storage.exists(target_key):
                    storage.delete(target_key)
                
                saved_name = storage.save(target_key, ContentFile(file_content))
                resume.file.name = saved_name
                resume.save(update_fields=['file'])

                migrated += 1
                self.stdout.write(self.style.SUCCESS(f"Successfully migrated Resume ID {resume.id} to R2: {saved_name}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed to migrate Resume ID {resume.id}: {e}"))
                failed += 1

        if include_orphans:
            from django.core.files.storage import default_storage
            self.stdout.write(self.style.NOTICE("\nScanning media/resumes/ for unlinked local files..."))
            media_resumes_dir = os.path.join(settings.MEDIA_ROOT, 'resumes')
            orphan_count = 0
            if os.path.exists(media_resumes_dir):
                for root, _, files in os.walk(media_resumes_dir):
                    for fname in files:
                        full_fpath = os.path.join(root, fname)
                        rel_path = os.path.relpath(full_fpath, media_resumes_dir).replace('\\', '/')
                        r2_target = f"resume/migrated/{rel_path}"
                        try:
                            if not default_storage.exists(r2_target):
                                with open(full_fpath, 'rb') as f:
                                    default_storage.save(r2_target, ContentFile(f.read()))
                                orphan_count += 1
                                self.stdout.write(f"Uploaded orphan: {rel_path} -> {r2_target}")
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"Error uploading orphan {rel_path}: {e}"))
            self.stdout.write(self.style.SUCCESS(f"Orphan migration completed: {orphan_count} files uploaded."))

        self.stdout.write(self.style.SUCCESS(
            f"\nMigration Complete! Migrated: {migrated}, Skipped: {skipped}, Failed: {failed}"
        ))
