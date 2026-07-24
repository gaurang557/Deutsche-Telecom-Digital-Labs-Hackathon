from app.voice.stt import collapse_repeated_sentences


def test_collapses_consecutive_duplicate_sentences() -> None:
    transcript = (
        "Open Bing.com in Google Chrome. "
        "Open Bing.com in Google Chrome."
    )

    assert collapse_repeated_sentences(transcript) == (
        "Open Bing.com in Google Chrome."
    )


def test_preserves_distinct_sentences() -> None:
    transcript = "Open Chrome. Then open Bing."

    assert collapse_repeated_sentences(transcript) == transcript
