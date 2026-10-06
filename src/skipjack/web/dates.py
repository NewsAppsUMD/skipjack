"""Month arithmetic for report dates written as "YYYY-MM"."""

from __future__ import annotations

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]  # fmt: skip


def shift_month(report_date: str, months: int) -> str:
    """The report date ``months`` after (or, if negative, before) ``report_date``."""
    year, month = int(report_date[:4]), int(report_date[5:7])
    index = year * 12 + (month - 1) + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def month_label(report_date: str) -> str:
    """ "2026-08" -> "August 2026"."""
    return f"{MONTH_NAMES[int(report_date[5:7]) - 1]} {report_date[:4]}"
