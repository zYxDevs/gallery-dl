# -*- coding: utf-8 -*-

# Copyright 2015-2023 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

import sys
import errno
import logging
import functools
import collections
from . import extractor, downloader, postprocessor
from . import config, text, util, path, formatter, output, exception, version
from .extractor.message import Message
from .output import stdout_write


class Job():
    """Base class for Job-types"""
    ulog = None

    def __init__(self, extr, parent=None):
        if isinstance(extr, str):
            extr = extractor.find(extr)
        if not extr:
            raise exception.NoExtractorError()

        self.extractor = extr
        self.pathfmt = None
        self.kwdict = {}
        self.status = 0

        cfgpath = []
        if parent and parent.extractor.category != extr.category:
            cat = f"{parent.extractor.category}>{extr.category}"
            cfgpath.extend(((cat, extr.subcategory), (extr.category, extr.subcategory)))
        if extr.basecategory:
            if not cfgpath:
                cfgpath.append((extr.category, extr.subcategory))
            cfgpath.append((extr.basecategory, extr.subcategory))
        if cfgpath:
            extr._cfgpath = cfgpath
            extr.config = extr._config_shared
            extr.config_accumulate = extr._config_shared_accumulate

        if actions := extr.config("actions"):
            from .actions import parse
            self._logger_actions = parse(actions)
            self._wrap_logger = self._wrap_logger_actions

        path_proxy = output.PathfmtProxy(self)
        self._logger_extra = {
            "job"      : self,
            "extractor": extr,
            "path"     : path_proxy,
            "keywords" : output.KwdictProxy(self),
        }
        extr.log = self._wrap_logger(extr.log)
        extr.log.debug("Using %s for '%s'", extr.__class__.__name__, extr.url)

        # data from parent job
        if parent:
            pextr = parent.extractor

            # transfer (sub)category
            if pextr.config("category-transfer", pextr.categorytransfer):
                extr._cfgpath = pextr._cfgpath
                extr.category = pextr.category
                extr.subcategory = pextr.subcategory

        self.metadata_url = extr.config("url-metadata")
        self.metadata_http = extr.config("http-metadata")

        version_info = extr.config("version-metadata")
        metadata_path = extr.config("path-metadata")

        if kwdict := extr.config("keywords"):
            self.kwdict |= kwdict
        if metadata_path:
            self.kwdict[metadata_path] = path_proxy
        if version_info:
            self.kwdict[version_info] = {
                "version"         : version.__version__,
                "is_executable"   : util.EXECUTABLE,
                "current_git_head": util.git_head()
            }

        # predicates
        self.pred_url = self._prepare_predicates("image", True)
        self.pred_queue = self._prepare_predicates("chapter", False)

    def run(self):
        """Execute or run the job"""
        extractor = self.extractor
        log = extractor.log
        msg = None

        if sleep := util.build_duration_func(extractor.config("sleep-extractor")):
            extractor.sleep(sleep(), "extractor")

        try:
            for msg in extractor:
                self.dispatch(msg)
        except exception.StopExtraction as exc:
            if exc.message:
                log.error(exc.message)
            self.status |= exc.code
        except (exception.TerminateExtraction, exception.RestartExtraction):
            raise
        except exception.GalleryDLException as exc:
            log.error("%s: %s", exc.__class__.__name__, exc)
            self.status |= exc.code
        except OSError as exc:
            log.error("Unable to download data:  %s: %s",
                      exc.__class__.__name__, exc)
            log.debug("", exc_info=True)
            self.status |= 128
        except Exception as exc:
            log.error(("An unexpected error occurred: %s - %s. "
                       "Please run gallery-dl again with the --verbose flag, "
                       "copy its output and report this issue on "
                       "https://github.com/mikf/gallery-dl/issues ."),
                      exc.__class__.__name__, exc)
            log.debug("", exc_info=True)
            self.status |= 1
        except BaseException:
            self.status |= 1
            raise
        else:
            if msg is None:
                log.info("No results for %s", extractor.url)
        finally:
            self.handle_finalize()
            extractor.finalize()

        return self.status

    def dispatch(self, msg):
        """Call the appropriate message handler"""
        if msg[0] == Message.Url:
            _, url, kwdict = msg
            if self.metadata_url:
                kwdict[self.metadata_url] = url
            if self.pred_url(url, kwdict):
                self.update_kwdict(kwdict)
                self.handle_url(url, kwdict)

        elif msg[0] == Message.Directory:
            self.update_kwdict(msg[1])
            self.handle_directory(msg[1])

        elif msg[0] == Message.Queue:
            _, url, kwdict = msg
            if self.metadata_url:
                kwdict[self.metadata_url] = url
            if self.pred_queue(url, kwdict):
                self.handle_queue(url, kwdict)

    def handle_url(self, url, kwdict):
        """Handle Message.Url"""

    def handle_directory(self, kwdict):
        """Handle Message.Directory"""

    def handle_queue(self, url, kwdict):
        """Handle Message.Queue"""

    def handle_finalize(self):
        """Handle job finalization"""

    def update_kwdict(self, kwdict):
        """Update 'kwdict' with additional metadata"""
        extr = self.extractor
        kwdict["category"] = extr.category
        kwdict["subcategory"] = extr.subcategory
        if self.metadata_http:
            kwdict.pop(self.metadata_http, None)
        if self.kwdict:
            kwdict.update(self.kwdict)

    def _prepare_predicates(self, target, skip=True):
        predicates = []

        if self.extractor.config(f"{target}-unique"):
            predicates.append(util.UniquePredicate())

        pfilter = self.extractor.config(f"{target}-filter")
        if pfilter:
            try:
                pred = util.FilterPredicate(pfilter, target)
            except (SyntaxError, ValueError, TypeError) as exc:
                self.extractor.log.warning(exc)
            else:
                predicates.append(pred)

        if prange := self.extractor.config(f"{target}-range"):
            try:
                pred = util.RangePredicate(prange)
            except ValueError as exc:
                self.extractor.log.warning(
                    "invalid %s range: %s", target, exc)
            else:
                if skip and pred.lower > 1 and not pfilter:
                    pred.index += self.extractor.skip(pred.lower - 1)
                predicates.append(pred)

        return util.build_predicate(predicates)

    def get_logger(self, name):
        return self._wrap_logger(logging.getLogger(name))

    def _wrap_logger(self, logger):
        return output.LoggerAdapter(logger, self)

    def _wrap_logger_actions(self, logger):
        return output.LoggerAdapterActions(logger, self)

    def _write_unsupported(self, url):
        if self.ulog:
            self.ulog.info(url)


class DownloadJob(Job):
    """Download images into appropriate directory/filename locations"""

    def __init__(self, url, parent=None):
        Job.__init__(self, url, parent)
        self.log = self.get_logger("download")
        self.fallback = None
        self.archive = None
        self.sleep = None
        self.hooks = ()
        self.downloaders = {}
        self.out = output.select()
        self.visited = parent.visited if parent else set()
        self._extractor_filter = None
        self._skipcnt = 0

    def handle_url(self, url, kwdict):
        """Download the resource specified in 'url'"""
        hooks = self.hooks
        pathfmt = self.pathfmt
        archive = self.archive

        # prepare download
        pathfmt.set_filename(kwdict)

        if "prepare" in hooks:
            for callback in hooks["prepare"]:
                callback(pathfmt)

        if archive and archive.check(kwdict):
            pathfmt.fix_extension()
            self.handle_skip()
            return

        if pathfmt.extension and not self.metadata_http:
            pathfmt.build_path()

            if pathfmt.exists():
                if archive:
                    archive.add(kwdict)
                self.handle_skip()
                return

        if self.sleep:
            self.extractor.sleep(self.sleep(), "download")

        # download from URL
        if not self.download(url):

            # use fallback URLs if available/enabled
            fallback = kwdict.get("_fallback", ()) if self.fallback else ()
            for num, url in enumerate(fallback, 1):
                util.remove_file(pathfmt.temppath)
                self.log.info("Trying fallback URL #%d", num)
                if self.download(url):
                    break
            else:
                # download failed
                self.status |= 4
                self.log.error("Failed to download %s",
                               pathfmt.filename or url)
                return

        if not pathfmt.temppath:
            if archive:
                archive.add(kwdict)
            self.handle_skip()
            return

        # run post processors
        if "file" in hooks:
            for callback in hooks["file"]:
                callback(pathfmt)

        # download succeeded
        pathfmt.finalize()
        self.out.success(pathfmt.path)
        self._skipcnt = 0
        if archive:
            archive.add(kwdict)
        if "after" in hooks:
            for callback in hooks["after"]:
                callback(pathfmt)

    def handle_directory(self, kwdict):
        """Set and create the target directory for downloads"""
        if not self.pathfmt:
            self.initialize(kwdict)
        else:
            if "post-after" in self.hooks:
                for callback in self.hooks["post-after"]:
                    callback(self.pathfmt)
            self.pathfmt.set_directory(kwdict)
        if "post" in self.hooks:
            for callback in self.hooks["post"]:
                callback(self.pathfmt)

    def handle_queue(self, url, kwdict):
        if url in self.visited:
            return
        self.visited.add(url)

        if cls := kwdict.get("_extractor"):
            extr = cls.from_url(url)
        else:
            extr = extractor.find(url)
            if extr:
                if self._extractor_filter is None:
                    self._extractor_filter = self._build_extractor_filter()
                if not self._extractor_filter(extr):
                    extr = None

        if extr:
            job = self.__class__(extr, self)
            pfmt = self.pathfmt
            pextr = self.extractor

            if pfmt and pextr.config("parent-directory"):
                extr._parentdir = pfmt.directory
            else:
                extr._parentdir = pextr._parentdir

            if pmeta := pextr.config("parent-metadata"):
                if isinstance(pmeta, str):
                    data = self.kwdict.copy()
                    if kwdict:
                        data.update(kwdict)
                    job.kwdict[pmeta] = data
                else:
                    if self.kwdict:
                        job.kwdict.update(self.kwdict)
                    if kwdict:
                        job.kwdict.update(kwdict)

            while True:
                try:
                    if pextr.config("parent-skip"):
                        job._skipcnt = self._skipcnt
                        self.status |= job.run()
                        self._skipcnt = job._skipcnt
                    else:
                        self.status |= job.run()
                    break
                except exception.RestartExtraction:
                    pass

        else:
            self._write_unsupported(url)

    def handle_finalize(self):
        if self.archive:
            self.archive.close()

        if pathfmt := self.pathfmt:
            hooks = self.hooks
            if "post-after" in hooks:
                for callback in hooks["post-after"]:
                    callback(pathfmt)

            self.extractor.cookies_store()
            if "finalize" in hooks:
                status = self.status
                for callback in hooks["finalize"]:
                    callback(pathfmt, status)

    def handle_skip(self):
        pathfmt = self.pathfmt
        self.out.skip(pathfmt.path)
        if "skip" in self.hooks:
            for callback in self.hooks["skip"]:
                callback(pathfmt)
        if self._skipexc:
            self._skipcnt += 1
            if self._skipcnt >= self._skipmax:
                raise self._skipexc()

    def download(self, url):
        """Download 'url'"""
        scheme = url.partition(":")[0]
        if downloader := self.get_downloader(scheme):
            try:
                return downloader.download(url, self.pathfmt)
            except OSError as exc:
                if exc.errno == errno.ENOSPC:
                    raise
                self.log.warning("%s: %s", exc.__class__.__name__, exc)
                return False
        self._write_unsupported(url)
        return False

    def get_downloader(self, scheme):
        """Return a downloader suitable for 'scheme'"""
        try:
            return self.downloaders[scheme]
        except KeyError:
            pass

        cls = downloader.find(scheme)
        if cls and config.get(("downloader", cls.scheme), "enabled", True):
            instance = cls(self)
        else:
            instance = None
            self.log.error("'%s:' URLs are not supported/enabled", scheme)

        if cls and cls.scheme == "http":
            self.downloaders["http"] = self.downloaders["https"] = instance
        else:
            self.downloaders[scheme] = instance
        return instance

    def initialize(self, kwdict=None):
        """Delayed initialization of PathFormat, etc."""
        extr = self.extractor
        cfg = extr.config

        pathfmt = self.pathfmt = path.PathFormat(extr)
        if kwdict:
            pathfmt.set_directory(kwdict)

        self.sleep = util.build_duration_func(cfg("sleep"))
        self.fallback = cfg("fallback", True)
        if not cfg("download", True):
            # monkey-patch method to do nothing and always return True
            self.download = pathfmt.fix_extension

        if archive := cfg("archive"):
            archive = util.expand_path(archive)
            archive_format = (cfg("archive-prefix", extr.category) +
                              cfg("archive-format", extr.archive_fmt))
            archive_pragma = (cfg("archive-pragma"))
            try:
                if "{" in archive:
                    archive = formatter.parse(archive).format_map(kwdict)
                self.archive = util.DownloadArchive(
                    archive, archive_format, archive_pragma)
            except Exception as exc:
                extr.log.warning(
                    "Failed to open download archive at '%s' ('%s: %s')",
                    archive, exc.__class__.__name__, exc)
            else:
                extr.log.debug("Using download archive '%s'", archive)

        if skip := cfg("skip", True):
            self._skipexc = None
            if skip == "enumerate":
                pathfmt.check_file = pathfmt._enum_file
            elif isinstance(skip, str):
                skip, _, smax = skip.partition(":")
                if skip == "abort":
                    self._skipexc = exception.StopExtraction
                elif skip == "terminate":
                    self._skipexc = exception.TerminateExtraction
                elif skip == "exit":
                    self._skipexc = sys.exit
                self._skipmax = text.parse_int(smax)
        else:
            # monkey-patch methods to always return False
            pathfmt.exists = lambda x=None: False
            if self.archive:
                self.archive.check = pathfmt.exists

        if not cfg("postprocess", True):
            return

        if postprocessors := extr.config_accumulate("postprocessors"):
            self.hooks = collections.defaultdict(list)

            pp_log = self.get_logger("postprocessor")
            pp_conf = config.get((), "postprocessor") or {}
            pp_opts = cfg("postprocessor-options")
            pp_list = []

            for pp_dict in postprocessors:
                if isinstance(pp_dict, str):
                    pp_dict = pp_conf.get(pp_dict) or {"name": pp_dict}
                if pp_opts:
                    pp_dict = pp_dict.copy()
                    pp_dict.update(pp_opts)

                clist = pp_dict.get("whitelist")
                if clist is not None:
                    negate = False
                else:
                    clist = pp_dict.get("blacklist")
                    negate = True
                if clist and not util.build_extractor_filter(
                        clist, negate)(extr):
                    continue

                name = pp_dict.get("name")
                pp_cls = postprocessor.find(name)
                if not pp_cls:
                    pp_log.warning("module '%s' not found", name)
                    continue
                try:
                    pp_obj = pp_cls(self, pp_dict)
                except Exception as exc:
                    pp_log.error("'%s' initialization failed:  %s: %s",
                                 name, exc.__class__.__name__, exc)
                    pp_log.debug("", exc_info=True)
                else:
                    pp_list.append(pp_obj)

            if pp_list:
                extr.log.debug("Active postprocessor modules: %s", pp_list)
                if "init" in self.hooks:
                    for callback in self.hooks["init"]:
                        callback(pathfmt)

    def register_hooks(self, hooks, options=None):
        expr = options.get("filter") if options else None

        if expr:
            condition = util.compile_expression(expr)
            for hook, callback in hooks.items():
                self.hooks[hook].append(functools.partial(
                    self._call_hook, callback, condition))
        else:
            for hook, callback in hooks.items():
                self.hooks[hook].append(callback)

    @staticmethod
    def _call_hook(callback, condition, pathfmt):
        if condition(pathfmt.kwdict):
            callback(pathfmt)

    def _build_extractor_filter(self):
        clist = self.extractor.config("whitelist")
        if clist is not None:
            negate = False
            special = None
        else:
            clist = self.extractor.config("blacklist")
            negate = True
            special = util.SPECIAL_EXTRACTORS
        if clist is None:
            clist = (self.extractor.category,)

        return util.build_extractor_filter(clist, negate, special)


class SimulationJob(DownloadJob):
    """Simulate the extraction process without downloading anything"""

    def handle_url(self, url, kwdict):
        if not kwdict["extension"]:
            kwdict["extension"] = "jpg"
        if self.sleep:
            self.extractor.sleep(self.sleep(), "download")
        if self.archive:
            self.archive.add(kwdict)
        self.out.skip(self.pathfmt.build_filename(kwdict))

    def handle_directory(self, kwdict):
        if not self.pathfmt:
            self.initialize()


class KeywordJob(Job):
    """Print available keywords"""

    def __init__(self, url, parent=None):
        Job.__init__(self, url, parent)
        self.private = config.get(("output",), "private")

    def handle_url(self, url, kwdict):
        stdout_write("\nKeywords for filenames and --filter:\n"
                     "------------------------------------\n")

        if self.metadata_http and url.startswith("http"):
            kwdict[self.metadata_http] = util.extract_headers(
                self.extractor.request(url, method="HEAD"))

        self.print_kwdict(kwdict)
        raise exception.StopExtraction()

    def handle_directory(self, kwdict):
        stdout_write("Keywords for directory names:\n"
                     "-----------------------------\n")
        self.print_kwdict(kwdict)

    def handle_queue(self, url, kwdict):
        extr = kwdict["_extractor"].from_url(url) if "_extractor" in kwdict else None
        if not util.filter_dict(kwdict):
            self.extractor.log.info(
                "This extractor only spawns other extractors "
                "and does not provide any metadata on its own.")

            if extr:
                self.extractor.log.info(
                    "Showing results for '%s' instead:\n", url)
                KeywordJob(extr, self).run()
            else:
                self.extractor.log.info(
                    "Try 'gallery-dl -K \"%s\"' instead.", url)
        else:
            stdout_write("Keywords for --chapter-filter:\n"
                         "------------------------------\n")
            self.print_kwdict(kwdict)
            if extr or self.extractor.categorytransfer:
                stdout_write("\n")
                KeywordJob(extr or url, self).run()
        raise exception.StopExtraction()

    def print_kwdict(self, kwdict, prefix="", markers=None):
        """Print key-value pairs in 'kwdict' with formatting"""
        write = sys.stdout.write
        suffix = "']" if prefix else ""

        markerid = id(kwdict)
        if markers is None:
            markers = {markerid}
        elif markerid in markers:
            write(f"{prefix[:-2]}\n  <circular reference>\n")
            return  # ignore circular reference
        else:
            markers.add(markerid)

        for key, value in sorted(kwdict.items()):
            if key[0] == "_" and not self.private:
                continue
            key = prefix + key + suffix

            if isinstance(value, dict):
                self.print_kwdict(value, f"{key}['", markers)

            elif isinstance(value, list):
                if not value:
                    pass
                elif isinstance(value[0], dict):
                    self.print_kwdict(value[0], f"{key}[N]['", markers)
                else:
                    fmt = ("  {:>%s} {}\n" % len(str(len(value)))).format
                    write(key + "[N]\n")
                    for idx, val in enumerate(value, 0):
                        write(fmt(idx, val))

            else:
                # string or number
                write(f"{key}\n  {value}\n")

        markers.remove(markerid)


class UrlJob(Job):
    """Print download urls"""
    maxdepth = 1

    def __init__(self, url, parent=None, depth=1):
        Job.__init__(self, url, parent)
        self.depth = depth
        if depth >= self.maxdepth:
            self.handle_queue = self.handle_url

    @staticmethod
    def handle_url(url, _):
        stdout_write(url + "\n")

    @staticmethod
    def handle_url_fallback(url, kwdict):
        stdout_write(url + "\n")
        if "_fallback" in kwdict:
            for url in kwdict["_fallback"]:
                stdout_write(f"| {url}" + "\n")

    def handle_queue(self, url, kwdict):
        if cls := kwdict.get("_extractor"):
            extr = cls.from_url(url)
        else:
            extr = extractor.find(url)

        if extr:
            self.status |= self.__class__(extr, self, self.depth + 1).run()
        else:
            self._write_unsupported(url)


class InfoJob(Job):
    """Print extractor defaults and settings"""

    def run(self):
        ex = self.extractor
        pm = self._print_multi
        pc = self._print_config

        if ex.basecategory:
            pm("Category / Subcategory / Basecategory",
               ex.category, ex.subcategory, ex.basecategory)
        else:
            pm("Category / Subcategory", ex.category, ex.subcategory)

        pc("Filename format", "filename", ex.filename_fmt)
        pc("Directory format", "directory", ex.directory_fmt)
        pc("Archive format", "archive-format", ex.archive_fmt)
        pc("Request interval", "sleep-request", ex.request_interval)

        return 0

    def _print_multi(self, title, *values):
        stdout_write(f'{title}\n  {" / ".join(map(util.json_dumps, values))}\n\n')

    def _print_config(self, title, optname, value):
        optval = self.extractor.config(optname, util.SENTINEL)
        if optval is not util.SENTINEL:
            stdout_write(
                f"{title} (custom):\n  {util.json_dumps(optval)}\n{title} (default):\n  {util.json_dumps(value)}\n\n"
            )
        elif value:
            stdout_write(f"{title} (default):\n  {util.json_dumps(value)}\n\n")


class DataJob(Job):
    """Collect extractor results and dump them"""

    def __init__(self, url, parent=None, file=sys.stdout, ensure_ascii=True):
        Job.__init__(self, url, parent)
        self.file = file
        self.data = []
        self.ascii = config.get(("output",), "ascii", ensure_ascii)

        private = config.get(("output",), "private")
        self.filter = dict.copy if private else util.filter_dict

    def run(self):
        extractor = self.extractor
        if sleep := util.build_duration_func(extractor.config("sleep-extractor")):
            extractor.sleep(sleep(), "extractor")

        # collect data
        try:
            for msg in extractor:
                self.dispatch(msg)
        except exception.StopExtraction:
            pass
        except Exception as exc:
            self.data.append((exc.__class__.__name__, str(exc)))
        except BaseException:
            pass

        # convert numbers to string
        if config.get(("output",), "num-to-str", False):
            for msg in self.data:
                util.transform_dict(msg[-1], util.number_to_string)

        # dump to 'file'
        try:
            util.dump_json(self.data, self.file, self.ascii, 2)
            self.file.flush()
        except Exception:
            pass

        return 0

    def handle_url(self, url, kwdict):
        self.data.append((Message.Url, url, self.filter(kwdict)))

    def handle_directory(self, kwdict):
        self.data.append((Message.Directory, self.filter(kwdict)))

    def handle_queue(self, url, kwdict):
        self.data.append((Message.Queue, url, self.filter(kwdict)))
