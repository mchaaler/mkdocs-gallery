#  Authors: Sylvain MARIE <sylvain.marie@se.com>
#            + All contributors to <https://github.com/smarie/mkdocs-gallery>
#
#  Original idea and code: sphinx-gallery, <https://sphinx-gallery.github.io>
#  License: 3-clause BSD, <https://github.com/smarie/mkdocs-gallery/blob/master/LICENSE>
"""
Parser for python source files
"""

from __future__ import division, absolute_import, print_function

from typing import List, Dict, Tuple, Union

from pathlib import Path

import ast
from packaging.version import Version, parse
from io import BytesIO
import re
import platform
import tokenize
from textwrap import dedent

from .errors import ExtensionError
from .mkdocs_compatibility import getLogger

logger = getLogger('mkdocs-gallery')

SYNTAX_ERROR_DOCSTRING = """
SyntaxError
===========

Example script with invalid Python syntax
"""

# The pattern for in-file config comments is designed to not greedily match
# newlines at the start and end, except for one newline at the end. This
# ensures that the matched pattern can be removed from the code without
# changing the block structure; i.e. empty newlines are preserved, e.g. in
#
#     a = 1
#
#     # mkdocs_gallery_thumbnail_number = 2
#
#     b = 2
INFILE_CONFIG_PATTERN = re.compile(
    r"^[\ \t]*#\s*mkdocs_gallery_([A-Za-z0-9_]+)(\s*=\s*(.+))?[\ \t]*\n?",
    re.MULTILINE)


def parse_source_file(file: Path):
    """Parse source file into AST node.

    Parameters
    ----------
    file : Path
        File path

    Returns
    -------
    node : AST node
    content : utf-8 encoded string
    """
    # with codecs.open(filename, 'r', 'utf-8') as fid:
    #     content = fid.read()
    content = file.read_text(encoding="utf-8")

    # change from Windows format to UNIX for uniformity
    content = content.replace('\r\n', '\n')

    try:
        node = ast.parse(content)
        return node, content
    except SyntaxError:
        return None, content


def _get_docstring_and_rest(file: Path):
    """Separate ``filename`` content between docstring and the rest.

    Strongly inspired from ast.get_docstring.

    Parameters
    ----------
    file : Path
        The source file

    Returns
    -------
    docstring : str
        docstring of ``filename``
    rest : str
        ``filename`` content without the docstring
    lineno : int
        The line number.
    node : ast Node
        The node.
    """
    node, content = parse_source_file(file)

    if node is None:
        return SYNTAX_ERROR_DOCSTRING, content, 1, node

    if not isinstance(node, ast.Module):
        raise ExtensionError("This function only supports modules. "
                             "You provided {0}"
                             .format(node.__class__.__name__))
    if not (node.body and isinstance(node.body[0], ast.Expr) and
            isinstance(node.body[0].value, ast.Str)):
        raise ExtensionError(
            f'Could not find docstring in file "{file}". '
            'A docstring is required by mkdocs-gallery '
            'unless the file is ignored by "ignore_pattern"')

    if parse(platform.python_version()) >= Version('3.7'):
        docstring = ast.get_docstring(node)
        assert docstring is not None  # noqa  # should be guaranteed above
        # This is just for backward compat
        if len(node.body[0].value.s) and node.body[0].value.s[0] == '\n':
            # just for strict backward compat here
            docstring = '\n' + docstring
        ts = tokenize.tokenize(BytesIO(content.encode()).readline)
        # find the first string according to the tokenizer and get its end row
        for tk in ts:
            if tk.exact_type == 3:
                lineno, _ = tk.end
                break
        else:
            lineno = 0
    else:
        # TODO this block can be removed when python 3.6 support is dropped
        docstring_node = node.body[0]
        docstring = docstring_node.value.s
        lineno = docstring_node.lineno  # The last line of the string.

    # This get the content of the file after the docstring last line
    # Note: 'maxsplit' argument is not a keyword argument in python2
    rest = '\n'.join(content.split('\n')[lineno:])
    lineno += 1
    return docstring, rest, lineno, node


def extract_file_config(content):
    """
    Pull out the file-specific config specified in the docstring.
    """
    file_conf = {}
    for match in re.finditer(INFILE_CONFIG_PATTERN, content):
        name = match.group(1)
        value = match.group(3)
        if value is None:  # a flag rather than a config setting
            continue
        try:
            value = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            logger.warning(
                'mkdocs-gallery option %s was passed invalid value %s',
                name, value)
        else:
            file_conf[name] = value
    return file_conf


def split_code_and_text_blocks(
    source_file: Union[str, Path],
    return_node=False
) -> Union[Tuple[Dict, List], Tuple[Dict, List, ast.AST]]:
    """Return list with source file separated into code and text blocks.

    Parameters
    ----------
    source_file : Union[str, Path]
        Path to the source file.
    return_node : bool
        If True, return the ast node.

    Returns
    -------
    file_conf : dict
        File-specific settings given in source file comments as:
        ``# mkdocs_gallery_<name> = <value>``
    blocks : list
        (label, content, line_number)
        List where each element is a tuple with the label ('text' or 'code'),
        the corresponding content string of block and the leading line number
    node : ast Node
        The parsed node.
    """
    source_file = Path(source_file)
    docstring, rest_of_content, lineno, node = _get_docstring_and_rest(source_file)
    blocks = [('text', docstring, 1)]

    file_conf = extract_file_config(rest_of_content)

    pattern = re.compile(r'(?P<header_line>^#{20,}.*|^# ?%%.*)\s(?P<text_content>(?:^#.*\s?)*)', flags=re.M)
    sub_pat = re.compile('^#', flags=re.M)

    pos_so_far = 0
    for match in re.finditer(pattern, rest_of_content):
        code_block_content = rest_of_content[pos_so_far:match.start()]
        if code_block_content.strip():
            blocks.append(('code', code_block_content, lineno))
        lineno += code_block_content.count('\n')

        lineno += 1  # Ignored header line of hashes.
        text_content = match.group('text_content')
        text_block_content = dedent(re.sub(sub_pat, '', text_content)).lstrip()
        if text_block_content.strip():
            blocks.append(('text', text_block_content, lineno))
        lineno += text_content.count('\n')

        pos_so_far = match.end()

    remaining_content = rest_of_content[pos_so_far:]
    if remaining_content.strip():
        blocks.append(('code', remaining_content, lineno))

    out = (file_conf, blocks)
    if return_node:
        out += (node,)
    return out


def remove_config_comments(code_block):
    """
    Return the content of *code_block* with in-file config comments removed.

    Comment lines of the pattern '# mkdocs_gallery_[option] = [val]' are
    removed, but surrounding empty lines are preserved.

    Parameters
    ----------
    code_block : str
        A code segment.
    """
    parsed_code, _ = re.subn(INFILE_CONFIG_PATTERN, '', code_block)
    return parsed_code
