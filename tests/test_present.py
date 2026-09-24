from decision_bench.present import decision_of, provider_hint, summary_of


def test_decision_and_summary_read_the_contract_fields():
    output = {"verdict": "block", "summary": "Auth check removed.", "risks": []}
    assert decision_of(output) == "block"
    assert summary_of(output) == "Auth check removed."
    assert decision_of({"severity": "sev1"}) == "sev1"
    assert decision_of(None) is None


def test_provider_hint_only_for_backpressure():
    assert provider_hint("HTTP 429: You exceeded your current quota")
    assert provider_hint("HTTP 503: This model is currently experiencing high demand")
    assert provider_hint("Output must be a JSON object.") is None
    assert provider_hint(None) is None
