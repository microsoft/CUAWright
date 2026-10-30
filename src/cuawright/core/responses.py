from urllib.parse import urlsplit, urlunsplit


def responses_url(value: str) -> str:
    endpoint = urlsplit(value)
    if (
        endpoint.scheme not in ("http", "https")
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
        or any(character.isspace() for character in value)
        or endpoint.path.rstrip("/").split("/")[-1] != "responses"
    ):
        raise ValueError(
            "responses URL must be an HTTP URL ending in /responses without credentials"
        )
    return urlunsplit(
        (endpoint.scheme, endpoint.netloc, endpoint.path.rstrip("/"), "", "")
    )


def sdk_base_url(value: str) -> str:
    endpoint = urlsplit(responses_url(value))
    path = endpoint.path.rsplit("/", 1)[0] or "/"
    return urlunsplit((endpoint.scheme, endpoint.netloc, path, "", ""))
