from ...core.responses import sdk_base_url


def create_client(key, settings):
    from openai import OpenAI

    return OpenAI(
        api_key=key,
        base_url=sdk_base_url(settings.responses_url),
        max_retries=0,
        timeout=600,
    )
