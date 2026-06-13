import csv
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


REQUIRED_COLUMNS = [
    "ID",
    "Source Name",
    "Website",
    "Country",
    "Language",
    "Type",
    "Category",
    "Priority",
    "RSS",
    "API",
    "Crawl Method",
    "Frequency",
    "Audience",
    "Content Quality Score",
    "Business Value Score",
    "Crawl Difficulty",
    "Copyright Risk",
    "AI Summary Enabled",
    "Status",
]

ALLOWED_VALUES = {
    "Priority": {"P1", "P2", "P3"},
    "RSS": {"Yes", "No", "Partial", "Unknown"},
    "API": {"Yes", "No", "Partial", "Unknown"},
    "Frequency": {"Hourly", "Daily", "Weekly", "Manual"},
    "Crawl Difficulty": {"Easy", "Medium", "Hard"},
    "Copyright Risk": {"Low", "Medium", "High"},
    "AI Summary Enabled": {"Yes", "No"},
    "Status": {"Active", "Future", "Disabled"},
}

SCORE_COLUMNS = ["Content Quality Score", "Business Value Score"]


@dataclass
class SourceValidationResult:
    path: Path
    row_count: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    priority_counts: dict[str, int] = field(default_factory=dict)
    rss_counts: dict[str, int] = field(default_factory=dict)
    status_counts: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self):
        return not self.errors


def load_sources(path):
    source_path = Path(path)
    with source_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)

    return rows, reader.fieldnames or []


def append_manual_source(path, values):
    source_path = Path(path)
    rows, columns = load_sources(source_path)
    fieldnames = columns or REQUIRED_COLUMNS
    row = build_manual_source_row(rows, values)
    errors = []
    candidate_rows = rows + [row]
    _validate_required_cells(candidate_rows, errors)
    _validate_uniques(candidate_rows, errors)
    _validate_allowed_values(candidate_rows, errors)
    _validate_scores(candidate_rows, errors)
    _validate_urls(candidate_rows, errors)
    if errors:
        raise ValueError("\n".join(errors))
    with source_path.open("a", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        if source_path.stat().st_size == 0:
            writer.writeheader()
        writer.writerow(row)
    return row


def build_manual_source_row(existing_rows, values):
    data = {key: str(values.get(key, "")).strip() for key in REQUIRED_COLUMNS}
    data["ID"] = data["ID"] or _next_source_id(existing_rows)
    data["Country"] = data["Country"] or "Global"
    data["Language"] = data["Language"] or "EN"
    data["Type"] = data["Type"] or "Media"
    data["Category"] = data["Category"] or "Shipping News"
    data["Priority"] = data["Priority"] or "P2"
    data["RSS"] = data["RSS"] or "Unknown"
    data["API"] = data["API"] or "No"
    data["Crawl Method"] = data["Crawl Method"] or "HTML"
    data["Frequency"] = data["Frequency"] or "Daily"
    data["Audience"] = data["Audience"] or "All"
    data["Content Quality Score"] = data["Content Quality Score"] or "6"
    data["Business Value Score"] = data["Business Value Score"] or "6"
    data["Crawl Difficulty"] = data["Crawl Difficulty"] or "Medium"
    data["Copyright Risk"] = data["Copyright Risk"] or "Medium"
    data["AI Summary Enabled"] = data["AI Summary Enabled"] or "Yes"
    data["Status"] = data["Status"] or "Active"
    return data


def validate_source_master(path):
    source_path = Path(path)
    errors = []
    warnings = []

    if not source_path.exists():
        return SourceValidationResult(
            path=source_path,
            row_count=0,
            errors=[f"Source master file not found: {source_path}"],
        )

    rows, columns = load_sources(source_path)
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in columns]
    extra_columns = [column for column in columns if column not in REQUIRED_COLUMNS]

    if missing_columns:
        errors.append(f"Missing required columns: {', '.join(missing_columns)}")

    if extra_columns:
        warnings.append(f"Extra columns present: {', '.join(extra_columns)}")

    _validate_required_cells(rows, errors)
    _validate_uniques(rows, errors)
    _validate_allowed_values(rows, errors)
    _validate_scores(rows, errors)
    _validate_urls(rows, errors)
    _validate_source_rules(rows, warnings)

    return SourceValidationResult(
        path=source_path,
        row_count=len(rows),
        errors=errors,
        warnings=warnings,
        priority_counts=_count_values(rows, "Priority"),
        rss_counts=_count_values(rows, "RSS"),
        status_counts=_count_values(rows, "Status"),
    )


def get_active_sources(path, priority=None):
    rows, _ = load_sources(path)
    active_sources = [row for row in rows if row.get("Status") == "Active"]
    if priority:
        active_sources = [row for row in active_sources if row.get("Priority") == priority]

    return active_sources


def get_fetch_plan(path, priority="P1"):
    sources = get_active_sources(path, priority=priority)
    rss_sources = []
    partial_rss_sources = []
    html_sources = []
    manual_sources = []

    for source in sources:
        rss = source.get("RSS", "")
        crawl_method = source.get("Crawl Method", "")

        if rss == "Yes":
            rss_sources.append(source)
        elif rss == "Partial":
            partial_rss_sources.append(source)
        elif crawl_method == "Manual":
            manual_sources.append(source)
        else:
            html_sources.append(source)

    return {
        "priority": priority,
        "rss": rss_sources,
        "partial_rss": partial_rss_sources,
        "html": html_sources,
        "manual": manual_sources,
    }


def format_validation_report(result):
    lines = [
        f"Source master: {result.path}",
        f"Rows: {result.row_count}",
        f"Status: {'OK' if result.ok else 'FAILED'}",
        "",
        "Priority counts:",
        *_format_counts(result.priority_counts),
        "",
        "RSS counts:",
        *_format_counts(result.rss_counts),
        "",
        "Status counts:",
        *_format_counts(result.status_counts),
    ]

    if result.warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in result.warnings)

    if result.errors:
        lines.extend(["", "Errors:"])
        lines.extend(f"- {error}" for error in result.errors)

    return "\n".join(lines)


def format_fetch_plan(plan):
    lines = [f"Fetch plan for {plan['priority']} active sources"]

    groups = [
        ("RSS", plan["rss"]),
        ("Partial RSS", plan["partial_rss"]),
        ("HTML", plan["html"]),
        ("Manual", plan["manual"]),
    ]

    for group_name, sources in groups:
        lines.append("")
        lines.append(f"{group_name}: {len(sources)}")
        for source in sources:
            lines.append(
                f"- {source.get('ID')} | {source.get('Source Name')} | "
                f"{source.get('Category')} | {source.get('Frequency')}"
            )

    return "\n".join(lines)


def _validate_required_cells(rows, errors):
    for row_number, row in enumerate(rows, start=2):
        for column in REQUIRED_COLUMNS:
            if not str(row.get(column, "")).strip():
                errors.append(f"Row {row_number}: missing value for {column}")


def _validate_uniques(rows, errors):
    for column in ["ID", "Source Name", "Website"]:
        seen = {}
        for row_number, row in enumerate(rows, start=2):
            value = str(row.get(column, "")).strip().lower()
            if not value:
                continue
            if value in seen:
                errors.append(
                    f"Row {row_number}: duplicate {column} with row {seen[value]}"
                )
            else:
                seen[value] = row_number


def _validate_allowed_values(rows, errors):
    for row_number, row in enumerate(rows, start=2):
        for column, allowed in ALLOWED_VALUES.items():
            value = str(row.get(column, "")).strip()
            if value and value not in allowed:
                errors.append(
                    f"Row {row_number}: invalid {column} '{value}', expected one of "
                    f"{', '.join(sorted(allowed))}"
                )


def _validate_scores(rows, errors):
    for row_number, row in enumerate(rows, start=2):
        for column in SCORE_COLUMNS:
            value = str(row.get(column, "")).strip()
            try:
                score = int(value)
            except ValueError:
                errors.append(f"Row {row_number}: {column} must be an integer")
                continue

            if score < 1 or score > 10:
                errors.append(f"Row {row_number}: {column} must be from 1 to 10")


def _validate_urls(rows, errors):
    for row_number, row in enumerate(rows, start=2):
        website = str(row.get("Website", "")).strip()
        parsed = urlparse(website)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(f"Row {row_number}: invalid Website URL '{website}'")


def _validate_source_rules(rows, warnings):
    for row_number, row in enumerate(rows, start=2):
        if row.get("Status") != "Active":
            continue

        if row.get("RSS") == "Yes" and row.get("Crawl Method") not in {"RSS", "RSS+Scrapy"}:
            warnings.append(
                f"Row {row_number}: RSS is Yes but Crawl Method is {row.get('Crawl Method')}"
            )

        if row.get("Copyright Risk") in {"Medium", "High"}:
            warnings.append(
                f"Row {row_number}: {row.get('Source Name')} has "
                f"{row.get('Copyright Risk')} copyright risk; keep metadata/link-first"
            )


def _count_values(rows, column):
    counts = {}
    for row in rows:
        value = row.get(column, "")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _format_counts(counts):
    if not counts:
        return ["- none"]
    return [f"- {key}: {value}" for key, value in counts.items()]


def _next_source_id(rows):
    numbers = []
    for row in rows:
        value = str(row.get("ID", "")).strip().upper()
        if value.startswith("SRC") and value[3:].isdigit():
            numbers.append(int(value[3:]))
    return f"SRC{(max(numbers) if numbers else 0) + 1:03d}"
