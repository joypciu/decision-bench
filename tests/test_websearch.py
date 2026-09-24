from decision_bench.domain import ProviderConfig
from decision_bench.websearch import fetch_url, is_public_http_url, parse_duckduckgo_html, web_search


def test_web_search_survives_a_source_failure():
    def fake_get(url, params=None, json=None, headers=None):
        del params, json, headers
        if "wikipedia" in url:
            raise RuntimeError("403")
        return {
            "AbstractText": "PyYAML load is unsafe before 5.4.",
            "AbstractURL": "https://example.com/pyyaml",
            "Heading": "PyYAML",
            "RelatedTopics": [],
        }

    result = web_search("PyYAML 5.3.1", [], get=fake_get)
    assert result["results"][0]["title"] == "PyYAML"
    assert "error" not in result


def test_duckduckgo_html_parser_reads_result_links():
    html = '''
    <a class="result__a" href="https://example.com/pyyaml">PyYAML advisory</a>
    <td class="result__snippet">Unsafe load before 5.4.</td>
    '''
    results = parse_duckduckgo_html(html)
    assert results[0]["title"] == "PyYAML advisory"
    assert results[0]["url"] == "https://example.com/pyyaml"
    assert "Unsafe load" in results[0]["snippet"]


def test_web_search_reads_wikipedia_results():
    def fake_get(url, params=None, json=None, headers=None):
        del params, json, headers
        if "wikipedia" in url:
            return {"query": {"search": [{"title": "SQLite", "snippet": "A database engine."}]}}
        return {}

    result = web_search("sqlite", [], get=fake_get)
    assert result["results"][0]["title"] == "SQLite"
    assert result["results"][0]["source"] == "wikipedia"


def test_fetch_url_refuses_local_addresses():
    assert fetch_url("http://127.0.0.1/secret")["error"]
    assert fetch_url("http://localhost/secret")["error"]
    assert is_public_http_url("http://127.0.0.1/secret") is False


def test_tavily_results_are_included_when_configured():
    config = ProviderConfig(
        name="tavily",
        kind="search",
        base_url="https://api.tavily.com",
        api_key="secret",
        default_model="search",
        enabled=True,
        builtin=False,
        created_at="",
    )

    def fake_get(url, params=None, json=None, headers=None):
        del params, headers
        if "tavily" in url:
            assert json["api_key"] == "secret"
            return {"results": [{"title": "Docs", "url": "https://example.com/docs", "content": "Notes"}]}
        return {}

    result = web_search("docs", [config], get=fake_get)
    assert result["results"][0]["source"] == "tavily"
    assert "secret" not in str(result)
