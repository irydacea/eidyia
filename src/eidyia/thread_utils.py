#!/usr/bin/env python3
'''
Wesnoth Site Status Service Stateful Support Survey Bot (codename Eidyia)

Copyright (C) 2023 by Iris Morelle <iris@irydacea.me>
See COPYING for use and distribution terms.
'''

import threading


class ConcurrentFlag:
    '''
    Simple lockable flag variable wrapper.

    The flag is initially cleared after construction. set(), clear() and get()
    methods are provided to interact with the flag.
    '''
    def __init__(self):
        self._flag = False
        self._lock = threading.Lock()

    def set(self):
        '''
        Sets the flag.
        '''
        with self._lock:
            self._flag = True

    def clear(self):
        '''
        Clears the flag.
        '''
        with self._lock:
            self._flag = False

    def get(self):
        '''
        Retrieves the current flag value.
        '''
        with self._lock:
            return self._flag
