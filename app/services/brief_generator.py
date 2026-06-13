from datetime import datetime


def generate_source_readiness_brief(plan, generated_at=None):
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "# Maritime Intelligence Hub - Source Readiness Brief",
        "",
        f"Generated at: {generated_at}",
        f"Priority scope: {plan['priority']}",
        "",
        "## Collection Readiness",
        "",
        f"- RSS-ready sources: {len(plan['rss'])}",
        f"- Partial RSS sources: {len(plan['partial_rss'])}",
        f"- HTML crawler sources: {len(plan['html'])}",
        f"- Manual sources: {len(plan['manual'])}",
        "",
        "## Recommended First Run",
        "",
    ]

    if plan["rss"]:
        lines.append("Start with RSS-ready P1 sources:")
        for source in plan["rss"]:
            lines.append(
                f"- {source.get('Source Name')} ({source.get('Category')}, "
                f"{source.get('Audience')})"
            )
    else:
        lines.append("No RSS-ready sources found for this priority.")

    lines.extend(
        [
            "",
            "## Next Engineering Step",
            "",
            "Implement RSS collection only after source master validation passes.",
            "Do not call AI until fetched articles are deduplicated and scored.",
        ]
    )

    return "\n".join(lines)
