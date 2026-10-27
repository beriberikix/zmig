"""Guide parsing on a sample written the way Zephyr's guides are (headings, nested
bullets, list-tables, roles and literals)."""

from __future__ import annotations

from zmig.guides import parse

SAMPLE = """\
:orphan:

.. _migration_9.9:

Migration guide to Zephyr v9.9.0
################################

Intro paragraph mentioning nothing.

.. contents::
    :local:

Build System
************

* The following build-time generated headers:

  .. list-table::
     :header-rows: 1

     * - Affected header files
     * - ``version.h``

  are now namespaced into the ``zephyr/`` folder. This Kconfig will be deprecated and
  eventually removed in the future.

Device Drivers
**************

CAN
===

* Removed the following deprecated CAN properties:

  * ``sjw``
  * ``prop-seg``

  The ``bus-speed`` property has been deprecated.

* Support for recovery was reworked (:github:`1`):

  * The Kconfig ``CONFIG_OLD_THING`` was renamed (and inverted) to
    :kconfig:option:`CONFIG_NEW_THING`, which is disabled by default.
  * :c:func:`can_recover()` now requires :kconfig:option:`CONFIG_NEW_THING`.

* The following structures have been renamed:

  * ``emul_old_api`` to :c:struct:`emul_new_api`

* Added :dtcompatible:`vnd,widget` and the header :zephyr_file:`include/zephyr/drivers/w.h`.
"""


def test_entries_and_headings() -> None:
    es = parse(SAMPLE, "doc/releases/migration-guide-9.9.rst", "9.9", "MG")
    assert [e.id for e in es] == [f"MG-9.9-{i:04d}" for i in range(1, 7)]
    assert es[0].headings == ()  # the intro paragraph, under the title only
    assert es[1].headings == ("Build System",)
    assert es[2].headings == ("Device Drivers", "CAN")


def ln(needle: str) -> int:
    """1-based line of the first line of SAMPLE containing `needle`."""
    return next(i for i, line in enumerate(SAMPLE.splitlines(), 1) if needle in line)


def mentions(entry: object) -> dict[str, tuple[str, str, int, str]]:
    return {
        m.value: (m.kind, m.strength, m.line, m.severity)  # type: ignore[attr-defined]
        for m in entry.mentions
    }  # type: ignore[attr-defined]


def test_mentions_lines_and_severity() -> None:
    es = parse(SAMPLE, "doc", "9.9", "MG")
    hdr = mentions(es[1])
    assert hdr["version.h"] == (
        "header",
        "literal",
        ln("``version.h``"),
        "risky",
    )  # "deprecated ... in the future"
    props = mentions(es[2])
    assert props["sjw"] == (
        "literal",
        "literal",
        ln("``sjw``"),
        "breaking",
    )  # "Removed" wins over "deprecated"
    assert props["bus-speed"] == ("literal", "literal", ln("``bus-speed``"), "risky")
    rec = mentions(es[3])
    assert rec["OLD_THING"] == ("kconfig", "literal", ln("CONFIG_OLD_THING"), "breaking")
    assert rec["NEW_THING"][:2] == ("kconfig", "role")
    assert rec["can_recover"] == ("ident", "role", ln("can_recover"), "breaking")
    last = mentions(es[5])
    assert last["vnd,widget"][:2] == ("compat", "role")
    assert last["zephyr/drivers/w.h"][:2] == ("header", "role")


def test_renames() -> None:
    es = parse(SAMPLE, "doc", "9.9", "MG")
    assert [(r.kind, r.old, r.new) for r in es[3].renames] == [
        ("kconfig", "OLD_THING", "NEW_THING")
    ]
    assert [(r.kind, r.old, r.new) for r in es[4].renames] == [
        ("ident", "emul_old_api", "emul_new_api")
    ]


def test_titles_have_no_rst_markup() -> None:
    es = parse(SAMPLE, "doc", "9.9", "MG")
    assert es[1].lead.startswith("The following build-time generated headers:")
    assert "list-table" not in es[1].lead and "* -" not in es[1].lead


def test_release_notes_section_filter() -> None:
    import re

    notes = (
        "Zephyr 9.9.0\n############\n\nAPI Changes\n***********\n\n"
        "Removed APIs and options\n========================\n\n"
        "* Removed :c:func:`old_api`.\n\n"
        "Other\n=====\n\n* Added :c:func:`new_api`.\n"
    )
    es = parse(notes, "rn", "9.9", "RN", re.compile(r"^(removed|deprecated)\b", re.I))
    assert [m.value for e in es for m in e.mentions] == ["old_api"]
