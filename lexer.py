from dataclasses import dataclass
from enum import Enum, auto


class TK(Enum):
    IDENT = auto()
    KEYWORD = auto()
    NUMBER = auto()
    STRING = auto()
    LPAREN = auto()
    RPAREN = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    DOT = auto()
    COMMA = auto()
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    AMP = auto()      # &     bitwise AND
    PIPE = auto()     # |     bitwise OR
    CARET = auto()    # ^     bitwise XOR
    TILDE = auto()    # ~     bitwise NOT
    SHL = auto()      # <<    shift left
    SHR = auto()      # >>    shift right
    NEWLINE = auto()
    EOF = auto()


@dataclass
class Token:
    kind: TK
    start: int
    end: int


KEYWORDS = {
    "set", "to", "as",
    "add", "subtract", "multiply", "divide", "by", "from",
    "plus", "minus", "times", "divided",
    "is", "equal", "not", "greater", "less", "than", "at", "least", "most",
    "if", "else", "end",
    "repeat", "for", "each", "in", "while",
    "stop", "skip",
    "define", "function", "record", "input", "output", "return",
    "call", "with", "and",
    "new", "empty", "list", "map", "matrix", "of",
    "append",
    "length", "rows", "columns",
    "print",
    "true", "false", "none",
    "bit_and", "bit_or", "bit_not",
    "xor", "shifted", "left", "right",
}


class LexError(Exception):
    def __init__(self, message: str, position: int):
        self.position = position
        super().__init__(f"{message} at position {position}")


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(source)

    while i < n:
        c = source[i]

        if c == " " or c == "\t" or c == "\r":
            i += 1
            continue

        if c == "\n":
            tokens.append(Token(TK.NEWLINE, i, i + 1))
            i += 1
            continue

        if c == "#":
            while i < n and source[i] != "\n":
                i += 1
            continue

        if c.isalpha() or c == "_":
            start = i
            while i < n and (source[i].isalnum() or source[i] == "_"):
                i += 1
            text = source[start:i]
            kind = TK.KEYWORD if text in KEYWORDS else TK.IDENT
            tokens.append(Token(kind, start, i))
            continue

        if c.isdigit():
            start = i
            while i < n and source[i].isdigit():
                i += 1
            if i + 1 < n and source[i] == "." and source[i + 1].isdigit():
                i += 1
                while i < n and source[i].isdigit():
                    i += 1
            tokens.append(Token(TK.NUMBER, start, i))
            continue

        if c == '"':
            start = i
            i += 1
            while i < n and source[i] != '"':
                if source[i] == "\\" and i + 1 < n:
                    i += 2
                else:
                    i += 1
            if i >= n:
                raise LexError("unterminated string", start)
            i += 1
            tokens.append(Token(TK.STRING, start, i))
            continue

        # Two-char symbols first (so `<<` doesn't get split into two separate
        # tokens and confused with comparison-like sequences).
        if c == "<" and i + 1 < n and source[i + 1] == "<":
            tokens.append(Token(TK.SHL, i, i + 2))
            i += 2
            continue
        if c == ">" and i + 1 < n and source[i + 1] == ">":
            tokens.append(Token(TK.SHR, i, i + 2))
            i += 2
            continue

        single = {
            "(": TK.LPAREN,
            ")": TK.RPAREN,
            "[": TK.LBRACKET,
            "]": TK.RBRACKET,
            ".": TK.DOT,
            ",": TK.COMMA,
            "+": TK.PLUS,
            "-": TK.MINUS,
            "*": TK.STAR,
            "/": TK.SLASH,
            "&": TK.AMP,
            "|": TK.PIPE,
            "^": TK.CARET,
            "~": TK.TILDE,
        }
        if c in single:
            tokens.append(Token(single[c], i, i + 1))
            i += 1
            continue

        raise LexError(f"unexpected character {c!r}", i)

    tokens.append(Token(TK.EOF, n, n))
    return tokens


def line_column(source: str, position: int) -> tuple[int, int]:
    line = 1
    col = 1
    for k in range(min(position, len(source))):
        if source[k] == "\n":
            line += 1
            col = 1
        else:
            col += 1
    return line, col
