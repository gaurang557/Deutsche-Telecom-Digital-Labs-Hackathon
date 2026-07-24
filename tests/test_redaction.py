from agent.redaction import redact_sensitive_data


def test_email_is_masked():
    assert redact_sensitive_data("contact bob@example.com now") == "contact <EMAIL> now"


def test_api_key_is_masked():
    text = "key is sk-abcdefgh12345678 for prod"
    assert redact_sensitive_data(text) == "key is <SECRET> for prod"


def test_bearer_token_is_masked():
    text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    assert redact_sensitive_data(text) == "Authorization: <SECRET>"


def test_card_number_is_masked_with_and_without_separators():
    assert redact_sensitive_data("card 4111111111111111 charged") == "card <CARD> charged"
    assert redact_sensitive_data("card 4111 1111 1111 1111 charged") == "card <CARD> charged"


def test_indian_phone_number_is_masked():
    assert redact_sensitive_data("call 9876543210 today") == "call <PHONE> today"
    assert redact_sensitive_data("call +91 9876543210 today") == "call <PHONE> today"


def test_sensitive_dict_keys_are_masked_regardless_of_value_type():
    data = {"password": "hunter2", "token": {"nested": "value"}, "api_key": None, "secret": 12345}
    assert redact_sensitive_data(data) == {
        "password": "<SECRET>",
        "token": "<SECRET>",
        "api_key": "<SECRET>",
        "secret": "<SECRET>",
    }


def test_long_string_is_collapsed_to_fingerprint():
    text = "a" * 2500
    result = redact_sensitive_data(text)
    assert result.startswith("<TEXT:len=2500,sha=")
    assert result.endswith(">")


def test_shape_is_preserved_for_dicts_and_lists():
    data = {
        "user": "bob@example.com",
        "attempts": [1, 2, {"password": "hunter2"}],
        "note": None,
    }
    result = redact_sensitive_data(data)
    assert result == {
        "user": "<EMAIL>",
        "attempts": [1, 2, {"password": "<SECRET>"}],
        "note": None,
    }


def test_normal_strings_are_not_over_redacted():
    assert redact_sensitive_data("report.pdf") == "report.pdf"
    assert redact_sensitive_data("step 3 of 5") == "step 3 of 5"
    assert redact_sensitive_data({"filename": "report.pdf"}) == {"filename": "report.pdf"}


def test_digits_glued_to_an_identifier_are_not_treated_as_a_card():
    assert redact_sensitive_data("invoice_2024001234567890") == "invoice_2024001234567890"
