# Rollback script to restore pre-OmniRoute configuration
import shutil
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
BACKUP_DIR = BACKEND_DIR / "backups" / "pre_omniroute"

FILES_TO_RESTORE = [
    ("gemini_service.py.bak", BACKEND_DIR / "apps" / "resumes" / "gemini_service.py"),
    ("semantic_matcher.py.bak", BACKEND_DIR / "apps" / "resumes" / "semantic_matcher.py"),
    (".env.bak", BACKEND_DIR / ".env"),
    (".env.example.bak", BACKEND_DIR / ".env.example"),
]

def rollback():
    print("=" * 60)
    print("RESTORING TALENTMATCH TO PRE-OMNIROUTE SETUP")
    print("=" * 60)

    if not BACKUP_DIR.exists():
        print(f"[ERROR] Backup directory not found: {BACKUP_DIR}")
        sys.exit(1)

    restored_count = 0
    for backup_name, target_path in FILES_TO_RESTORE:
        src = BACKUP_DIR / backup_name
        if src.exists():
            shutil.copy2(src, target_path)
            print(f"[RESTORED] {src.name} -> {target_path.relative_to(BACKEND_DIR)}")
            restored_count += 1
        else:
            print(f"[WARNING] Backup file missing: {src.name}")

    print("-" * 60)
    print(f"Rollback complete! Successfully restored {restored_count} files.")
    print("TalentMatch is back to the direct Gemini API setup.")
    print("=" * 60)

if __name__ == "__main__":
    rollback()
