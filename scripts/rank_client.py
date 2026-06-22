def fetch_rank(session, url, payload, timeout_seconds=180, attempts=2):
    last_error = None
    for attempt in range(max(1, attempts)):
        try:
            response = session.post(url, json=payload, timeout=timeout_seconds)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("rank endpoint returned non-object JSON")
            return data
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= max(1, attempts):
                raise
    raise last_error
