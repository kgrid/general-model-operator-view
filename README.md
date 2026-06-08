# GMOV Prototype 17

GMOV is a prototype Model Player for FAIR Digital Object (FDO) Models.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open:

```text
http://127.0.0.1:5001
```

## Demo

1. Start GMOV with no Models loaded.
2. Click **Choose FDO** and select one or more ZIP files from `sample_fdos/`.
3. Select a Model to view its declared Services.
4. If a Model shows **Info**, click it to open the FDO's top-level `index.html` information page.
5. Run a Service; results accumulate under that Service.
6. Use **Ask Agent to Compute** to demonstrate the MVC constraint: computations run only through Ready Services from loaded Models.

## Prototype 17 changes

- Added top-level FDO `index.html` detection and an **Info** link beside Model status.
- Removed **Ask About Models** from the UI.
- Focused Ask Agent on constrained computation through loaded Ready Services.
- Renamed the Ask panel to **Ask Agent**.
- Fixed per-Service Run numbering so a Service's first Run starts at Run 1.
- Replaced long default JSON display with a demo-friendly result summary and collapsed Raw JSON.

## Prototype 18 notes

Prototype 18 adds Model unloading:

- Use **Unload** on a Model row to remove that loaded Model from GMOV.
- Use **Unload All** in the Models header to reset the loaded Model library.
- Unloading a Model Assembly also unloads its member Models.
- Assembly-owned member Models are protected from independent unload; unload the parent Assembly instead.

Unloading only removes Models from the local GMOV session library. It does not delete the original FAIR Digital Object ZIP files.
