import html
import json
import os
from dataclasses import dataclass
from glob import glob

import constants as C
from util import clean_filename, clean_text, ffm_escape, ms_to_fftime

@dataclass
class Chapter:
    '''Represents a single chapter within a Book()'''
    index: int
    '''chapter index'''
    title: str
    '''chapter title'''
    duration: int
    '''chapter duration'''
    input_offset: int
    '''start offset relative to Book() input file'''
    output_offset: int
    '''start offset relative to Book() output file'''


    def get_metadata(self, container=C.Container.OGG) -> any:
        match container:
            case C.Container.MP4:
                return C.FFMPEG_CHAPTER_FMT.format(start=self.output_offset,
                                                   end=self.output_offset + self.duration,
                                                   title=ffm_escape(self.title))

            case C.Container.WEBM:
                return C.MATROSKA_CHAPTER_ATOM_FMT.format(uid=self.index+1,
                                                          start=ms_to_fftime(self.output_offset),
                                                          end=ms_to_fftime(self.output_offset + self.duration),
                                                          title=html.escape(self.title))
            case C.Container.OGG:
                return (f'CHAPTER{self.index:03d}={ms_to_fftime(self.output_offset)}',
                        f'CHAPTER{self.index:03d}NAME={self.title}')
            case other:
                return None

class Book:
    def __init__(self, aaxc_path: str, output_directory: str) -> None:
        (location, filename) = os.path.split(aaxc_path)
        (filename, ext) = os.path.splitext(filename)

        with open(f'{location}/{filename}.voucher', 'r') as vf:
            voucher = json.load(vf)['content_license']

        content_reference = voucher['content_metadata']['content_reference']

        filename_suffix = f"-{content_reference['content_format']}"
        _, sr, br = filename_suffix.split('_')

        self._use_combined_chapter_names = False #FIXME
        '''whether to combine nested chapter parent title with the child's title'''

        self.aaxc_path = aaxc_path
        '''path to the input aaxc file'''
        self.input_sample_rate = 22050 if sr == '22' else 44100 if sr == '44' else None
        '''aaxc sample rate'''
        self.input_bit_rate = int(br)
        '''aaxc bit rate'''
        self.input_duration = None
        '''input file duration'''
        self.input_start_offset = None
        '''input file audible intro length'''
        self.input_end_offset = None
        '''input file audible outro length'''
        self.key = voucher['license_response']['key']
        '''aaxc enctrption key'''
        self.iv = voucher['license_response']['iv']
        '''aaxc encryption initialization vector'''
        self.asin = voucher['asin']
        '''the audible asin for this aaxc'''
        self.input_base_filename = f"{location}/{filename.replace(filename_suffix, '')}"
        '''common filename prefix between aaxc, voucher, chapters, and cover files'''

        pic = glob(f'{self.input_base_filename}_(*).jpg')

        self.cover_file = pic[-1] if pic else None
        '''cover jpg file associated with this aaxc, if any'''
        self.metadata = {'asin': self.asin}
        '''dict containing metadata tags for the output file(s), only contains 'asin' until metadata import'''

        self.output_base_directory = output_directory
        '''base path for the output file(s) not including any metadata-based directory names'''
        self.output_duration = None
        '''output file duration'''
        self.output_filename = None
        '''output filename without an extension, only valid after metadata import'''
        self.output_directory = None
        '''full destination output directory, only valid after metadata import'''
        self.chapters = self._load_chapters()
        '''a tuple containing Chapter() entries'''

    def _load_chapters(self):
        with open(f'{self.input_base_filename}-chapters.json','r') as cf:
            chapters_json = json.load(cf)['content_metadata']['chapter_info']

        self.input_start_offset = int(chapters_json['brandIntroDurationMs'])
        self.input_end_offset   = int(chapters_json['brandOutroDurationMs'])
        self.input_duration     = int(chapters_json['runtime_length_ms'])
        self.output_duration    = self.input_duration - self.input_start_offset - self.input_end_offset

        # for chapters mapping source to output:
        # -first chapter must be offset by start offset
        # for chapters referencing output file:
        # -first chapter must start at 0
        # -all other chapters must be offset by start offset
        # both:
        # -first chapter duration must be shortened by start offset
        # -last chapter duration must be shortened by end trim
        def flatten(node: dict, prefix: str = '', chapter_list: list[Chapter] = []):
            #FIXME: matroska supports this natively
            #Handles recursively traversing the chapter tree when each book has it's own chapter heading. Produces
            # output like "Book 2: Chapter 3" instead of having multiple "Chapter 3" in a single file if use_combined_chapter_names
            # is True. Multi-book files don't always have nested or even per-book chapters.
            for item in node:
                index = int(len(chapter_list))
                title = f"{prefix}{clean_text(item['title'])}"
                offset = int(item['start_offset_ms'])
                duration = int(item['length_ms'])
                if index == 0:
                    duration -= self.input_start_offset
                    chapter_list.append(Chapter(index, title, duration, self.input_start_offset, 0))
                else:
                    chapter_list.append(Chapter(index, title, duration, offset, offset - self.input_start_offset))
                if 'chapters' in item:
                    flatten(item['chapters'], f'{title}: ' if self._use_combined_chapter_names else '', chapter_list)
            return chapter_list

        chapter_list = flatten(chapters_json['chapters'])
        chapter_list[-1].duration -= self.input_end_offset

        return tuple(chapter_list)

    def import_metadata(self, js: dict) -> None:
        # sort mononyms last to work around "lastname, firstname" detection in abs
        authors, mononym_authors = [], []
        for author in js['authors']:
            name = clean_text(author['name'])
            if name in C.NAMES_REMOVE:
                continue
            for args in C.NAMES_REPLACE:
                name = name.replace(*args)
            if '\u0020' in name:
                authors.append(name)
            else:
                mononym_authors.append(name)

        narrators = [clean_text(n['name']) for n in js['narrators']]
        narrators.sort(key=lambda n: '\u0020' not in n)

        # 'tags' is not imported by abs, but we don't want to combine it with genres
        genre, tags = [], []
        for g in js['genres']:
            name = clean_text(g['name'])
            match g['type']:
                case 'genre':
                    genre.append(name)
                case 'tag':
                    tags.append(name)

        authors = C.DELIM_NAME.join(authors + mononym_authors)
        narrators = C.DELIM_NAME.join(narrators)
        genre = C.DELIM_GENRE.join(genre)
        tags = C.DELIM_GENRE.join(tags)
        
        self.metadata.update({
            'language': clean_text(js['language']),
            'artist': authors,
            'composer': narrators,
            'genre': genre,
            'tags': tags,
            'date': js['releaseDate'][:10],
            'title': clean_text(js['title']),
            'description': clean_text(js['summary']),
            'publisher': clean_text(js['publisherName'])
        })

        conditional_meta = {}
        if 'seriesPrimary' in js and 'position' in js['seriesPrimary']:
            conditional_meta['series'] = clean_text(js['seriesPrimary']['name']).replace(',',' -')
            conditional_meta['series-part'] = clean_text(js['seriesPrimary']['position'])
        if 'subtitle' in js:
            conditional_meta['subtitle'] = clean_text(js['subtitle'])
        self.metadata.update(conditional_meta)

        filename_prefix = clean_filename(self.metadata['title'])
        self.output_directory = f'{self.output_base_directory}/{clean_filename(authors)}/{filename_prefix}'
        self.output_filename = f'{self.output_directory}/{filename_prefix}'
