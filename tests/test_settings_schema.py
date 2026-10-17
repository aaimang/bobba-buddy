"""The settings window's page layout matches the config, and the logo is in sync."""

from __future__ import annotations

from pathlib import Path

from bobba_buddy.config import EFFORT_LEVELS
from bobba_buddy.settings.configfile import config_fields
from bobba_buddy.settings.schema import BOX_ICONS, HIDDEN, PAGE_ICONS, PAGES, all_fields

ROOT = Path(__file__).resolve().parents[1]
KINDS = {"toggle": bool, "number": (int, float), "text": str, "names": list, "chips": list}


def test_every_config_key_is_shown_or_hidden():
    shown = {f.id for f in all_fields()}
    assert set(config_fields()) == shown | HIDDEN
    assert not shown & HIDDEN


def test_no_field_appears_twice():
    ids = [f.id for f in all_fields()]
    assert len(ids) == len(set(ids))


def test_field_kinds_match_the_config_types():
    defaults = config_fields()
    for field in all_fields():
        default = defaults[field.id]
        if field.kind == "choice":
            assert default in field.options, field.id
            continue
        expected = KINDS[field.kind]
        assert isinstance(default, expected), field.id
        if field.kind == "number":
            assert not isinstance(default, bool), field.id
            if isinstance(default, int):
                assert field.decimals == 0 and float(field.step).is_integer(), field.id


def test_defaults_are_within_the_limits():
    defaults = config_fields()
    for field in all_fields():
        if field.kind == "number":
            value = defaults[field.id]
            assert field.min is None or value >= field.min, field.id
            assert field.max is None or value <= field.max, field.id


def test_effort_choices_follow_the_config():
    effort = next(f for f in all_fields() if f.id == ("claude", "effort"))
    assert effort.options == EFFORT_LEVELS


def test_pages_have_unique_names():
    names = [p.name for p in PAGES]
    assert len(names) == len(set(names)) and "About" not in names


def test_packaged_logo_matches_the_docs_logo():
    packaged = ROOT / "src" / "bobba_buddy" / "assets" / "logo.png"
    docs = ROOT / "docs" / "sphinx" / "_static" / "logo.png"
    assert packaged.read_bytes() == docs.read_bytes()


def _make_icons():
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_icons", ROOT / "scripts" / "make_icons.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_page_and_box_has_an_icon():
    titles = {box.title for page in PAGES for box in page.boxes} | {"About Bobba Buddy"}
    assert set(PAGE_ICONS) == {p.name for p in PAGES} | {"About"}
    assert titles <= set(BOX_ICONS)


def test_icon_pngs_exist_in_both_sizes():
    make_icons = _make_icons()
    icon_dir = ROOT / "src" / "bobba_buddy" / "assets" / "icons"
    wanted = [(n, "nav") for n in PAGE_ICONS.values()] + [(n, "box") for n in BOX_ICONS.values()]
    wanted += [("brand-github", "link"), ("brand-github", "white")]
    for name, variant in wanted:
        assert name in make_icons.ICONS[variant], (name, variant)
        size = make_icons.VARIANTS[variant][1]
        for px in (size, size * 2):
            assert (icon_dir / f"{name}-{variant}-{px}.png").is_file(), (name, variant, px)
    assert (icon_dir / "LICENSE").is_file()  # Tabler is MIT: keep its licence with the PNGs
