# pc-tidy

Tools for keeping a Windows PC clean, with a small local LLM (via Ollama) for the cases simple rules can't handle.

- `disk_report.py` shows what is filling up the C: drive: the biggest folders and files, known cleanable items with what to do about each, LLM guesses for unrecognized big folders, and installed apps by size. It is read-only.
- `tidy_downloads.py` sorts the Downloads folder into `~/Sorted`. Rules handle most files, and the LLM sorts unclear ones (PDFs, Word docs) by reading their first page. It never deletes, logs every move, and can undo.

## Setup

Requires Python 3.9+ and [Ollama](https://ollama.com).

```
git clone https://github.com/<your-username>/pc-tidy.git
cd pc-tidy
conda create -n pc-tidy python=3.12 -y
conda activate pc-tidy
pip install -r requirements.txt
ollama pull llama3.2:3b
```

## Usage

```
python disk_report.py                    # scan C:\ (a few minutes)
python disk_report.py --out report.txt   # also save the report
python disk_report.py --path D:\         # another drive or folder

python tidy_downloads.py                 # preview: prints planned moves, changes nothing
python tidy_downloads.py --apply         # move files into ~/Sorted
python tidy_downloads.py --undo          # revert the last --apply run
```

Add `--no-llm` to either script to skip the LLM. Run PowerShell as administrator so `disk_report.py` can see protected folders.

## Configuration

- `llm.py`: Ollama model (tested with `llama3.2:3b`) and address
- `disk_report.py`: `KNOWN` list of recognized space users and advice, tree size threshold and depth
- `tidy_downloads.py`: `RULES` (extension to folder), `CATEGORIES` for the LLM, archive age

## Notes

- The size shown for `C:\Windows` is too high because Windows uses hard links. Clean it only with Disk Cleanup.
- Online-only OneDrive files are counted as 0 bytes, because they don't use disk space.
- App sizes come from the installers themselves and are often missing or rough.
