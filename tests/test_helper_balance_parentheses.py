from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import helper


def test_single_arg_unquoted_is_wrapped_as_one_string():
    assert helper.balance_parentheses("send Hello world") == '((send "Hello world"))'


def test_escaped_quotes_are_preserved():
    text = 'send "He said \\"hi\\""'
    assert helper.balance_parentheses(text) == '((send "He said \\"hi\\""))'


def test_two_arg_file_command_is_parsed_stably():
    text = 'write-file "notes.txt" "line one"'
    assert helper.balance_parentheses(text) == '((write-file "notes.txt" "line one"))'


def test_multiline_commands_are_preserved():
    text = 'search omega\nremember "abc"'
    assert helper.balance_parentheses(text) == '((search "omega") (remember "abc"))'


def test_placeholder_tokens_are_decoded():
    assert helper.balance_parentheses("send _quote_hi_quote_") == '((send "hi"))'
