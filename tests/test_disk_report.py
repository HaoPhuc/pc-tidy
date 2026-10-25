"""disk_report.py and scanner.py as they behave today, on a fake profile."""

from pypdf import PdfReader

import scanner
from support import Runner, make_fake_profile

SECTIONS = ["DISK REPORT", "FIXES FOR C:, LOWEST RISK FIRST", "WHAT'S LEFT IN YOUR FOLDERS", "DRIVES",
            "WHERE THE SPACE GOES",
            "THINGS YOU CAN CLEAN UP", "BIGGEST FOLDERS ON", "BIGGEST FILES", "GOOD TO KNOW"]


def test_text_report_has_every_section(tmp_path):
    home = make_fake_profile(tmp_path / "big", big_cache=True)
    result = Runner(home, tmp_path, [tmp_path]).python(
        "disk_report.py", "--path", home, "--out", "report.txt", "--no-llm", "--no-open")

    assert result.returncode == 0, result.output
    text = (tmp_path / "report.txt").read_text(encoding="utf-8")
    positions = [text.find(heading) for heading in SECTIONS]
    assert -1 not in positions, [h for h, p in zip(SECTIONS, positions) if p == -1]
    assert positions == sorted(positions)          # in this order: the fixes for C: come first
    assert "pip download cache" in text           # the 110 MB file in AppData\Local\pip\cache
    assert "Text report saved to" in result.stdout


def test_without_reportlab_the_guided_flow_saves_a_web_page(profile, monkeypatch, capsys):
    """pc-tidy.exe has no reportlab, and neither may a source install: the report is then one web page."""
    import sys

    import disk_report
    from pctidy import context, guided, settings
    monkeypatch.setitem(sys.modules, "pdf_report", None)          # import pdf_report now fails
    ctx = context.current()
    data = disk_report.build_report([str(profile)], use_llm=False, opts=disk_report.prepare(settings.load(ctx, create=True)))

    path = guided.save_report(ctx, data, disk_report.render_text(data))

    assert path.endswith(".html") and path.startswith(ctx.folder("reports"))
    with open(path, encoding="utf-8") as f:
        page = f.read()
    assert page.startswith("<!doctype html>") and "<h2>Fixes for C:</h2>" in page and "WHERE THE SPACE GOES" in page
    assert "so it is saved as a web page" in capsys.readouterr().out


def test_the_report_leads_with_the_ranked_fixes(tmp_path):
    home = make_fake_profile(tmp_path / "big", big_cache=True)
    result = Runner(home, tmp_path, [tmp_path]).python(
        "disk_report.py", "--path", home, "--out", "report.txt", "--out", "report.pdf", "--no-llm", "--no-open",
        env={"PCTIDY_TEST_RECYCLE_BIN": f"{3 * 1024 ** 3},312"})

    assert result.returncode == 0, result.output
    text = (tmp_path / "report.txt").read_text(encoding="utf-8")
    fixes = text[text.index("FIXES FOR C:"):text.index("\nDRIVES")]
    lines = fixes.splitlines()
    assert lines[2].split()[:4] == ["1", "Low", "110", "MB"] and lines[2].endswith("pip and uv caches")
    assert "How: cleanup.ps1: pip_cache, uv_cache (on). Undo: Not needed." in lines[3]
    assert "High" in fixes and "Empty the Recycle Bin (312 items)" in fixes
    assert "Only part of C: was scanned" in fixes
    page1 = PdfReader(str(tmp_path / "report.pdf")).pages[0].extract_text()
    assert page1.index("Fixes for C:") < page1.index("pip and uv caches") < page1.index("Empty the Recycle Bin")
    pages = "".join(p.extract_text() for p in PdfReader(str(tmp_path / "report.pdf")).pages)
    assert pages.index("Empty the Recycle Bin") < pages.index("What's left in your folders") < pages.index("Where the space goes")


def test_a_pdf_open_in_a_viewer_doesnt_lose_the_report(run, profile, tmp_path):
    from pctidy import _win
    pdf = tmp_path / "disk_report.pdf"
    pdf.write_bytes(b"%PDF-1.4 an older report")
    # open it the way a PDF viewer does: no sharing, so nobody else can write it
    handle = _win.kernel32.CreateFileW(str(pdf), 0x80000000, 0, None, 3, 0, None)
    assert handle != _win.INVALID_HANDLE_VALUE
    try:
        result = run.python("disk_report.py", "--path", profile, "--no-llm", "--no-open")
    finally:
        _win.kernel32.CloseHandle(handle)

    assert result.returncode == 0, result.output
    assert "disk_report.pdf is open in another program, so this report goes to disk_report-" in result.stdout
    others = list(tmp_path.glob("disk_report-*.pdf"))
    assert len(others) == 1 and others[0].read_bytes()[:5] == b"%PDF-"
    assert pdf.read_bytes() == b"%PDF-1.4 an older report"


def test_a_missing_reportlab_stops_it_before_the_scan(run, profile, tmp_path):
    from support import GUARD_DIR, ROOT, write
    write(tmp_path / "no-reportlab" / "reportlab" / "__init__.py", "raise ImportError('not installed here')\n")
    path = ";".join(str(p) for p in (tmp_path / "no-reportlab", GUARD_DIR, ROOT))
    result = run.python("disk_report.py", "--path", profile, "--no-llm", "--no-open", env={"PYTHONPATH": path})

    assert result.returncode == 1, result.output
    assert "The PDF report needs reportlab, which isn't installed" in result.stdout
    assert "Scanning" not in result.stdout


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


def test_moves_to_another_volume_are_in_the_fixes(run, profile, tmp_path, ntfs_volume):
    from support import write
    lectures = profile / "Videos" / "Lectures"
    write(lectures / "lecture2.mp4", b"\0" * 3 * 1024 ** 2)
    write(tmp_path / "settings.ini", "[move]\nmin_gb = 0.001\n")

    result = run.python("disk_report.py", "--path", profile, "--out", "report.txt", "--no-llm", "--no-open",
                        extra_roots=[ntfs_volume.root])

    assert result.returncode == 0, result.output
    text = (tmp_path / "report.txt").read_text(encoding="utf-8")
    fixes = text[text.index("FIXES FOR C:"):text.index("\nDRIVES")]
    assert "Medium" in fixes and f"Move {lectures} to {ntfs_volume.root}From-C" in fixes
    assert f'How: python move_off_c.py --path "{lectures}". Undo: python move_off_c.py --undo.' in fixes
