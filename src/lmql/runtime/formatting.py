"""
Formatting of values in a prompt context.
"""
from lmql.api.blobs import Blob
from lmql.runtime.context import Context

def unescape(s):
    return str(s).replace("[", "[[").replace("]", "]]")

def is_chat_list(l):
    if not isinstance(l, list):
        return False
    if any(not isinstance(x, dict) for x in l):
        return False

    # keys can be role and content
    return all(x in ["role", "content"] for x in l[0].keys())

def format_chat(chat):
    """
    Formats a list of dicts representing an OpenAI Chat model
    input into an LMQL-compliant prompt string using <lmql:ROLE/> tags.
    """
    return "".join(f"<lmql:{m['role']}/>{m['content']}" for m in chat)

def tag(t):
    return f"<lmql:{t}/>"

def format(s):
    """
    Formats a value for insertion into an LMQL query string, i.e. the LLM prompt.
    """
    if is_chat_list(s):
        return format_chat(s)

    return tag(f"media id='{s.id}'") if isinstance(s, Blob) else unescape(s)