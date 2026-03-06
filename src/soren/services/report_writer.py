"""CycleReport JSON writer."""

from pathlib import Path

from soren.models.session import CycleReport


def write_report(report: CycleReport, output_dir: Path) -> Path:
    """Serialize CycleReport to JSON, write to output_dir/{cycle_id}.json.

    Creates latest.json symlink to most recent report.
    Returns path to written file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    path = output_dir / f"{report.cycle_id}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    latest_link = output_dir / "latest.json"
    if latest_link.exists():
        latest_link.unlink()
    latest_link.symlink_to(path.name)

    return path
