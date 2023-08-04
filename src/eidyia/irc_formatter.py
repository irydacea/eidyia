#!/usr/bin/env python3
'''
Wesnoth Site Status Service Stateful Support Survey Bot (codename Eidyia)

Copyright (C) 2023 by Iris Morelle <iris@irydacea.me>
See COPYING for use and distribution terms.
'''

from dataclasses import dataclass
from typing import List

import src.eidyia.ui_utils as ui
from src.valen.V1Report import Report as V1Report

# Split long status lines past this byte count
_SPLIT_THRESHOLD = 190

# Break formatting past this byte count
_QUICKFORMAT_THRESHOLD = 350

# Maximum columns for table displays
_SPLIT_TABLE_COLUMNS = 6

# Formatting prefix
_PREFIX = ' '


def u8bytecount(u8string: str) -> int:
    '''
    Returns the byte count of a UTF-8 string.
    '''
    return len(u8string.encode('utf-8'))


@dataclass
class _Cell:
    visual: str
    raw: str

    def byte_count(self) -> int:
        return u8bytecount(self.raw)

    def width(self) -> int:
        return len(self.visual)

    def padded(self, length: int) -> str:
        visual_padding = length - self.width()
        if visual_padding <= 0:
            return self.raw
        else:
            return self.raw.ljust(len(self.raw) + visual_padding)


class Table:
    def __init__(self):
        self._table: List[List[str]] = [[]]
        self._colspans: List[int] = []

    def push(self,
             facility_name: str,
             status: V1Report.FacilityStatus):
        '''
        Adds a cell to the table.
        '''
        if (len(self._table[-1]) > _SPLIT_TABLE_COLUMNS or
                self._row_bytecount() > min(_SPLIT_THRESHOLD, _QUICKFORMAT_THRESHOLD)):
            self._new_column()
            colnum = 0
        else:
            colnum = len(self._table[-1])

        bold = ui.IrcFormat.BOLD
        colour = ui.status_to_irc_colour(status)
        caption = ui.status_to_caption(status)
        icon = ui.status_to_irc_icon(status)
        # NOTE: internally the table has separate columns for item labels and
        # status icons, meaning we have to push two columns at a time.
        label_col = _Cell(facility_name, bold.apply(facility_name))
        status_col = _Cell(
            f'{icon} {caption}',
            ' '.join((colour.apply(icon),
                      colour.apply(caption)))
            )
        self._table[-1] += [label_col, status_col]
        if colnum + 1 >= len(self._colspans):
            self._colspans += [label_col.width(), status_col.width()]
        else:
            self._colspans[colnum] = max(
                self._colspans[colnum], label_col.width())
            self._colspans[colnum + 1] = max(
                self._colspans[colnum + 1], status_col.width())

    def format(self) -> List[str]:
        '''
        Returns a list of IRC-formatted text rows.
        '''
        return [self._format_row(num) for num, _ in enumerate(self._table)]

    def _new_column(self):
        self._table.append([])

    def _widest_overall(self) -> int:
        '''
        Returns the largest cell width (without padding).
        '''
        return max(col.width() for row in self._table for col in row)

    def _column_width(self, colnum: int) -> int:
        return self._colspans[colnum]

    def _quickformat_row(self, rownum: int = -1) -> str:
        return _PREFIX + '  '.join(cell.raw for cell in self._table[rownum])

    def _format_row(self, rownum: int = -1) -> str:
        quick = self._quickformat_row(rownum)
        if u8bytecount(quick) <= _QUICKFORMAT_THRESHOLD:
            # We can fancy format the row without running afoul of the IRC length
            # limit, hopefully
            cols = []
            for n, col in enumerate(self._table[rownum]):
                cols.append(col.padded(self._column_width(n)))
            coltext = _PREFIX + '  '.join(cols)
            if u8bytecount(coltext) <= _QUICKFORMAT_THRESHOLD:
                return coltext
        return quick

    def _row_bytecount(self, rownum: int = -1) -> int:
        return u8bytecount(self._quickformat_row(rownum))
