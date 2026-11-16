"""The same 2000-character Markdown excerpt extraction used for training."""
import html
import re


def clean_markdown(text):
    kept, skip_level = [], None
    for line in text.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.*)", line)
        if heading:
            level, title = len(heading[1]), heading[2]
            if skip_level is not None and level <= skip_level:
                skip_level = None
            if re.search(r"팀원|팀 소개|멤버|회고|느낀\s*점|연락처|contributors|team members|contact us", title, re.I):
                skip_level = level
        if skip_level is None:
            kept.append(line)
    value = "\n".join(kept)
    value = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", value, flags=re.S)
    value = re.sub(r"~~~.*?~~~", "", value, flags=re.S)
    value = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[email removed]", value)
    value = re.sub(r"(?<!\d)(?:\+82[- ]?)?0?1[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)", "[phone removed]", value)
    value = re.sub(r"\{\{.*?\}\}", "", value, flags=re.S)
    value = re.sub(r"```.*?```", "", value, flags=re.S)
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"https?://\S+", "", value)
    lines = []
    for line in html.unescape(value).splitlines():
        line = line.strip()
        if not line or line.startswith((".. ", "|", "[!", "[![")):
            continue
        if set(line) <= set("=-_#* "):
            continue
        line = re.sub(r"^[#*> ]+", "", line)
        if len(line) >= 12:
            lines.append(line)
    return "\n".join(lines)[:2000]
