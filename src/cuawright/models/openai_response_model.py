def create_client(key, settings):
    from openai import OpenAI

    return OpenAI(
        api_key=key,
        base_url=settings.base_url,
        max_retries=0,
        timeout=600,
    )
