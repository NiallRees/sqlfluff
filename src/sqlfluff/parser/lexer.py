"""The code for the Lexer."""

from collections import namedtuple
import re

from .markers import FilePositionMarker
from .segments_base import RawSegment
from ..errors import SQLLexError


class LexMatch(namedtuple('LexMatch', ['new_string', 'new_pos', 'segments'])):
    """A class to hold matches from the Lexer."""
    def __bool__(self):
        """A LexMatch is truthy if it contains a non-zero number of matched segments."""
        return len(self.segments) > 0


class SingletonMatcher:
    """This singleton matcher matches single characters.

    This is the simplest usable matcher, but it also defines some of the
    mechanisms for more complicated matchers, which may simply override the
    `_match` function rather than the public `match` function.  This acts as
    the base class for matchers.
    """
    def __init__(self, name, template, target_seg_class, subdivide=None, trim_post_subdivide=None, *args, **kwargs):
        self.name = name
        self.template = template
        self.target_seg_class = target_seg_class
        self.subdivide = subdivide
        self.trim_post_subdivide = trim_post_subdivide

    def _match(self, forward_string):
        """The private match function. Just look for a single character match."""
        if forward_string[0] == self.template:
            return forward_string[0]
        else:
            return None

    def _trim(self, matched, start_pos):
        """Given a string, trim if we are allowed to.

        Returns:
            :obj:`tuple` of segments

        """
        seg_buff = ()
        cont_buff = matched
        cont_pos_buff = start_pos
        idx = 0

        if self.trim_post_subdivide:
            trimmer = re.compile(self.trim_post_subdivide['regex'], re.DOTALL)
            TrimClass = RawSegment.make(
                self.trim_post_subdivide['regex'],
                name=self.trim_post_subdivide['name'],
                type=self.trim_post_subdivide['type']
            )

            for trim_mat in trimmer.finditer(matched):
                trim_span = trim_mat.span()
                # Is it at the start?
                if trim_span[0] == 0:
                    seg_buff += (
                        TrimClass(
                            raw=matched[:trim_span[1]],
                            pos_marker=cont_pos_buff
                        ),
                    )
                    idx = trim_span[1]
                    cont_pos_buff = cont_pos_buff.advance_by(matched[:trim_span[1]])
                    # Have we consumed the whole string? This avoids us having
                    # an empty string on the end.
                    if idx == len(matched):
                        break
                # Is it at the end?
                if trim_span[1] == len(matched):
                    seg_buff += (
                        self.target_seg_class(
                            raw=matched[idx:trim_span[0]],
                            pos_marker=cont_pos_buff
                        ),
                        TrimClass(
                            raw=matched[trim_span[0]:trim_span[1]],
                            pos_marker=cont_pos_buff.advance_by(cont_buff[idx:trim_span[0]])
                        ),
                    )
                    idx = len(matched)

        # Do we have anything left? (or did nothing happen)
        if idx < len(matched):
            seg_buff += (
                self.target_seg_class(
                    raw=matched[idx:],
                    pos_marker=cont_pos_buff
                ),
            )

        return seg_buff

    def _subdivide(self, matched, start_pos):
        """Given a string, subdivide if we area allowed to.

        Returns:
            :obj:`tuple` of segments

        """
        # Can we have to subdivide?
        if self.subdivide:
            # Yes subdivision
            seg_buff = ()
            str_buff = matched
            pos_buff = start_pos
            divider = re.compile(self.subdivide['regex'], re.DOTALL)
            DividerClass = RawSegment.make(
                self.subdivide['regex'],
                name=self.subdivide['name'],
                type=self.subdivide['type']
            )

            while True:
                # Iterate through subdividing as appropriate
                mat = divider.search(str_buff)
                if mat:
                    # Found a division
                    span = mat.span()
                    trimmed_segments = self._trim(str_buff[:span[0]], pos_buff)
                    div_seg = DividerClass(
                        raw=str_buff[span[0]:span[1]],
                        pos_marker=pos_buff.advance_by(str_buff[:span[0]])
                    )
                    seg_buff += trimmed_segments + (div_seg,)
                    pos_buff = pos_buff.advance_by(str_buff[:span[1]])
                    str_buff = str_buff[span[1]:]
                else:
                    # No more division matches. Trim?
                    trimmed_segments = self._trim(str_buff, pos_buff)
                    seg_buff += trimmed_segments
                    pos_buff = pos_buff.advance_by(str_buff)
                    break
            return seg_buff
        else:
            # NB: Tuple literal
            return (
                self.target_seg_class(
                    raw=matched,
                    pos_marker=start_pos),
            )

    def match(self, forward_string, start_pos):
        """Given a string, match what we can and return the rest.

        Returns:
            :obj:`LexMatch`

        """
        if len(forward_string) == 0:
            raise ValueError("Unexpected empty string!")
        matched = self._match(forward_string)

        if matched:
            # Handle potential subdivision elsewhere.
            new_segments = self._subdivide(matched, start_pos)
            return LexMatch(
                forward_string[len(matched):],
                new_segments[-1].get_end_pos_marker(),
                new_segments
            )
        else:
            return LexMatch(forward_string, start_pos, ())

    @classmethod
    def from_shorthand(cls, name, template, **kwargs):
        """A shorthand was of making new instances of this class.

        This is the primary way of defining matchers. It is convenient
        because several parameters of the matcher and the class of segment
        to be returned are shared, and here we define both together.
        """
        # Some kwargs get consumed by the class, the rest
        # are passed to the raw segment.
        class_kwargs = {}
        possible_class_kwargs = ['subdivide', 'trim_post_subdivide']
        for k in possible_class_kwargs:
            if k in kwargs:
                class_kwargs[k] = kwargs.pop(k)

        return cls(
            name, template,
            RawSegment.make(
                template, name=name, **kwargs
            ),
            **class_kwargs
        )


class RegexMatcher(SingletonMatcher):
    """This RegexMatcher matches based on regular expressions."""

    def __init__(self, *args, **kwargs):
        super(RegexMatcher, self).__init__(*args, **kwargs)
        # We might want to configure this at some point, but for now, newlines
        # do get matched by .
        flags = re.DOTALL
        self._compiled_regex = re.compile(self.template, flags)

    def _match(self, forward_string):
        """Use regexes to match chunks."""
        match = self._compiled_regex.match(forward_string)
        if match:
            return match.group(0)
        else:
            return None


class RepeatedMultiMatcher(SingletonMatcher):
    """Uses other matchers in priority order.

    Args:
        *submatchers: An iterable of other matchers which can be tried
            in turn. If none match a given forward looking string we simply
            return the unmatched part as per any other matcher.

    """

    def __init__(self, *submatchers):
        self.submatchers = submatchers

    def match(self, forward_string, start_pos):
        """Iteratively match strings using the selection of submatchers."""
        seg_buff = ()
        while True:
            if len(forward_string) == 0:
                return LexMatch(
                    forward_string,
                    start_pos,
                    seg_buff
                )
            for matcher in self.submatchers:
                res = matcher.match(forward_string, start_pos)
                if res.segments:
                    # If we have new segments then whoop!
                    seg_buff += res.segments
                    forward_string = res.new_string
                    start_pos = res.new_pos
                    # Cycle back around again and start with the top
                    # matcher again.
                    break
                else:
                    continue
            else:
                # We've got so far, but now can't match. Return
                return LexMatch(
                    forward_string,
                    start_pos,
                    seg_buff
                )

    @classmethod
    def from_struct(cls, s):
        """Creates a matcher from a lexer_struct.

        Expects an iterable of :obj:`tuple`. Each tuple should be:
        (name, type, pattern, kwargs).

        """
        matchers = []
        for elem in s:
            if elem[1] == "regex":
                m_cls = RegexMatcher
            elif elem[1] == "singleton":
                m_cls = SingletonMatcher
            else:
                raise ValueError(
                    "Unexpected matcher type in lexer struct: {0!r}".format(
                        elem[1]))
            k = elem[3] or {}
            m = m_cls.from_shorthand(elem[0], elem[2], **k)
            matchers.append(m)
        return cls(*matchers)


class Lexer:
    """The Lexer class actually does the lexing step.

    This class is likely called directly from a top level segment
    such as the `FileSegment`.
    """
    def __init__(self, config, last_resort_lexer=None):
        # config is required - we use it to get the dialect
        self.config = config
        lexer_struct = config.get('dialect_obj').get_lexer_struct()
        self.matcher = RepeatedMultiMatcher.from_struct(lexer_struct)
        self.last_resort_lexer = last_resort_lexer or RegexMatcher.from_shorthand(
            '<unlexable>', r'[^\t\n\,\.\ \-\+\*\\\/\'\"\;\:\[\]\(\)\|]*',
            is_code=True
        )

    def lex(self, raw):
        """Take a string and return segments.

        If we fail to match the *whole* string, then we must have
        found something that we cannot lex. If that happens we should
        package it up as unlexable and keep track of the exceptions.
        """
        start_pos = FilePositionMarker.from_fresh()
        segment_buff = ()
        violations = []

        while True:
            res = self.matcher.match(raw, start_pos)
            segment_buff += res.segments
            if len(res.new_string) > 0:
                violations.append(SQLLexError(
                    "Unable to lex characters: '{0!r}...'".format(
                        res.new_string[:10]),
                    pos=res.new_pos
                ))
                resort_res = self.last_resort_lexer.match(
                    res.new_string, res.new_pos
                )
                if not resort_res:
                    # If we STILL can't match, then just panic out.
                    raise violations[-1]

                raw = resort_res.new_string
                start_pos = resort_res.new_pos
                segment_buff += resort_res.segments
            else:
                break
        return segment_buff, violations
