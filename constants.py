from enum import Enum, auto

from util import StrEnum

DEFAULT_THREAD_LIMIT = 4
'''Default max simultaneous transcode/mux jobs'''

DELIM_NAME = ','
'''Name-type metadata field delimiter'''
DELIM_GENRE = ';'
'''Genre-type metadata field delimiter'''

#some specific jank to remove
NAMES_REMOVE = (
    'The Great Courses'
)
'''Exact name matches to remove entirely'''
NAMES_REPLACE = (   
    (' - introductions', ''),
    ('James S.A. Corey', 'James S. A. Corey')
)
'''(string, replace) pairs to replace within names'''

FF_CMD = ('ffmpeg', '-loglevel', 'error')

TRANSCODE_BUF_SIZE = 0
'''Python subprocess pipe buffer size for the transcode job'''
TRANSCODE_CHUNK_SIZE = 16*1024
'''In-app "pipe buffer" size for transocde'''
POLLING_INTERVAL = 1/10
'''General cancel check polling interval in seconds'''
PROGRESS_INTERVAL = 1
'''Progress printing interval in seconds'''

FFMETADATA_FMT = \
''';FFMETADATA1
{tags}
{chapters}
'''

FFMPEG_CHAPTER_FMT = \
'''[CHAPTER]
TIMEBASE=1/1000
START={start}
END={end}
TITLE={title}'''

MATROSKA_TAG_XML_FMT = \
'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Tags SYSTEM "matroskatags.dtd">
<Tags>
{tags}
</Tags>
'''

MATROSKA_TAG_SIMPLE_FMT = \
'''  <Tag>
      <Simple>
        <Name>{key}</Name>
        <String>{value}</String>
      </Simple>
  </Tag>'''

MATROSKA_CHAPTERS_XML_FMT = \
"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Chapters SYSTEM "matroskachapters.dtd">
<Chapters>
  <EditionEntry>
{atoms}
  </EditionEntry>
</Chapters>
"""

MATROSKA_CHAPTER_ATOM_FMT = \
'''    <ChapterAtom>
      <ChapterUID>{uid}</ChapterUID>
      <ChapterTimeStart>{start}</ChapterTimeStart>
      <ChapterTimeEnd>{end}</ChapterTimeEnd>
      <ChapterDisplay>
        <ChapterString>{title}</ChapterString>
      </ChapterDisplay>
    </ChapterAtom>'''


class Container(StrEnum):
    '''Output container format'''
    MP4 = auto()
    '''opus in mp4 container, "m4b" file extension'''
    OGG = auto()
    '''opus in ogg container, "opus" file extension'''
    WEBM = auto()
    '''opus in webm container, "webm" file extension'''

class Quality(StrEnum):
    '''Output opus quality setting'''
    MONO_VOICE = auto()
    '''mono 32k voice mode'''
    STEREO_VOICE = auto()
    '''stereo 48k voice mode'''
    STEREO = auto()
    '''stereo 64k auto'''

class ChapterFormat(Enum):
    '''Chapter metadata format type'''
    FFMPEG = auto()
    '''ffmpeg ffmetadata chapters: str'''
    MATROSKA = auto()
    '''mkv/webm XML chapter atoms: str'''
    VORBIS = auto()
    '''ogg vorbis comment key=value sets: tuple[str, str]'''
