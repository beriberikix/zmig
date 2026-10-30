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


def test_crossed_releases_without_a_migration_guide_are_not_checked() -> None:
    """AUDIT.md F4: Zephyr has doc/releases/migration-guide-X.Y.rst only from 3.5 on, so a
    2.7 -> 3.7 scan read no guide for 3.0-3.4 and still reported the guide analyzer ok."""
    from zmig.analyzers.guide import GuideAnalyzer
    from zmig.guides import Document

    def doc(v: str, kind: str) -> Document:
        name = "migration-guide" if kind == "guide" else "release-notes"
        return Document(f"doc/releases/{name}-{v}.rst", v, "v3.7.0", "", kind)  # type: ignore[arg-type]

    docs = [doc("3.4", "notes"), doc("3.5", "guide"), doc("3.5", "notes"), doc("3.6", "guide")]
    gaps = GuideAnalyzer.guide_gaps(["3.3", "3.4", "3.5", "3.6"], docs)
    assert [g.area for g in gaps] == ["3.3 migration guide", "3.4 migration guide"]
    assert "no release notes either" in gaps[0].reason
    assert "only its release notes' Removed/Deprecated sections" in gaps[1].reason
    assert all(g.analyzer == "guide" for g in gaps)
    assert GuideAnalyzer.guide_gaps(["3.5", "3.6"], docs) == []


def test_a_vendor_section_applies_only_to_that_vendors_soc() -> None:
    """DOGFOOD-12 E3, proves (RP2350): the 4.4 guide's "NXP" entries (`soc-nv-flash`) and
    its "STM32" entry (`cs-gpios`) matched the board's DTS. A section named for a SoC vendor
    applies to builds on that vendor's SoC (CONFIG_SOC_FAMILY / _SERIES / CONFIG_SOC)."""
    from zmig.guides import vendor_mismatch

    rp = {"SOC_FAMILY": '"rpi_pico"', "SOC_SERIES": '"rp2350"', "SOC": '"rp2350a"'}
    stm = {"SOC_FAMILY": '"stm32"', "SOC_SERIES": '"stm32h5x"', "SOC": '"stm32h563xx"'}
    nxp_heads = ("Device Drivers and Devicetree", "NXP")
    assert vendor_mismatch(nxp_heads, rp) == "NXP"
    assert vendor_mismatch(("Device Drivers and Devicetree", "STM32"), rp) == "STM32"
    assert vendor_mismatch(("Device Drivers and Devicetree", "STM32"), stm) is None
    assert vendor_mismatch(("Device Drivers and Devicetree", "Raspberry Pi"), rp) is None
    assert vendor_mismatch(("Networking", "CoAP"), rp) is None  # not a vendor section
    assert vendor_mismatch(nxp_heads, {}) is None  # no configuration: can't tell


def test_a_struct_member_mention_names_its_struct() -> None:
    """DOGFOOD-12 E3: `coap_resource.user_data` matched every `user_data` parameter, and
    `bt_conn_le_info.interval` a `bt_le_scan_param` initializer's `.interval`."""
    from zmig.guides import member_qualifiers

    s = "bt_conn_le_info.interval has been deprecated. Use bt_conn_le_info.interval_us instead."
    assert member_qualifiers(s, "interval") == ["bt_conn_le_info"]
    assert member_qualifiers("instead of coap_resource.user_data, which", "user_data") == [
        "coap_resource"
    ]
    assert member_qualifiers("k_work_submit() now returns 1", "k_work_submit") == []
