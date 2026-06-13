from app.services.pipeline import validate_sources


def run_business_task():
    result, _ = validate_sources()
    if result.ok:
        return f"Source master OK: {result.row_count} sources"

    return f"Source master validation failed: {len(result.errors)} errors"
