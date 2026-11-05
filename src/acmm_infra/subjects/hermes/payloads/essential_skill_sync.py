from tools.skills_sync import sync_skills

result = sync_skills(quiet=True)
copied = len(result.get("copied", []))
updated = len(result.get("updated", []))
print(
    f"Essential bundled skills synchronized "
    f"(copied={copied}, updated={updated})."
)
