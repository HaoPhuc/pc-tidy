"""disk_report.py and scanner.py as they behave today, on a fake profile."""

from pypdf import PdfReader

import scanner
from support import Runner, make_fake_profile

SECTIONS = ["DISK REPORT", "DRIVES", "WHERE THE SPACE GOES", "THINGS YOU CAN CLEAN UP",
            "BIGGEST FOLDERS ON", "BIGGEST FILES", "GOOD TO KNOW"]


def test_text_report_has_every_section(tmp_path):
    home = make_fake_profile(tmp_path / "big", big_cache=True)
    result = Runner(home, tmp_path, [tmp_path]).python(
        "disk_report.py", "--path", home, "--out", "report.txt", "--no-llm", "--no-open")

    assert result.returncode == 0, result.output
    text = (tmp_path / "report.txt").read_text(encoding="utf-8")
    for heading in SECTIONS:
        assert heading in text, heading
    assert "pip download cache" in text           # the 110 MB file in AppData\Local\pip\cache
    assert "Text report saved to" in result.stdout


def test_pdf_report_is_written(run, profile, tmp_path):
    result = run.python("disk_report.py", "--path", profile, "--out", "report.pdf", "--no-llm", "--no-open")

    assert result.returncode == 0, result.output
    pdf = tmp_path / "report.pdf"
    assert pdf.read_bytes()[:5] == b"%PDF-"
    reader = PdfReader(str(pdf))
    assert len(reader.pages) >= 1
    assert "Disk report" in reader.pages[0].extract_text()


def test_scan_skips_junctions_and_counts_each_file_once(profile):
    scan = scanner.Scan([str(profile)])
    temp = str(profile / "AppData" / "Local" / "Temp")
    precious = str(profile / "Precious")

    assert scan.sizes[temp] == 100                 # tmp1.tmp only, not the junction's target
    assert scan.sizes[precious] == 1000
    assert str(profile / "AppData" / "Local" / "Temp" / "link-to-precious") not in scan.sizes
