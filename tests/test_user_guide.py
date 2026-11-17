def test_contextual_user_guide_is_available_on_core_pages(client):
    for path, page_key in (
        ("/", "dashboard"),
        ("/partners", "partners"),
        ("/partner-reconciliation", "partner_reconciliation"),
        ("/psmf", "psmf"),
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert 'data-page-guide-open' in response.text
        assert 'user_guide.js' in response.text
        assert f'data-page="{page_key}"' in response.text


def test_contextual_user_guide_asset_is_served(client):
    response = client.get("/static/user_guide.js")
    assert response.status_code == 200
    assert 'partner_reconciliation' in response.text
    assert 'pv-guide-popover' in response.text
