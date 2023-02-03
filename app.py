import itertools
import json
import os
import sys
import traceback
from datetime import datetime
from glob import glob
from shutil import copyfile, get_terminal_size
from shlex import quote
from urllib.request import urlopen
from threading import Event
from subprocess import Popen, PIPE, DEVNULL, CalledProcessError
from concurrent.futures import ThreadPoolExecutor, Future
from html import escape

import constants as C
from book import Book, Chapter
from util import ffm_escape, ms_to_fftime

class OperationCancelled(Exception):
    pass

#FIXME: support chapter splitting again
def construct_decode_command(book:Book, quality:C.Quality, chapter:Chapter=None):
    quality_args = ('-ac', '1') if quality == C.Quality.MONO_VOICE else ()
    return (*C.FF_CMD, '-audible_key', book.key,
                       '-audible_iv', book.iv,
                       '-i', book.aaxc_path,
                       '-ss', ms_to_fftime(book.input_start_offset),
                       '-t', ms_to_fftime(book.output_duration),
                       '-map_metadata', '-1',
                       *quality_args,
                       '-f', 'wav',
                       '-')

def construct_encode_command(book:Book, quality:C.Quality, container:C.Container, chapter:Chapter=None):
    meta_args = []
    if container == C.Container.OGG:
        for key, value in book.metadata.items():
            if key in ('title', 'artist', 'genre', 'date'):
                meta_args += (f'--{key}', f'{value}')
            else:
                meta_args += ('--comment', f'{key}={value}')

        if book.cover_file:
            meta_args += ('--picture', book.cover_file)

        for c in book.chapters:
            for arg in c.get_metadata(C.Container.OGG):
                meta_args += ('--comment', f'{arg}')

    mode = ('--speech', )
    match quality:
        case C.Quality.MONO_VOICE:
            br = '32'
        case C.Quality.STEREO_VOICE:
            br = '48'
        case C.Quality.STEREO:
            br = '64'
            mode = ()
    
    args = ('opusenc', '--quiet',
                       '--bitrate', f'{br}k',
                       *mode,
                       *meta_args,
                       '-',
                       f'{book.output_filename}.opus')

    return args

class App:
    def __init__(self, args, use_nested_chapter_names=False):
        self.quiet = args.quiet

        if not os.path.isdir(args.output):
            self.print(f'Error: output is not a directory: {args.output}')
            sys.exit(1)

        if os.path.isdir(args.inputs[0]):
            input_files = glob(f'{args.inputs[0]}/*.aaxc')
            if input_files:
                if len(args.inputs) > 1:
                    self.print('Warning: ignoring additional inputs in directory input mode')
                args.inputs = input_files
            else:
                self.print(f'Error: input directory contains no aaxc files: {args.inputs[0]}')
                sys.exit(1)
        else:
            for file in args.inputs:
                if not os.path.isfile(file):
                    print(f'Error: input file not found: {file}')
                    sys.exit(1)

        self._progress_iterator = itertools.cycle(('—', '|'))
        self._executor = ThreadPoolExecutor()
        self._running = False
        self._books = []
        self._active_jobs = 0
        self._failed_jobs = 0
        self._n_jobs = 0
        self._cancel_event = Event()
        self._last_print_was_progress = False

        self.input_files = args.inputs
        self.output_dir = args.output
        self.container = args.container
        self.quality = args.quality
        self.max_threads = args.threads
        self.use_nested_chapter_names = use_nested_chapter_names

    def _transcode_book(self, book: Book) -> str:
        #ensure output dir
        res = os.stat(book.output_base_directory)
        os.makedirs(book.output_directory, mode=res.st_mode, exist_ok=True)

        decode_command = construct_decode_command(book, self.quality)
        encode_command = construct_encode_command(book, self.quality, self.container)

        with Popen(args=encode_command, bufsize=C.TRANSCODE_BUF_SIZE, stdin=PIPE, stdout=DEVNULL, stderr=DEVNULL) as encoder:
            with Popen(args=decode_command, bufsize=C.TRANSCODE_BUF_SIZE, stdin=DEVNULL, stdout=PIPE, stderr=DEVNULL) as decoder:
                while decoder.poll() == None:
                    if self.cancelled:
                        raise OperationCancelled()
                    encoder.stdin.write(decoder.stdout.read(C.TRANSCODE_CHUNK_SIZE))
                    encoder.stdin.flush()
                if decoder.returncode != 0:
                    raise CalledProcessError(decoder.returncode, decode_command)

            encoder.stdin.close()
            while encoder.poll() == None:
                if self.cancellable_sleep():
                    raise OperationCancelled()
            if encoder.returncode != 0:
                raise CalledProcessError(encoder.returncode, encode_command)

        return f'{book.output_filename}.opus'

    def _remux_book(self, book: Book, transcoded_file: str):
        if self.cancelled:
            raise OperationCancelled()

        temp_files = []
        output_file = f'{book.output_filename}'
        cover_file  = f'{book.output_directory}/cover.jpg'

        match self.container:
            case C.Container.MP4:
                output_file += '.m4b'
                ffmetadata_file = f'{book.output_directory}/ffmetadata'
                tags = (f'{k}={ffm_escape(v)}' for k,v in book.metadata.items())
                chapters = (c.get_metadata(C.ChapterFormat.FFMPEG) for c in book.chapters)
                ffmetadata = C.FFMETADATA_FMT.format(tags='\n'.join(tags),
                                                     chapters='\n'.join(chapters))
                temp_files.append((ffmetadata_file, ffmetadata))
                remux_cmd = (*C.FF_CMD, '-i', transcoded_file,
                                        '-i', ffmetadata_file,
                                        '-map_metadata', '1',
                                        '-codec', 'copy',
                                        '-f', 'mp4',
                                        output_file)
            case C.Container.WEBM:
                output_file += 'webm'
                tags_file    = f'{book.output_directory}/tags'
                chapter_file = f'{book.output_directory}/chapters'
                tags = (C.MATROSKA_TAG_SIMPLE_FMT.format(key=k, value=escape(v)) for k,v in book.metadata.items())
                tags_xml = C.MATROSKA_TAG_XML_FMT.format(tags='\n'.join(tags))
                chapters = (c.get_metadata(C.ChapterFormat.MATROSKA) for c in book.chapters)
                chapters_xml = C.MATROSKA_CHAPTERS_XML_FMT.format(atoms='\n'.join(chapters))
                temp_files.append((tags_file, tags_xml))
                temp_files.append((chapter_file, chapters_xml))
                remux_cmd = ("mkvmerge", "-o", output_file,
                                          "--webm",
                                          "--quiet",
                                          "--global-tags", tags_file,
                                          "--chapters", chapter_file,
                                          transcoded_file)
            case other:
                return transcoded_file

        for file, content in temp_files:
            if self.cancelled:
                raise OperationCancelled()
            with open(file, 'w') as f:
                f.writelines(content)

        self.cancellable_exec(remux_cmd)

        for file, _ in temp_files:
            os.remove(file)

        os.remove(transcoded_file)
        copyfile(book.cover_file, cover_file)

        return output_file

    def _process_book(self, book: Book):
        with urlopen(f'https://api.audnex.us/books/{book.asin}') as data:
            meta = json.load(data)

        if self.cancelled:
            raise OperationCancelled()

        book.import_metadata(meta)

        return self._remux_book(book, self._transcode_book(book))

    def _future_done_cb(self, future: Future):
        self._active_jobs -= 1
        if self.cancelled:
            return
        
        exc = future.exception()
        msg = ''
        if exc:
            #can't avoid broken pipe error upon interruption during stdio
            if isinstance(exc, (OperationCancelled, BrokenPipeError)):
                pass
            else:
                self._failed_jobs += 1
                if isinstance(exc, CalledProcessError):
                    cmd = ' '.join(quote(arg) for arg in exc.cmd)
                    msg = f'Exec failed with code {exc.returncode}: "{cmd}"'
                else:
                    msg = f'Task failed successfully:\n{traceback.format_exception(exc)}'
        elif future.result():
            msg = future.result()
        
        if msg:
            self.print(msg)

    @property
    def cancelled(self):
        return self._cancel_event.is_set()

    @property
    def running(self):
        return self._running

    def cancel(self):
        if self.cancelled or not self.running:
            return
        self.print('\nCancelling, please wait…\n')
        self._cancel_event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def cancellable_exec(self, *args):
        if self.cancelled:
            raise OperationCancelled()
        with Popen(args=args, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL) as process:
            while process.poll() == None:
                if self.cancellable_sleep():
                    raise OperationCancelled()
            if process.returncode:
                raise CalledProcessError(process.returncode, process.args)

    def cancellable_sleep(self, duration=C.POLLING_INTERVAL) -> bool:
        if duration == 0:
            return self.cancelled
        return self._cancel_event.wait(duration)

    def print(self, *args, **kwargs):
        self._print(*args, progress=False, **kwargs)

    def _print(self, *args, progress=False, **kwargs):
        if self.quiet:
            return
        term_width, _ = get_terminal_size()
        reprint_progress = False
        if progress:
            bar_width = term_width / 4
            bar_width = round(bar_width)
            n_done  = self.n_jobs - self._active_jobs - len(self._books)
            percent = n_done * 100 / self.n_jobs
            bar = '|' * int(percent / 100 * bar_width - 1) + next(self._progress_iterator)
            args = (f'{f"Progress: {n_done}/{self.n_jobs} [{bar:—<{bar_width}s}] {percent:.2f}%":{term_width}s}', )
            kwargs = {'end': '\r'}
            self._last_print_was_progress = True
        else:
            if self._last_print_was_progress:
                print(f'{" ":{term_width}s}', end='\r')
            self._last_print_was_progress = False
            reprint_progress = self.running and not self.cancelled
        print(*args, **kwargs)
        if reprint_progress:
            self._print(progress=True)

    def run(self):
        if self.running:
            return
        start_time = datetime.now()
        self._running = True
        for aaxc in self.input_files:
            b = Book(aaxc, self.output_dir)
            self._books.append(b)
        self._books.sort(key=lambda b: b.input_duration)

        progress_loops = C.PROGRESS_INTERVAL / C.POLLING_INTERVAL
        self.n_jobs = len(self._books)

        self.print(f'Enqueued {self.n_jobs} jobs at: {start_time}')

        i = progress_loops
        while not self.cancellable_sleep():
            if i >= progress_loops:
                i = 0
                self._print(progress=True)
            i += 1

            if not self.cancelled:
                if self._active_jobs < self.max_threads and len(self._books):
                    self._active_jobs += 1
                    self._executor.submit(self._process_book, self._books.pop()) \
                                  .add_done_callback(self._future_done_cb)

                if not self._active_jobs:
                    self._running = False
                    break

        if self.cancelled:
            sys.exit(1)

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        if self._failed_jobs:
            status = f'with {self._failed_jobs} failure{"s" if self._failed_jobs > 1 else ""}'
        else:
            status = 'successfully'

        self.print(f'Finished at {end_time} {status}, elapsed: {duration:.3f}s')

        return 1 if self._failed_jobs else 0