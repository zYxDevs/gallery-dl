# -*- coding: utf-8 -*-

# Copyright 2018-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Execute processes"""

from .common import PostProcessor
from .. import util, formatter
import subprocess
import os


if util.WINDOWS:
    def quote(s):
        return '"' + s.replace('"', '\\"') + '"'
else:
    from shlex import quote


class ExecPP(PostProcessor):

    def __init__(self, job, options):
        PostProcessor.__init__(self, job)

        if options.get("async", False):
            self._exec = self._exec_async

        args = options["command"]
        if isinstance(args, str):
            self.args = args
            execute = self.exec_string
        else:
            self.args = [formatter.parse(arg) for arg in args]
            execute = self.exec_list

        events = options.get("event")
        if events is None:
            events = ("after",)
        elif isinstance(events, str):
            events = events.split(",")
        job.register_hooks({event: execute for event in events}, options)

        self._init_archive(job, options)

    def exec_list(self, pathfmt, status=None):
        if status:
            return

        archive = self.archive
        kwdict = pathfmt.kwdict

        if archive and archive.check(kwdict):
            return

        kwdict["_directory"] = pathfmt.realdirectory
        kwdict["_filename"] = pathfmt.filename
        kwdict["_path"] = pathfmt.realpath

        args = [arg.format_map(kwdict) for arg in self.args]
        args[0] = os.path.expanduser(args[0])
        self._exec(args, False)

        if archive:
            archive.add(kwdict)

    def exec_string(self, pathfmt, status=None):
        if status:
            return

        archive = self.archive
        if archive and archive.check(pathfmt.kwdict):
            return

        if status is None and pathfmt.realpath:
            args = self.args.replace("{}", quote(pathfmt.realpath))
        else:
            args = self.args.replace("{}", quote(pathfmt.realdirectory))

        self._exec(args, True)

        if archive:
            archive.add(pathfmt.kwdict)

    def _exec(self, args, shell):
        self.log.debug("Running '%s'", args)
        if retcode := subprocess.Popen(args, shell=shell).wait():
            self.log.warning("'%s' returned with non-zero exit status (%d)",
                             args, retcode)

    def _exec_async(self, args, shell):
        self.log.debug("Running '%s'", args)
        subprocess.Popen(args, shell=shell)


__postprocessor__ = ExecPP
