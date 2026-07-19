import re

from .errors import MbError


def split_frontmatter(source: str) -> tuple[str, str]:
    if not source.startswith("---\n"):
        raise MbError("wiki page must start with YAML frontmatter", 4)
    end = source.find("\n---\n", 4)
    if end < 0:
        raise MbError("wiki page has unterminated YAML frontmatter", 4)
    return source[: end + 5], source[end + 5 :]


def scalar(frontmatter: str, key: str) -> str | None:
    matches = list(re.finditer(rf"(?m)^{re.escape(key)}:\s*(.*?)\s*$", frontmatter))
    if len(matches) > 1:
        raise MbError(f"duplicate frontmatter field: {key}", 4)
    if not matches:
        return None
    value = matches[0].group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return value


def patch_scalars(source: str, values: dict[str, str]) -> str:
    frontmatter, body = split_frontmatter(source)
    result = frontmatter
    for key, value in values.items():
        if "\n" in value:
            raise MbError(f"frontmatter scalar must be one line: {key}", 1)
        rendered = f'{key}: "{value.replace(chr(34), chr(92) + chr(34))}"'
        pattern = re.compile(rf"(?m)^{re.escape(key)}:\s*.*$")
        matches = list(pattern.finditer(result))
        if len(matches) > 1:
            raise MbError(f"duplicate frontmatter field: {key}", 4)
        if matches:
            result = pattern.sub(rendered, result, count=1)
        else:
            closing = result.rfind("---\n")
            result = result[:closing] + rendered + "\n" + result[closing:]
    return result + body
