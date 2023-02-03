import argparse
import re
from enum import Enum

def clean_filename(input=''):
    '''Removes some characters that are unsupported on various filesystems'''
    return re.sub(r'[/\\?%*:|"<>]', '', input)

def clean_text(input=''):
    '''This is a hackjob solution to remove html and various formatting/whitespace chars'''
    out = re.sub(r'<\/?[\w\s]*>|<.+[\W]>', '', input)
    out = re.sub(r'\u202F|\u00A0|\s/\s|\\n|\s\s+', '\u0020', out).strip()
    return out.replace('\u2019', '\u0027')

def ffm_escape(input=''):
    '''Escape text to conform to ffmetadata escaping rules'''
    for s in (r'\=;#'):
        input = input.replace(s, f'\\{s}')
    return input.replace('\n', r'\\n')

def ms_to_fftime(milliseconds=0):
    '''Return hh:mm:ss.fff format from milliseconds'''
    (s, ms) = divmod(milliseconds, 1000)
    (m, s) = divmod(s, 60)
    (h, m) = divmod(m, 60)
    return f'{h:02}:{m:02}:{s:02}.{ms:03d}'

class StrEnum(Enum):
    def __str__(self):
        return self.value

    @staticmethod
    def _generate_next_value_(name, start, count, last_values):
        #ENUM_MEMBER.value = "enum-member"
        return name.lower().replace('_', '-')

# https://stackoverflow.com/a/60750535
class EnumAction(argparse.Action):
    """
    Argparse action for handling Enums
    """
    def __init__(self, **kwargs):
        # Pop off the type value
        enum_type = kwargs.pop("type", None)

        # Ensure an Enum subclass is provided
        if enum_type is None:
            raise ValueError("type must be assigned an Enum when using EnumAction")
        if not issubclass(enum_type, Enum):
            raise TypeError("type must be an Enum when using EnumAction")

        # Generate choices from the Enum
        kwargs.setdefault("choices", tuple(e.value for e in enum_type))

        super(EnumAction, self).__init__(**kwargs)

        self._enum = enum_type

    def __call__(self, parser, namespace, values, option_string=None):
        # Convert value back into an Enum
        value = self._enum(values)
        setattr(namespace, self.dest, value)