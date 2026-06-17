from pathlib import Path


def test_packaging_assets_present() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "assets" / "signalloom.png").is_file()
    assert (root / "assets" / "signalloom.ico").is_file()
    assert (root / "SignalLoomOps.spec").is_file()
    assert (root / "installer" / "SignalLoomOps.iss").is_file()


def test_ui_imports_without_launching() -> None:
    import loom.ui as ui

    assert ui.APP == "SignalLoom"
    assert hasattr(ui, "LoadOverlay")
